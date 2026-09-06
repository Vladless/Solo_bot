import uuid

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB

from ._base import Base, DictLikeMixin


class Poll(DictLikeMixin, Base):
    __tablename__ = "polls"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    question = Column(Text, nullable=False)
    options = Column(JSONB, nullable=False)
    allows_multiple = Column(Boolean, nullable=False, server_default=sql_text("false"))
    is_anonymous = Column(Boolean, nullable=False, server_default=sql_text("false"))
    status = Column(String(16), nullable=False, server_default=sql_text("'open'"), index=True)
    sent_count = Column(Integer, nullable=False, server_default=sql_text("0"))
    created_by_tg_id = Column(BigInteger, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=sql_text("now()"))
    closed_at = Column(DateTime(timezone=True), nullable=True)


class PollMessage(DictLikeMixin, Base):
    __tablename__ = "poll_messages"

    telegram_poll_id = Column(String(64), primary_key=True)
    poll_id = Column(String(36), ForeignKey("polls.id", ondelete="CASCADE"), nullable=False, index=True)
    tg_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=sql_text("now()"))


class PollVote(DictLikeMixin, Base):
    __tablename__ = "poll_votes"

    poll_id = Column(String(36), ForeignKey("polls.id", ondelete="CASCADE"), primary_key=True)
    tg_id = Column(BigInteger, primary_key=True)
    option_ids = Column(JSONB, nullable=False)
    voted_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=sql_text("now()"))
