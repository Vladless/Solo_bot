from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB

from ._base import Base, DictLikeMixin


class AuditEvent(DictLikeMixin, Base):
    """События аудита (флоу пользователя)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_tg_created", "actor_tg_id", "created_at"),
        Index("ix_audit_events_identity_created", "actor_identity_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(64), nullable=False)
    channel = Column(String(32), nullable=False)
    actor_identity_id = Column(
        String(36),
        ForeignKey("identities.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    actor_tg_id = Column(BigInteger, nullable=True)
    path_or_handler = Column(String(255), nullable=False)
    entity_type = Column(String(64), nullable=True)
    entity_id = Column(String(255), nullable=True, index=True)
    result = Column(String(32), nullable=False, server_default=sql_text("'success'"))
    reason = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=True)
    request_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
