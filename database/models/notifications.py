import uuid

from datetime import UTC, datetime

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


class Notification(DictLikeMixin, Base):
    __tablename__ = "notifications"

    tg_id = Column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=True, index=True)
    user_id = Column(BigInteger, nullable=False, primary_key=True)
    notification_type = Column(String, primary_key=True)
    last_notification_time = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class ScheduledBroadcast(DictLikeMixin, Base):
    __tablename__ = "scheduled_broadcasts"
    __table_args__ = (
        Index("ix_scheduled_broadcasts_status_time", "status", "scheduled_for"),
        Index("ix_scheduled_broadcasts_creator_time", "created_by_tg_id", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_by_user_id = Column(BigInteger, nullable=True, index=True)
    created_by_tg_id = Column(BigInteger, ForeignKey("users.tg_id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(String(32), nullable=False, server_default=sql_text("'scheduled'"), index=True)
    send_to = Column(String(32), nullable=False, index=True)
    channel = Column(String(32), nullable=False, server_default=sql_text("'both'"))
    cluster_name = Column(String, nullable=True)
    text = Column(Text, nullable=False)
    photo = Column(String, nullable=True)
    keyboard_json = Column(JSONB, nullable=True)
    scheduled_for = Column(DateTime(timezone=True), nullable=False, index=True)
    workers = Column(Integer, nullable=False, server_default=sql_text("5"))
    messages_per_second = Column(Integer, nullable=False, server_default=sql_text("35"))
    stats_json = Column(JSONB, nullable=True)
    error_text = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
