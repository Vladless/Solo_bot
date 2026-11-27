import uuid

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from ._base import Base, DictLikeMixin


class KeyPanelCleanup(DictLikeMixin, Base):
    __tablename__ = "key_panel_cleanup"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(BigInteger, nullable=False, index=True)
    client_id = Column(String, nullable=False)
    target_client_id = Column(String, nullable=False)
    creation_finished = Column(Boolean, nullable=False, default=False)
    email = Column(String, nullable=False, index=True)
    previous_revision = Column(String(36), nullable=True)
    old_server = Column(JSONB, nullable=False)
    target_server = Column(JSONB, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    settle_after = Column(DateTime(timezone=True), nullable=False)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
