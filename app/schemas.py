from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID


# ─────────────────────────────────────────────
# User Schemas
# ─────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=100)
    full_name: Optional[str] = Field(None, max_length=100)


class UserLogin(BaseModel):
    username: str
    password: str


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(None, max_length=100)
    bio: Optional[str] = Field(None, max_length=500)
    avatar_color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")


class UserResponse(BaseModel):
    id: UUID
    username: str
    email: str
    full_name: Optional[str]
    bio: Optional[str]
    avatar_color: str
    created_at: datetime
    last_seen: datetime

    model_config = {"from_attributes": True}


class UserPublicResponse(BaseModel):
    """Public profile - no email"""
    id: UUID
    username: str
    full_name: Optional[str]
    avatar_color: str

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class TokenData(BaseModel):
    user_id: Optional[str] = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6)


# ─────────────────────────────────────────────
# Document Schemas
# ─────────────────────────────────────────────

class DocumentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: Optional[str] = ""
    is_rich_text: Optional[bool] = False
    tags: Optional[List[str]] = []


class DocumentUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    content: Optional[str] = None
    tags: Optional[List[str]] = None
    is_public: Optional[bool] = None
    allow_comments: Optional[bool] = None
    allow_chat: Optional[bool] = None


class DocumentResponse(BaseModel):
    id: UUID
    title: str
    content: str
    owner_id: UUID
    created_at: datetime
    updated_at: datetime
    is_rich_text: bool
    tags: List[str]
    is_public: bool
    allow_comments: bool
    allow_chat: bool
    current_version: int

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    id: UUID
    title: str
    content: str   # snippet for preview
    owner_id: UUID
    owner_username: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    tags: List[str]
    is_public: bool
    current_version: int
    collaborator_count: Optional[int] = 0
    user_role: Optional[str] = "owner"  # 'owner', 'editor', 'viewer'

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────
# Collaborator Schemas
# ─────────────────────────────────────────────

class CollaboratorAdd(BaseModel):
    username: str
    role: str = Field(..., pattern="^(viewer|editor)$")


class CollaboratorResponse(BaseModel):
    id: UUID
    user_id: UUID
    username: str
    full_name: Optional[str]
    avatar_color: str
    role: str
    added_at: datetime


class CollaboratorUpdate(BaseModel):
    role: str = Field(..., pattern="^(viewer|editor)$")


# ─────────────────────────────────────────────
# Version Schemas
# ─────────────────────────────────────────────

class VersionResponse(BaseModel):
    id: UUID
    document_id: UUID
    content: str
    title: Optional[str]
    saved_at: datetime
    version_number: int
    change_summary: Optional[str]
    author_username: Optional[str] = None

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────
# Share Link Schemas
# ─────────────────────────────────────────────

class ShareLinkCreate(BaseModel):
    permission: str = Field(..., pattern="^(viewer|editor)$")
    password: Optional[str] = None
    expires_at: Optional[datetime] = None
    max_uses: Optional[int] = None

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, v):
        if v:
            from datetime import timezone
            now = datetime.now(timezone.utc)
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            if v < now:
                raise ValueError("Expiration date must be in the future")
        return v

    @field_validator("max_uses")
    @classmethod
    def validate_max_uses(cls, v):
        if v is not None and v < 1:
            raise ValueError("Max uses must be at least 1")
        return v


class ShareLinkResponse(BaseModel):
    id: UUID
    token: str
    link_url: str
    permission: str
    expires_at: Optional[datetime]
    max_uses: Optional[int]
    use_count: int
    is_active: bool
    created_at: datetime
    created_by: UUID
    has_password: bool

    model_config = {"from_attributes": True}


class ShareLinkAccessRequest(BaseModel):
    password: Optional[str] = None


class ShareLinkAccessResponse(BaseModel):
    document: DocumentResponse
    permission: str
    can_edit: bool
    link_id: UUID


# ─────────────────────────────────────────────
# Comment Schemas
# ─────────────────────────────────────────────

class CommentCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)
    start_position: int = Field(..., ge=0)
    end_position: int = Field(..., ge=0)
    parent_id: Optional[UUID] = None

    @field_validator("end_position")
    @classmethod
    def validate_positions(cls, v, info):
        if "start_position" in info.data and v < info.data["start_position"]:
            raise ValueError("end_position must be >= start_position")
        return v


class CommentResponse(BaseModel):
    id: UUID
    document_id: UUID
    user_id: UUID
    username: str
    avatar_color: str
    content: str
    start_position: int
    end_position: int
    parent_id: Optional[UUID]
    is_resolved: bool
    created_at: datetime
    updated_at: datetime
    replies: List["CommentResponse"] = []

    model_config = {"from_attributes": True}


class CommentUpdate(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)


# ─────────────────────────────────────────────
# Chat Schemas
# ─────────────────────────────────────────────

class ChatMessageResponse(BaseModel):
    id: UUID
    document_id: UUID
    user_id: UUID
    username: str
    avatar_color: str
    message: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────
# Activity Schemas
# ─────────────────────────────────────────────

class ActivityResponse(BaseModel):
    id: UUID
    document_id: UUID
    user_id: UUID
    username: str
    avatar_color: str
    action: str
    details: Optional[Dict[str, Any]]
    timestamp: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────
# Operation / OT Schemas
# ─────────────────────────────────────────────

class OperationCreate(BaseModel):
    operation_type: str = Field(..., pattern="^(insert|delete|replace)$")
    position: int = Field(..., ge=0)
    content: Optional[str] = None
    length: Optional[int] = None
    document_version: int = Field(..., ge=0)


# ─────────────────────────────────────────────
# Search Schemas
# ─────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    tags: Optional[List[str]] = []
    owner_only: Optional[bool] = False


class SearchResponse(BaseModel):
    documents: List[DocumentListResponse]
    total_count: int


# ─────────────────────────────────────────────
# WebSocket Message Schema
# ─────────────────────────────────────────────

class WebSocketMessage(BaseModel):
    type: str
    document_id: Optional[str] = None
    user_id: Optional[str] = None
    username: Optional[str] = None
    content: Optional[str] = None
    cursor_position: Optional[int] = None
    operation: Optional[Dict[str, Any]] = None
    message: Optional[str] = None  # for chat


# Allow forward references
CommentResponse.model_rebuild()
