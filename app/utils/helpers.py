import secrets
import random
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session


# Predefined collaboration colors (visually distinct)
COLLAB_COLORS = [
    "#6366f1",  # indigo
    "#ec4899",  # pink
    "#f59e0b",  # amber
    "#10b981",  # emerald
    "#3b82f6",  # blue
    "#8b5cf6",  # violet
    "#ef4444",  # red
    "#14b8a6",  # teal
    "#f97316",  # orange
    "#06b6d4",  # cyan
]


def generate_share_token(length: int = 32) -> str:
    """Generate a cryptographically secure random token for share links."""
    return secrets.token_urlsafe(length)


def get_random_color() -> str:
    """Return a random color from the collaboration palette."""
    return random.choice(COLLAB_COLORS)


def get_content_preview(content: str, max_length: int = 150) -> str:
    """Return a short preview snippet of document content."""
    if not content:
        return ""
    # Strip any HTML tags for plain preview
    import re
    text = re.sub(r"<[^>]+>", " ", content)
    text = " ".join(text.split())  # collapse whitespace
    if len(text) > max_length:
        return text[:max_length].rsplit(" ", 1)[0] + "…"
    return text


def log_activity(
    db: Session,
    document_id,
    user_id,
    action: str,
    details: Optional[dict] = None
):
    """Helper to insert an Activity record."""
    from ..models import Activity
    activity = Activity(
        document_id=document_id,
        user_id=user_id,
        action=action,
        details=details or {},
        timestamp=datetime.utcnow(),
    )
    db.add(activity)
    # Caller is responsible for db.commit()
