"""
WebSocket Document Handler
==========================

Message types accepted from clients:
  operation       – OT text edit (insert / delete / replace / full_replace)
  cursor          – cursor position update
  selection       – text selection range
  typing          – typing indicator (is_typing: bool)
  chat            – in-document chat message
  ping            – keep-alive ping

Message types sent to clients:
  initial_state   – sent on connect: content, version, active users, chat history
  operation       – broadcast of a transformed op from another user
  operation_ack   – acknowledgement to the sender (version)
  operation_reject – sent when op cannot be applied (with reason)
  cursor          – another user's cursor position
  selection       – another user's text selection
  typing          – another user's typing indicator
  user_joined     – new user connected
  user_left       – user disconnected
  active_users    – updated active users list
  chat            – new chat message broadcast
  error           – general error message
  pong            – response to ping
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query
from sqlalchemy.orm import Session
from uuid import UUID
import json
from datetime import datetime, timezone
from typing import Optional
from ..models import ShareLink

from ..database import get_db
from ..models import Document, DocumentCollaborator, User, DocumentOperation, ChatMessage, RoleEnum
from .connection_manager import manager
from .ot_helper import apply_operation, transform_against_history
from ..auth.jwt_handler import get_user_from_token_ws

router = APIRouter(tags=["WebSocket"])


async def _authenticate(token: str, db: Session) -> Optional[User]:
    return await get_user_from_token_ws(token, db)

async def _check_document_access(document_id: str, user: User, db: Session, share_token: str = None):
    """
    Returns (document, role_enum) or (None, None) if no access.
    Checks in order: owner → collaborator → share link → public.
    """

    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        return None, None

    document = db.query(Document).filter(Document.id == doc_uuid).first()
    if not document:
        return None, None

    # 1. Owner always has full access
    if document.owner_id == user.id:
        return document, RoleEnum.EDITOR

    # 2. Direct collaborator
    collab = db.query(DocumentCollaborator).filter(
        DocumentCollaborator.document_id == doc_uuid,
        DocumentCollaborator.user_id == user.id,
    ).first()
    if collab:
        return document, collab.role

    # 3. Valid share link — check any active link for this document
    #    that hasn't expired and hasn't exceeded max uses
    active_links = db.query(ShareLink).filter(
        ShareLink.document_id == doc_uuid,
        ShareLink.is_active == True,
    ).all()

    for link in active_links:
        # Check expiry
        if link.expires_at:
            now = datetime.now(timezone.utc)
            expires = link.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if now > expires:
                continue  # this link expired, try next

        # Check max uses
        if link.max_uses is not None and link.use_count >= link.max_uses:
            continue  # exhausted, try next

        # This link is valid — grant its permission
        return document, link.permission

    # 4. Public document — viewer only
    if document.is_public:
        return document, RoleEnum.VIEWER

    return None, None

def _save_op_to_db(doc_id: UUID, user_id: UUID, op: dict, version: int, db: Session):
    try:
        db_op = DocumentOperation(
            document_id=doc_id,
            user_id=user_id,
            operation_type=op.get("type") or op.get("operation_type"),
            position=op.get("position", 0),
            content=op.get("content"),
            length=op.get("length"),
            document_version=version,
        )
        db.add(db_op)
        db.commit()
    except Exception as e:
        print(f"[WS] Error saving operation to DB: {e}")
        db.rollback()


def _save_chat_to_db(doc_id: UUID, user_id: UUID, message: str, db: Session):
    try:
        chat = ChatMessage(
            document_id=doc_id,
            user_id=user_id,
            message=message,
        )
        db.add(chat)
        db.commit()
        db.refresh(chat)
        return chat
    except Exception as e:
        print(f"[WS] Error saving chat message: {e}")
        db.rollback()
        return None


def _get_recent_chat(doc_id: UUID, db: Session, limit: int = 50) -> list:
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.document_id == doc_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    result = []
    for msg in reversed(messages):
        user = db.query(User).filter(User.id == msg.user_id).first()
        result.append({
            "id": str(msg.id),
            "user_id": str(msg.user_id),
            "username": user.username if user else "Unknown",
            "avatar_color": user.avatar_color if user else "#6366f1",
            "message": msg.message,
            "created_at": msg.created_at.isoformat(),
        })
    return result


@router.websocket("/ws/documents/{document_id}")
async def websocket_document(
    websocket: WebSocket,
    document_id: str,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    # ── 1. Authenticate ──────────────────────────────────
    user = await _authenticate(token, db)
    if not user:
        await websocket.close(code=4001, reason="Authentication failed")
        return

    # ── 2. Check access ──────────────────────────────────
    document, user_role = await _check_document_access(document_id, user, db)
    if document is None:
        await websocket.close(code=4003, reason="Access denied or document not found")
        return

    can_edit = (user_role == RoleEnum.EDITOR)
    doc_uuid = document.id

    # ── 3. Connect to room ───────────────────────────────
    room = await manager.connect(
        websocket=websocket,
        document_id=document_id,
        user_id=str(user.id),
        username=user.username,
        avatar_color=user.avatar_color,
        role=user_role.value,
    )

    # Sync DB version → room version (on first user connecting)
    if room.user_count() == 1:
        room.version = document.current_version

    # ── 4. Send initial state ────────────────────────────
    chat_history = _get_recent_chat(doc_uuid, db)
    active_users = room.get_active_users()

    await manager.send_personal(
        {
            "type": "initial_state",
            "content": document.content or "",
            "version": room.version,
            "can_edit": can_edit,
            "user_role": user_role.value,
            "active_users": active_users,
            "chat_history": chat_history,
            "document": {
                "id": str(document.id),
                "title": document.title,
                "allow_comments": document.allow_comments,
                "allow_chat": document.allow_chat,
            },
        },
        websocket,
    )

    # ── 5. Notify others ─────────────────────────────────
    await room.broadcast(
        {
            "type": "user_joined",
            "user_id": str(user.id),
            "username": user.username,
            "avatar_color": user.avatar_color,
            "role": user_role.value,
            "active_users": active_users,
        },
        exclude=websocket,
    )

    # ── 6. Message loop ──────────────────────────────────
    try:
        while True:
            raw = await websocket.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_personal(
                    {"type": "error", "message": "Invalid JSON"}, websocket
                )
                continue

            msg_type = msg.get("type")

            # ── operation ────────────────────────────────
            if msg_type == "operation":
                if not can_edit:
                    await manager.send_personal(
                        {"type": "error", "message": "You have view-only access"},
                        websocket,
                    )
                    continue

                op = msg.get("operation")
                client_version = msg.get("client_version", room.version)

                if not op or not op.get("type"):
                    await manager.send_personal(
                        {"type": "error", "message": "Malformed operation"}, websocket
                    )
                    continue

                # Refresh document from DB to get latest content
                db.refresh(document)

                try:
                    # Transform op if client is behind server version
                    if client_version < room.version:
                        op = transform_against_history(op, room.operation_history, client_version)

                    # Apply to document content
                    new_content = apply_operation(document.content or "", op)
                    document.content = new_content
                    document.updated_at = datetime.utcnow()
                    document.current_version += 1

                    room.version = document.current_version

                    # Store in room history for future transforms
                    room.push_operation({
                        "version": room.version,
                        "operation": op,
                        "user_id": str(user.id),
                    })

                    # Persist op + document to DB
                    _save_op_to_db(doc_uuid, user.id, op, room.version, db)
                    db.commit()

                    # ACK to sender
                    await manager.send_personal(
                        {"type": "operation_ack", "version": room.version},
                        websocket,
                    )

                    # Broadcast transformed op to all other users
                    await room.broadcast(
                        {
                            "type": "operation",
                            "operation": op,
                            "version": room.version,
                            "user_id": str(user.id),
                            "username": user.username,
                            "avatar_color": user.avatar_color,
                            "timestamp": datetime.utcnow().isoformat(),
                        },
                        exclude=websocket,
                    )

                except Exception as e:
                    db.rollback()
                    print(f"[WS] OT error: {e}")
                    await manager.send_personal(
                        {
                            "type": "operation_reject",
                            "reason": "Server error applying operation",
                            "current_version": room.version,
                        },
                        websocket,
                    )

            # ── cursor ───────────────────────────────────
            elif msg_type == "cursor":
                conn = room.get_conn(websocket)
                if conn:
                    conn.cursor_position = msg.get("position")

                await room.broadcast(
                    {
                        "type": "cursor",
                        "user_id": str(user.id),
                        "username": user.username,
                        "avatar_color": user.avatar_color,
                        "position": msg.get("position"),
                    },
                    exclude=websocket,
                )

            # ── selection ────────────────────────────────
            elif msg_type == "selection":
                conn = room.get_conn(websocket)
                if conn:
                    conn.selection_start = msg.get("start")
                    conn.selection_end = msg.get("end")

                await room.broadcast(
                    {
                        "type": "selection",
                        "user_id": str(user.id),
                        "username": user.username,
                        "avatar_color": user.avatar_color,
                        "start": msg.get("start"),
                        "end": msg.get("end"),
                    },
                    exclude=websocket,
                )

            # ── typing indicator ─────────────────────────
            elif msg_type == "typing":
                await room.broadcast(
                    {
                        "type": "typing",
                        "user_id": str(user.id),
                        "username": user.username,
                        "avatar_color": user.avatar_color,
                        "is_typing": msg.get("is_typing", True),
                    },
                    exclude=websocket,
                )

            # ── chat message ─────────────────────────────
            elif msg_type == "chat":
                if not document.allow_chat:
                    await manager.send_personal(
                        {"type": "error", "message": "Chat is disabled for this document"},
                        websocket,
                    )
                    continue

                chat_text = (msg.get("message") or "").strip()
                if not chat_text or len(chat_text) > 1000:
                    await manager.send_personal(
                        {"type": "error", "message": "Message must be 1–1000 characters"},
                        websocket,
                    )
                    continue

                saved = _save_chat_to_db(doc_uuid, user.id, chat_text, db)
                chat_payload = {
                    "type": "chat",
                    "id": str(saved.id) if saved else "",
                    "user_id": str(user.id),
                    "username": user.username,
                    "avatar_color": user.avatar_color,
                    "message": chat_text,
                    "created_at": datetime.utcnow().isoformat(),
                }
                # Broadcast to ALL users in room including sender
                await room.broadcast(chat_payload)

            # ── title update ─────────────────────────────
            elif msg_type == "title_update":
                if not can_edit:
                    continue
                new_title = (msg.get("title") or "").strip()
                if new_title and len(new_title) <= 200:
                    document.title = new_title
                    document.updated_at = datetime.utcnow()
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
                    await room.broadcast(
                        {
                            "type": "title_update",
                            "title": new_title,
                            "user_id": str(user.id),
                            "username": user.username,
                        },
                        exclude=websocket,
                    )

            # ── ping / keep-alive ────────────────────────
            elif msg_type == "ping":
                await manager.send_personal({"type": "pong"}, websocket)

            else:
                await manager.send_personal(
                    {"type": "error", "message": f"Unknown message type: {msg_type!r}"},
                    websocket,
                )

    # ── 7. Disconnection ─────────────────────────────────
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS] Unexpected error for user {user.username}: {e}")
    finally:
        conn = manager.disconnect(websocket, document_id)
        remaining_users = manager.get_active_users(document_id)

        await manager.broadcast_to_document(
            document_id,
            {
                "type": "user_left",
                "user_id": str(user.id),
                "username": user.username,
                "active_users": remaining_users,
            },
        )
