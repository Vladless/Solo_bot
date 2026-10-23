import uuid

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB

from ._base import Base, DictLikeMixin


class TicketDelivery(DictLikeMixin, Base):
    __tablename__ = "ticket_deliveries"
    __table_args__ = (
        CheckConstraint("status IN ('pending','running','retry','done','failed')", name="ck_ticket_delivery_status"),
        Index("ix_ticket_delivery_due", "kind", "status", "next_retry_at", "lease_until"),
        Index("ix_ticket_delivery_cleanup", "kind", "status", "updated_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ticket_id = Column(String(36), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=True, index=True)
    ticket_message_id = Column(
        String(36), ForeignKey("ticket_messages.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind = Column(String(32), nullable=False)
    payload = Column(JSONB, nullable=False, default=dict, server_default=text("'{}'"))
    dedupe_key = Column(String(255), nullable=True, unique=True)
    status = Column(String(16), nullable=False, default="pending", server_default=text("'pending'"))
    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    next_retry_at = Column(
        DateTime(timezone=True), nullable=True, default=lambda: datetime.now(UTC), server_default=text("now()")
    )
    lease_token = Column(String(32), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), server_default=text("now()")
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("now()"),
        onupdate=lambda: datetime.now(UTC),
    )
