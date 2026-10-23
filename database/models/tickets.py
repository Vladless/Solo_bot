import uuid

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
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
    topic_header_message_id = Column(BigInteger, nullable=True)
    topic_chat_id = Column(BigInteger, nullable=True)
    topic_bot_id = Column(BigInteger, nullable=True)
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
    author_identity_id = Column(String(36), ForeignKey("identities.id", ondelete="SET NULL"), nullable=True)
    agent_tg_id = Column(BigInteger, nullable=True)
    body = Column(Text, nullable=False, server_default=sql_text("''"))
    attachments = Column(JSONB, nullable=True)
    revision = Column(Integer, nullable=False, default=0, server_default=sql_text("0"))
    edited_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    read_by_client = Column(Boolean, nullable=False, server_default=sql_text("false"))
    read_by_agent = Column(Boolean, nullable=False, server_default=sql_text("false"))
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=sql_text("now()"), index=True
    )


class TicketTelegramMessage(DictLikeMixin, Base):
    __tablename__ = "ticket_telegram_messages"
    __table_args__ = (
        UniqueConstraint("bot_id", "chat_id", "telegram_message_id", name="uq_ticket_telegram_message_address"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ticket_message_id = Column(
        String(36), ForeignKey("ticket_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bot_id = Column(BigInteger, nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    telegram_message_id = Column(BigInteger, nullable=False)
    topic_id = Column(BigInteger, nullable=True)
    source = Column(Boolean, nullable=False, default=False, server_default=sql_text("false"))
    actor_tg_id = Column(BigInteger, nullable=True)
    media_kind = Column(String(16), nullable=False, default="text", server_default=sql_text("'text'"))
    part_index = Column(Integer, nullable=False, default=-1, server_default=sql_text("-1"))
    file_unique_id = Column(String(255), nullable=True)
    source_edited_at = Column(DateTime(timezone=True), nullable=True)
    applied_revision = Column(Integer, nullable=False, default=0, server_default=sql_text("0"))
    deleted = Column(Boolean, nullable=False, default=False, server_default=sql_text("false"))
    last_error = Column(Text, nullable=True)
