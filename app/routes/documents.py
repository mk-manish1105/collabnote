from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from typing import List, Optional
from uuid import UUID
from datetime import datetime

from ..database import get_db
from ..models import (
    User, Document, DocumentCollaborator, DocumentVersion,
    Activity, RoleEnum
)
from ..schemas import (
    DocumentCreate, DocumentUpdate, DocumentResponse, DocumentListResponse,
    CollaboratorAdd, CollaboratorResponse, CollaboratorUpdate,
    VersionResponse, SearchResponse
)
from ..auth.jwt_handler import get_current_user
from ..utils.helpers import get_content_preview, log_activity

router = APIRouter(prefix="/api/documents", tags=["Documents"])


# ─────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────

def _check_access(document_id: UUID, user: User, db: Session, required_role: str = "viewer") -> Document:
    """
    Ensure the user can access the document.
    required_role: 'viewer' | 'editor' | 'owner'
    Returns the Document if access is granted, raises HTTPException otherwise.
    """
    db.expire_all()
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Owner always has full access
    if document.owner_id == user.id:
        return document

    if required_role == "owner":
        raise HTTPException(status_code=403, detail="Only the owner can perform this action")

    collaborator = db.query(DocumentCollaborator).filter(
        DocumentCollaborator.document_id == document_id,
        DocumentCollaborator.user_id == user.id
    ).first()

    if not collaborator:
        # Check if document is public (viewer access only)
        if document.is_public and required_role == "viewer":
            return document
        raise HTTPException(status_code=403, detail="Access denied")

    if required_role == "editor" and collaborator.role == RoleEnum.VIEWER:
        raise HTTPException(status_code=403, detail="Editor access required")

    return document


def _get_user_role(document: Document, user: User, db: Session) -> str:
    if document.owner_id == user.id:
        return "owner"
    collab = db.query(DocumentCollaborator).filter(
        DocumentCollaborator.document_id == document.id,
        DocumentCollaborator.user_id == user.id
    ).first()
    return collab.role.value if collab else "none"


def _save_version(document: Document, user: User, db: Session, summary: Optional[str] = None):
    """Save a new version snapshot before overwriting content."""
    version = DocumentVersion(
        document_id=document.id,
        content=document.content,
        title=document.title,
        created_by=user.id,
        version_number=document.current_version,
        change_summary=summary,
    )
    db.add(version)

    # Prune old versions if exceeding max (keep newest MAX-1 + new one)
    from ..config import settings
    versions = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.saved_at.desc())
        .all()
    )
    if len(versions) >= settings.MAX_VERSIONS_PER_DOCUMENT:
        # Delete oldest
        for v in versions[settings.MAX_VERSIONS_PER_DOCUMENT - 1:]:
            db.delete(v)


# ─────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────

@router.post("/", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def create_document(
    doc: DocumentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new document owned by the current user."""
    new_doc = Document(
        title=doc.title,
        content=doc.content or "",
        owner_id=current_user.id,
        is_rich_text=doc.is_rich_text,
        tags=doc.tags or [],
    )
    db.add(new_doc)
    db.commit()
    db.refresh(new_doc)

    # Initial version
    initial_version = DocumentVersion(
        document_id=new_doc.id,
        content=new_doc.content,
        title=new_doc.title,
        created_by=current_user.id,
        version_number=1,
        change_summary="Initial version",
    )
    db.add(initial_version)

    log_activity(db, new_doc.id, current_user.id, "created", {"title": new_doc.title})
    db.commit()

    return new_doc


@router.get("/", response_model=List[DocumentListResponse])
def list_documents(
    tag: Optional[str] = Query(None, description="Filter by tag"),
    search: Optional[str] = Query(None, description="Search in title"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all documents accessible to the current user (owned + collaborated)."""

    # Owned documents
    owned_q = db.query(Document).filter(Document.owner_id == current_user.id)

    # Collaborated documents
    collab_ids = [
        c.document_id for c in
        db.query(DocumentCollaborator)
        .filter(DocumentCollaborator.user_id == current_user.id)
        .all()
    ]
    collab_q = db.query(Document).filter(Document.id.in_(collab_ids))

    # Apply filters
    if tag:
        owned_q = owned_q.filter(Document.tags.any(tag))
        collab_q = collab_q.filter(Document.tags.any(tag))
    if search:
        owned_q = owned_q.filter(Document.title.ilike(f"%{search}%"))
        collab_q = collab_q.filter(Document.title.ilike(f"%{search}%"))

    owned_docs = owned_q.order_by(Document.updated_at.desc()).all()
    collab_docs = collab_q.order_by(Document.updated_at.desc()).all()

    all_docs = list({doc.id: doc for doc in owned_docs + collab_docs}.values())
    all_docs.sort(key=lambda d: d.updated_at, reverse=True)

    result = []
    for doc in all_docs:
        collab_count = db.query(DocumentCollaborator).filter(
            DocumentCollaborator.document_id == doc.id
        ).count()
        owner = db.query(User).filter(User.id == doc.owner_id).first()
        role = _get_user_role(doc, current_user, db)

        result.append(DocumentListResponse(
            id=doc.id,
            title=doc.title,
            content=get_content_preview(doc.content),
            owner_id=doc.owner_id,
            owner_username=owner.username if owner else None,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
            tags=doc.tags or [],
            is_public=doc.is_public,
            current_version=doc.current_version,
            collaborator_count=collab_count,
            user_role=role,
        ))

    return result


@router.get("/search", response_model=SearchResponse)
def search_documents(
    q: str = Query(..., min_length=1),
    tag: Optional[str] = Query(None),
    owner_only: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Full-text search across title and content of accessible documents."""
    base = db.query(Document).filter(
        Document.owner_id == current_user.id
        if owner_only else
        or_(
            Document.owner_id == current_user.id,
            Document.id.in_(
                db.query(DocumentCollaborator.document_id)
                .filter(DocumentCollaborator.user_id == current_user.id)
            )
        )
    )

    base = base.filter(
        or_(
            Document.title.ilike(f"%{q}%"),
            Document.content.ilike(f"%{q}%"),
        )
    )

    if tag:
        base = base.filter(Document.tags.any(tag))

    docs = base.order_by(Document.updated_at.desc()).all()

    result = []
    for doc in docs:
        owner = db.query(User).filter(User.id == doc.owner_id).first()
        role = _get_user_role(doc, current_user, db)
        result.append(DocumentListResponse(
            id=doc.id,
            title=doc.title,
            content=get_content_preview(doc.content),
            owner_id=doc.owner_id,
            owner_username=owner.username if owner else None,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
            tags=doc.tags or [],
            is_public=doc.is_public,
            current_version=doc.current_version,
            collaborator_count=0,
            user_role=role,
        ))

    return SearchResponse(documents=result, total_count=len(result))


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return _check_access(document_id, current_user, db)


@router.put("/{document_id}", response_model=DocumentResponse)
def update_document(
    document_id: UUID,
    doc_update: DocumentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update document content / title / settings."""
    document = _check_access(document_id, current_user, db, required_role="editor")

    content_changed = (
        doc_update.content is not None and doc_update.content != document.content
    )

    if content_changed:
        _save_version(document, current_user, db)
        document.content = doc_update.content
        document.current_version += 1

    if doc_update.title is not None:
        document.title = doc_update.title
    if doc_update.tags is not None:
        document.tags = doc_update.tags
    if doc_update.is_public is not None:
        document.is_public = doc_update.is_public
    if doc_update.allow_comments is not None:
        document.allow_comments = doc_update.allow_comments
    if doc_update.allow_chat is not None:
        document.allow_chat = doc_update.allow_chat

    document.updated_at = datetime.utcnow()

    if content_changed:
        log_activity(db, document.id, current_user.id, "edited", {"title": document.title})

    db.commit()
    db.refresh(document)
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a document (owner only)."""
    document = _check_access(document_id, current_user, db, required_role="owner")
    db.delete(document)
    db.commit()


# ─────────────────────────────────────────────
# Collaborators
# ─────────────────────────────────────────────

@router.post("/{document_id}/collaborators", response_model=CollaboratorResponse)
def add_collaborator(
    document_id: UUID,
    collaborator: CollaboratorAdd,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Add a collaborator (owner only)."""
    _check_access(document_id, current_user, db, required_role="owner")

    user_to_add = db.query(User).filter(User.username == collaborator.username).first()
    if not user_to_add:
        raise HTTPException(status_code=404, detail="User not found")

    if user_to_add.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot add yourself as collaborator")

    existing = db.query(DocumentCollaborator).filter(
        DocumentCollaborator.document_id == document_id,
        DocumentCollaborator.user_id == user_to_add.id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="User is already a collaborator")

    new_collab = DocumentCollaborator(
        document_id=document_id,
        user_id=user_to_add.id,
        role=RoleEnum(collaborator.role)
    )
    db.add(new_collab)
    log_activity(db, document_id, current_user.id, "collaborator_added",
                 {"username": user_to_add.username, "role": collaborator.role})
    db.commit()
    db.refresh(new_collab)

    return CollaboratorResponse(
        id=new_collab.id,
        user_id=user_to_add.id,
        username=user_to_add.username,
        full_name=user_to_add.full_name,
        avatar_color=user_to_add.avatar_color,
        role=new_collab.role.value,
        added_at=new_collab.added_at,
    )


@router.get("/{document_id}/collaborators", response_model=List[CollaboratorResponse])
def list_collaborators(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _check_access(document_id, current_user, db)
    collabs = db.query(DocumentCollaborator).filter(
        DocumentCollaborator.document_id == document_id
    ).all()

    result = []
    for c in collabs:
        user = db.query(User).filter(User.id == c.user_id).first()
        result.append(CollaboratorResponse(
            id=c.id,
            user_id=c.user_id,
            username=user.username,
            full_name=user.full_name,
            avatar_color=user.avatar_color,
            role=c.role.value,
            added_at=c.added_at,
        ))
    return result


@router.put("/{document_id}/collaborators/{collaborator_id}", response_model=CollaboratorResponse)
def update_collaborator_role(
    document_id: UUID,
    collaborator_id: UUID,
    update: CollaboratorUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Change a collaborator's role (owner only)."""
    _check_access(document_id, current_user, db, required_role="owner")

    collab = db.query(DocumentCollaborator).filter(
        DocumentCollaborator.id == collaborator_id,
        DocumentCollaborator.document_id == document_id
    ).first()
    if not collab:
        raise HTTPException(status_code=404, detail="Collaborator not found")

    collab.role = RoleEnum(update.role)
    db.commit()
    db.refresh(collab)

    user = db.query(User).filter(User.id == collab.user_id).first()
    return CollaboratorResponse(
        id=collab.id,
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        avatar_color=user.avatar_color,
        role=collab.role.value,
        added_at=collab.added_at,
    )


@router.delete("/{document_id}/collaborators/{collaborator_id}", status_code=204)
def remove_collaborator(
    document_id: UUID,
    collaborator_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Remove a collaborator (owner only)."""
    _check_access(document_id, current_user, db, required_role="owner")

    collab = db.query(DocumentCollaborator).filter(
        DocumentCollaborator.id == collaborator_id,
        DocumentCollaborator.document_id == document_id
    ).first()
    if not collab:
        raise HTTPException(status_code=404, detail="Collaborator not found")

    db.delete(collab)
    log_activity(db, document_id, current_user.id, "collaborator_removed", {})
    db.commit()


# ─────────────────────────────────────────────
# Version History
# ─────────────────────────────────────────────

@router.get("/{document_id}/versions", response_model=List[VersionResponse])
def list_versions(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _check_access(document_id, current_user, db)
    versions = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.saved_at.desc())
        .all()
    )

    result = []
    for v in versions:
        author = db.query(User).filter(User.id == v.created_by).first() if v.created_by else None
        result.append(VersionResponse(
            id=v.id,
            document_id=v.document_id,
            content=v.content,
            title=v.title,
            saved_at=v.saved_at,
            version_number=v.version_number,
            change_summary=v.change_summary,
            author_username=author.username if author else None,
        ))
    return result


@router.post("/{document_id}/versions/{version_id}/restore", response_model=DocumentResponse)
def restore_version(
    document_id: UUID,
    version_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Restore document to a previous version."""
    document = _check_access(document_id, current_user, db, required_role="editor")

    version = db.query(DocumentVersion).filter(
        DocumentVersion.id == version_id,
        DocumentVersion.document_id == document_id
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Save current state before restoring
    _save_version(document, current_user, db, summary=f"Auto-saved before restoring v{version.version_number}")

    document.content = version.content
    document.current_version += 1
    document.updated_at = datetime.utcnow()

    log_activity(db, document_id, current_user.id, "restored",
                 {"restored_version": version.version_number})
    db.commit()
    db.refresh(document)
    return document


# ─────────────────────────────────────────────
# Activity
# ─────────────────────────────────────────────

@router.get("/{document_id}/activity")
def get_activity(
    document_id: UUID,
    limit: int = Query(20, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _check_access(document_id, current_user, db)
    from ..models import Activity
    activities = (
        db.query(Activity)
        .filter(Activity.document_id == document_id)
        .order_by(Activity.timestamp.desc())
        .limit(limit)
        .all()
    )
    result = []
    for a in activities:
        user = db.query(User).filter(User.id == a.user_id).first()
        result.append({
            "id": str(a.id),
            "action": a.action,
            "details": a.details,
            "timestamp": a.timestamp.isoformat(),
            "username": user.username if user else "Unknown",
            "avatar_color": user.avatar_color if user else "#6366f1",
        })
    return result
