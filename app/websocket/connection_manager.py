from fastapi import WebSocket
from typing import Dict, List, Optional, Set
from datetime import datetime
import asyncio
import json


class UserConnection:
    """Represents a single user's WebSocket connection in a document room."""

    def __init__(
        self,
        websocket: WebSocket,
        user_id: str,
        username: str,
        avatar_color: str,
        role: str,
    ):
        self.websocket = websocket
        self.user_id = user_id
        self.username = username
        self.avatar_color = avatar_color
        self.role = role
        self.connected_at = datetime.utcnow()
        self.cursor_position: Optional[int] = None
        self.selection_start: Optional[int] = None
        self.selection_end: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "avatar_color": self.avatar_color,
            "role": self.role,
            "cursor_position": self.cursor_position,
            "connected_at": self.connected_at.isoformat(),
        }


class DocumentRoom:
    """
    Manages all WebSocket connections for a single document.
    Tracks users, their cursors, and document version state.
    """

    def __init__(self, document_id: str):
        self.document_id = document_id
        # websocket → UserConnection
        self.connections: Dict[WebSocket, UserConnection] = {}
        # document version for OT
        self.version: int = 0
        # operation log for transform (last N ops kept in memory)
        self.operation_history: List[dict] = []
        self.MAX_HISTORY = 200

    def add(self, ws: WebSocket, conn: UserConnection):
        self.connections[ws] = conn

    def remove(self, ws: WebSocket) -> Optional[UserConnection]:
        return self.connections.pop(ws, None)

    def get_conn(self, ws: WebSocket) -> Optional[UserConnection]:
        return self.connections.get(ws)

    def get_active_users(self) -> List[dict]:
        return [c.to_dict() for c in self.connections.values()]

    def user_count(self) -> int:
        return len(self.connections)

    def push_operation(self, op: dict):
        self.operation_history.append(op)
        if len(self.operation_history) > self.MAX_HISTORY:
            self.operation_history = self.operation_history[-self.MAX_HISTORY:]

    def get_ops_since(self, version: int) -> List[dict]:
        """Return all ops that happened after the given version."""
        return [op for op in self.operation_history if op.get("version", 0) > version]

    async def broadcast(
        self,
        message: dict,
        exclude: Optional[WebSocket] = None,
        only: Optional[WebSocket] = None,
    ):
        """Send a message to all (or specific) connections in this room."""
        text = json.dumps(message, default=str)
        dead: List[WebSocket] = []

        targets = (
            [only] if only
            else [ws for ws in self.connections if ws != exclude]
        )

        for ws in targets:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)

        # Clean up dead connections
        for ws in dead:
            self.connections.pop(ws, None)


class ConnectionManager:
    """
    Top-level manager: maps document_id → DocumentRoom.
    Single instance shared across all WebSocket handlers.
    """

    def __init__(self):
        self.rooms: Dict[str, DocumentRoom] = {}

    def _get_or_create_room(self, document_id: str) -> DocumentRoom:
        if document_id not in self.rooms:
            self.rooms[document_id] = DocumentRoom(document_id)
        return self.rooms[document_id]

    async def connect(
        self,
        websocket: WebSocket,
        document_id: str,
        user_id: str,
        username: str,
        avatar_color: str,
        role: str,
    ) -> DocumentRoom:
        await websocket.accept()
        room = self._get_or_create_room(document_id)
        conn = UserConnection(
            websocket=websocket,
            user_id=user_id,
            username=username,
            avatar_color=avatar_color,
            role=role,
        )
        room.add(websocket, conn)
        return room

    def disconnect(self, websocket: WebSocket, document_id: str) -> Optional[UserConnection]:
        room = self.rooms.get(document_id)
        if not room:
            return None
        conn = room.remove(websocket)
        # Garbage-collect empty rooms
        if room.user_count() == 0:
            del self.rooms[document_id]
        return conn

    def get_room(self, document_id: str) -> Optional[DocumentRoom]:
        return self.rooms.get(document_id)

    async def send_personal(self, message: dict, websocket: WebSocket):
        try:
            await websocket.send_text(json.dumps(message, default=str))
        except Exception:
            pass

    async def broadcast_to_document(
        self,
        document_id: str,
        message: dict,
        exclude: Optional[WebSocket] = None,
    ):
        room = self.rooms.get(document_id)
        if room:
            await room.broadcast(message, exclude=exclude)

    def get_active_users(self, document_id: str) -> List[dict]:
        room = self.rooms.get(document_id)
        return room.get_active_users() if room else []

    def get_document_version(self, document_id: str) -> int:
        room = self.rooms.get(document_id)
        return room.version if room else 0

    def total_connections(self) -> int:
        return sum(r.user_count() for r in self.rooms.values())


# Singleton instance
manager = ConnectionManager()
