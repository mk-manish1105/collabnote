from sqlalchemy import (
    Column, String, Text, ForeignKey, DateTime, Enum,
    Boolean, Integer, JSON, Index, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum
from .database import Base


# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────

class RoleEnum(str, enum.Enum):
    VIEWER = "viewer"
    EDITOR = "editor"


class ActivityAction(str, enum.Enum):
    CREATED = "created"
    EDITED = "edited"
    SHARED = "shared"
    COMMENTED = "commented"
    RESTORED = "restored"
    COLLABORATOR_ADDED = "collaborator_added"
    COLLABORATOR_REMOVED = "collaborator_removed"
    SHARE_LINK_CREATED = "share_link_created"


# ─────────────────────────────────────────────
# User
# ─────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=True)
    bio = Column(Text, nullable=True)
    avatar_color = Column(String(7), nullable=False, default="#6366f1")  # hex color
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)

    # Relationships
    owned_documents = relationship(
        "Document", back_populates="owner", cascade="all, delete-orphan"
    )
    collaborations = relationship(
        "DocumentCollaborator", back_populates="user", cascade="all, delete-orphan"
    )
    created_share_links = relationship(
        "ShareLink", back_populates="creator", cascade="all, delete-orphan"
    )
    comments = relationship(
        "DocumentComment", foreign_keys="DocumentComment.user_id",
        back_populates="user", cascade="all, delete-orphan"
    )
    chat_messages = relationship(
        "ChatMessage", back_populates="user", cascade="all, delete-orphan"
    )
    activities = relationship(
        "Activity", foreign_keys="Activity.user_id",
        back_populates="user", cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────────
# Document
# ─────────────────────────────────────────────

class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(200), nullable=False, index=True)
    content = Column(Text, default="")
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Features
    is_rich_text = Column(Boolean, default=False)
    tags = Column(ARRAY(String), default=[])
    is_public = Column(Boolean, default=False)
    allow_comments = Column(Boolean, default=True)
    allow_chat = Column(Boolean, default=True)

    # Version tracking (current version number)
    current_version = Column(Integer, default=1)

    # Relationships
    owner = relationship("User", back_populates="owned_documents")
    collaborators = relationship(
        "DocumentCollaborator", back_populates="document", cascade="all, delete-orphan"
    )
    versions = relationship(
        "DocumentVersion", back_populates="document", cascade="all, delete-orphan",
        order_by="DocumentVersion.version_number.desc()"
    )
    share_links = relationship(
        "ShareLink", back_populates="document", cascade="all, delete-orphan"
    )
    operations = relationship(
        "DocumentOperation", back_populates="document", cascade="all, delete-orphan"
    )
    comments = relationship(
        "DocumentComment", back_populates="document", cascade="all, delete-orphan"
    )
    chat_messages = relationship(
        "ChatMessage", back_populates="document", cascade="all, delete-orphan"
    )
    activities = relationship(
        "Activity", back_populates="document", cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────────
# Collaborator
# ─────────────────────────────────────────────

class DocumentCollaborator(Base):
    __tablename__ = "document_collaborators"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    role = Column(Enum(RoleEnum), nullable=False, default=RoleEnum.VIEWER)
    added_at = Column(DateTime, default=datetime.utcnow)

    # Unique constraint: one record per user per document
    __table_args__ = (
        UniqueConstraint("document_id", "user_id", name="uq_doc_collab"),
    )

    document = relationship("Document", back_populates="collaborators")
    user = relationship("User", back_populates="collaborations")


# ─────────────────────────────────────────────
# Version History
# ─────────────────────────────────────────────

class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    title = Column(String(200), nullable=True)  # snapshot of title at time of save
    saved_at = Column(DateTime, default=datetime.utcnow, index=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    version_number = Column(Integer, default=1)
    change_summary = Column(String(200), nullable=True)  # optional human-readable summary

    document = relationship("Document", back_populates="versions")
    author = relationship("User", foreign_keys=[created_by])


# ─────────────────────────────────────────────
# Share Links
# ─────────────────────────────────────────────

class ShareLink(Base):
    __tablename__ = "share_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True)
    token = Column(String(64), unique=True, nullable=False, index=True)

    permission = Column(Enum(RoleEnum), nullable=False, default=RoleEnum.VIEWER)
    password_hash = Column(String(255), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    max_uses = Column(Integer, nullable=True)
    use_count = Column(Integer, default=0)

    is_active = Column(Boolean, default=True, index=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_accessed_at = Column(DateTime, nullable=True)

    document = relationship("Document", back_populates="share_links")
    creator = relationship("User", back_populates="created_share_links")
    access_logs = relationship(
        "LinkAccessLog", back_populates="share_link", cascade="all, delete-orphan"
    )


class LinkAccessLog(Base):
    __tablename__ = "link_access_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    link_id = Column(UUID(as_uuid=True), ForeignKey("share_links.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    ip_address = Column(String(45))
    user_agent = Column(String(500))
    accessed_at = Column(DateTime, default=datetime.utcnow, index=True)
    action = Column(String(50))  # 'view', 'edit', 'denied', 'expired'

    share_link = relationship("ShareLink", back_populates="access_logs")


# ─────────────────────────────────────────────
# Operational Transform Operations Log
# ─────────────────────────────────────────────

class DocumentOperation(Base):
    __tablename__ = "document_operations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    operation_type = Column(String(20))  # 'insert', 'delete', 'replace'
    position = Column(Integer, nullable=False)
    content = Column(Text, nullable=True)
    length = Column(Integer, nullable=True)

    document_version = Column(Integer, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    document = relationship("Document", back_populates="operations")


# ─────────────────────────────────────────────
# Inline Comments
# ─────────────────────────────────────────────

class DocumentComment(Base):
    __tablename__ = "document_comments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    content = Column(Text, nullable=False)
    start_position = Column(Integer, nullable=False)
    end_position = Column(Integer, nullable=False)

    # Thread support
    parent_id = Column(UUID(as_uuid=True), ForeignKey("document_comments.id"), nullable=True)

    is_resolved = Column(Boolean, default=False)
    resolved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    document = relationship("Document", back_populates="comments")
    user = relationship("User", foreign_keys=[user_id], back_populates="comments")
    replies = relationship("DocumentComment", backref="parent", remote_side=[id])


# ─────────────────────────────────────────────
# In-Document Chat
# ─────────────────────────────────────────────

class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    document = relationship("Document", back_populates="chat_messages")
    user = relationship("User", back_populates="chat_messages")


# ─────────────────────────────────────────────
# Activity Log
# ─────────────────────────────────────────────

class Activity(Base):
    __tablename__ = "activities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    action = Column(String(50), nullable=False)
    details = Column(JSON, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    document = relationship("Document", back_populates="activities")
    user = relationship("User", foreign_keys=[user_id], back_populates="activities")
