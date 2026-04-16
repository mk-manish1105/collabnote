from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID
from datetime import datetime

from ..database import get_db
from ..models import User, Document, ShareLink, LinkAccessLog, DocumentCollaborator, RoleEnum
from ..schemas import (
    ShareLinkCreate, ShareLinkResponse,
    ShareLinkAccessRequest, ShareLinkAccessResponse
)
from ..auth.jwt_handler import get_current_user
from ..auth.hashing import hash_password, verify_password
from ..utils.helpers import generate_share_token, log_activity

router = APIRouter(prefix="/api/sharing", tags=["Sharing"])


def _build_share_link_response(link: ShareLink, request: Request) -> ShareLinkResponse:
    base_url = str(request.base_url).rstrip("/")
    return ShareLinkResponse(
        id=link.id,
        token=link.token,
        link_url=f"{base_url}/shared/{link.token}",
        permission=link.permission.value,
        expires_at=link.expires_at,
        max_uses=link.max_uses,
        use_count=link.use_count,
        is_active=link.is_active,
        created_at=link.created_at,
        created_by=link.created_by,
        has_password=link.password_hash is not None,
    )


@router.post("/documents/{document_id}/links", response_model=ShareLinkResponse)
def create_share_link(
    document_id: UUID,
    link_data: ShareLinkCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a shareable link for a document (owner only)."""
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can create share links")

    token = generate_share_token()
    password_hash = hash_password(link_data.password) if link_data.password else None

    link = ShareLink(
        document_id=document_id,
        token=token,
        permission=RoleEnum(link_data.permission),
        password_hash=password_hash,
        expires_at=link_data.expires_at,
        max_uses=link_data.max_uses,
        created_by=current_user.id,
    )
    db.add(link)
    log_activity(db, document_id, current_user.id, "share_link_created",
                 {"permission": link_data.permission})
    db.commit()
    db.refresh(link)

    return _build_share_link_response(link, request)


@router.get("/documents/{document_id}/links", response_model=List[ShareLinkResponse])
def list_share_links(
    document_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all share links for a document (owner only)."""
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can view share links")

    links = db.query(ShareLink).filter(
        ShareLink.document_id == document_id,
        ShareLink.is_active == True
    ).all()

    return [_build_share_link_response(l, request) for l in links]


@router.delete("/links/{token}", status_code=204)
def revoke_share_link(
    token: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Revoke (deactivate) a share link."""
    link = db.query(ShareLink).filter(ShareLink.token == token).first()
    if not link:
        raise HTTPException(status_code=404, detail="Share link not found")

    document = db.query(Document).filter(Document.id == link.document_id).first()
    if not document or document.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can revoke links")

    link.is_active = False
    db.commit()


@router.post("/access/{token}", response_model=ShareLinkAccessResponse)
def access_shared_document(
    token: str,
    access_data: ShareLinkAccessRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Access a document via share link.
    Returns document content and permissions.
    No authentication required — anyone with the token can access.
    """
    link = db.query(ShareLink).filter(
        ShareLink.token == token,
        ShareLink.is_active == True
    ).first()

    if not link:
        raise HTTPException(status_code=404, detail="Share link not found or has been revoked")

    # Check expiry
    if link.expires_at:
        from datetime import timezone
        now = datetime.now(timezone.utc)
        expires = link.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if now > expires:
            _log_access(db, link.id, None, request, "expired")
            raise HTTPException(status_code=410, detail="This share link has expired")

    # Check max uses
    if link.max_uses is not None and link.use_count >= link.max_uses:
        _log_access(db, link.id, None, request, "denied")
        raise HTTPException(status_code=410, detail="This share link has reached its maximum uses")

    # Check password
    if link.password_hash:
        if not access_data.password:
            raise HTTPException(status_code=401, detail="This link requires a password")
        if not verify_password(access_data.password, link.password_hash):
            _log_access(db, link.id, None, request, "denied")
            raise HTTPException(status_code=403, detail="Incorrect password")

    # All checks passed — increment usage
    link.use_count += 1
    link.last_accessed_at = datetime.utcnow()
    _log_access(db, link.id, None, request, "view")
    db.commit()

    document = db.query(Document).filter(Document.id == link.document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    can_edit = link.permission == RoleEnum.EDITOR

    return ShareLinkAccessResponse(
        document=document,
        permission=link.permission.value,
        can_edit=can_edit,
        link_id=link.id,
    )


@router.get("/info/{token}")
def get_share_link_info(token: str, db: Session = Depends(get_db)):
    """Get public info about a share link (no auth required)."""
    link = db.query(ShareLink).filter(
        ShareLink.token == token,
        ShareLink.is_active == True
    ).first()

    if not link:
        raise HTTPException(status_code=404, detail="Share link not found or revoked")

    document = db.query(Document).filter(Document.id == link.document_id).first()

    return {
        "document_title": document.title if document else "Unknown",
        "permission": link.permission.value,
        "has_password": link.password_hash is not None,
        "expires_at": link.expires_at.isoformat() if link.expires_at else None,
        "is_active": link.is_active,
    }


def _log_access(db, link_id, user_id, request: Request, action: str):
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    log = LinkAccessLog(
        link_id=link_id,
        user_id=user_id,
        ip_address=ip[:45],
        user_agent=ua[:500],
        action=action,
    )
    db.add(log)
