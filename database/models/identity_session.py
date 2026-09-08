import uuid

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)

from ._base import Base, DictLikeMixin


class IdentitySession(DictLikeMixin, Base):
    """Активная сессия identity. Один identity может иметь несколько сессий (devices)."""

    __tablename__ = "identity_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    identity_id = Column(
        String(36),
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash = Column(String(64), nullable=False, unique=True)
    device_label = Column(String(128), nullable=True)
    origin = Column(String(16), nullable=True)
    user_agent = Column(Text, nullable=True)
    ip = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_identity_sessions_identity_last_seen", "identity_id", "last_seen_at"),)
