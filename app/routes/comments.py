from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID
from datetime import datetime

from ..database import get_db
from ..models import User, Document, DocumentComment, DocumentCollaborator, RoleEnum
from ..schemas import CommentCreate, CommentResponse, CommentUpdate
from ..auth.jwt_handler import get_current_user

router = APIRouter(prefix="/api/comments", tags=["Comments"])


def _can_access(document_id: UUID, user: User, db: Session):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.allow_comments:
        raise HTTPException(status_code=403, detail="Comments are disabled on this document")
    if doc.owner_id == user.id:
        return doc
    collab = db.query(DocumentCollaborator).filter(
        DocumentCollaborator.document_id == document_id,
        DocumentCollaborator.user_id == user.id
    ).first()
    if not collab and not doc.is_public:
        raise HTTPException(status_code=403, detail="Access denied")
    return doc


def _build_response(comment: DocumentComment, db: Session) -> CommentResponse:
    user = db.query(User).filter(User.id == comment.user_id).first()
    replies_raw = db.query(DocumentComment).filter(
        DocumentComment.parent_id == comment.id,
        DocumentComment.parent_id != None
    ).order_by(DocumentComment.created_at).all()
    replies = [_build_response(r, db) for r in replies_raw]

    return CommentResponse(
        id=comment.id,
        document_id=comment.document_id,
        user_id=comment.user_id,
        username=user.username if user else "Unknown",
        avatar_color=user.avatar_color if user else "#6366f1",
        content=comment.content,
        start_position=comment.start_position,
        end_position=comment.end_position,
        parent_id=comment.parent_id,
        is_resolved=comment.is_resolved,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
        replies=replies,
    )


@router.post("/documents/{document_id}", response_model=CommentResponse, status_code=201)
def create_comment(
    document_id: UUID,
    data: CommentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _can_access(document_id, current_user, db)

    if data.parent_id:
        parent = db.query(DocumentComment).filter(DocumentComment.id == data.parent_id).first()
        if not parent or parent.document_id != document_id:
            raise HTTPException(status_code=404, detail="Parent comment not found")

    comment = DocumentComment(
        document_id=document_id,
        user_id=current_user.id,
        content=data.content,
        start_position=data.start_position,
        end_position=data.end_position,
        parent_id=data.parent_id,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _build_response(comment, db)


@router.get("/documents/{document_id}", response_model=List[CommentResponse])
def list_comments(
    document_id: UUID,
    include_resolved: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _can_access(document_id, current_user, db)

    q = db.query(DocumentComment).filter(
        DocumentComment.document_id == document_id,
        DocumentComment.parent_id == None  # top-level only; replies fetched recursively
    )
    if not include_resolved:
        q = q.filter(DocumentComment.is_resolved == False)

    comments = q.order_by(DocumentComment.created_at).all()
    return [_build_response(c, db) for c in comments]


@router.put("/{comment_id}", response_model=CommentResponse)
def update_comment(
    comment_id: UUID,
    data: CommentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    comment = db.query(DocumentComment).filter(DocumentComment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only edit your own comments")

    comment.content = data.content
    comment.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(comment)
    return _build_response(comment, db)


@router.post("/{comment_id}/resolve", response_model=CommentResponse)
def resolve_comment(
    comment_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    comment = db.query(DocumentComment).filter(DocumentComment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    doc = db.query(Document).filter(Document.id == comment.document_id).first()
    is_owner = doc and doc.owner_id == current_user.id
    is_commenter = comment.user_id == current_user.id

    if not is_owner and not is_commenter:
        raise HTTPException(status_code=403, detail="Only the document owner or comment author can resolve")

    comment.is_resolved = True
    comment.resolved_by = current_user.id
    comment.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(comment)
    return _build_response(comment, db)


@router.delete("/{comment_id}", status_code=204)
def delete_comment(
    comment_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    comment = db.query(DocumentComment).filter(DocumentComment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    doc = db.query(Document).filter(Document.id == comment.document_id).first()
    is_owner = doc and doc.owner_id == current_user.id

    if comment.user_id != current_user.id and not is_owner:
        raise HTTPException(status_code=403, detail="Permission denied")

    db.delete(comment)
    db.commit()
