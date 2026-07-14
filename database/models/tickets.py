import uuid

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    Text,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB

from ._base import Base, DictLikeMixin


class Ticket(DictLikeMixin, Base):
    __tablename__ = "tickets"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    identity_id = Column(String(36), ForeignKey("identities.id", ondelete="CASCADE"), nullable=False, index=True)
    subject = Column(String(255), nullable=True)
    category = Column(String(64), nullable=True, index=True)
    priority = Column(String(16), nullable=False, server_default=sql_text("'normal'"))
    status = Column(String(16), nullable=False, server_default=sql_text("'open'"), index=True)
    source = Column(String(16), nullable=False, server_default=sql_text("'web'"))
    assigned_agent_tg_id = Column(BigInteger, nullable=True, index=True)
    rating = Column(SmallInteger, nullable=True)
    tags = Column(JSONB, nullable=True)
    ref_type = Column(String(16), nullable=True)
    ref_id = Column(String(64), nullable=True)
    topic_id = Column(BigInteger, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=sql_text("now()"))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=sql_text("now()"))
    last_message_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=sql_text("now()"), index=True
    )


class TicketMessage(DictLikeMixin, Base):
    __tablename__ = "ticket_messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ticket_id = Column(String(36), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    author = Column(String(16), nullable=False)
    agent_tg_id = Column(BigInteger, nullable=True)
    body = Column(Text, nullable=False, server_default=sql_text("''"))
    attachments = Column(JSONB, nullable=True)
    read_by_client = Column(Boolean, nullable=False, server_default=sql_text("false"))
    read_by_agent = Column(Boolean, nullable=False, server_default=sql_text("false"))
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=sql_text("now()"), index=True
    )
