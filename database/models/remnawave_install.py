import uuid

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB

from ._base import Base, DictLikeMixin


class RemnawaveInstall(DictLikeMixin, Base):
    __tablename__ = "remnawave_installs"
    __table_args__ = (
        Index("ix_remnawave_installs_poll", "status", "next_retry_at"),
        Index(
            "uq_remnawave_installs_active_address",
            "address",
            unique=True,
            postgresql_where=text("address <> '' AND status IN ('queued','running','waiting_dns','cancel_requested')"),
            sqlite_where=text("address <> '' AND status IN ('queued','running','waiting_dns','cancel_requested')"),
        ),
    )

    id = Column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    owner_id = Column(BigInteger, nullable=False, index=True)
    chat_id = Column(BigInteger, nullable=False)
    progress_message_id = Column(BigInteger, nullable=True)
    address = Column(String(45), nullable=False, default="")
    params = Column(JSONB, nullable=False, default=dict)
    fingerprint = Column(String(64), nullable=True)
    encrypted_secrets = Column(Text, nullable=True)
    encrypted_artifacts = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="draft", index=True)
    stage = Column(String(32), nullable=False, default="server")
    logs = Column(JSONB, nullable=False, default=list)
    dns_records = Column(JSONB, nullable=False, default=list)
    error = Column(String(512), nullable=True)
    retryable = Column(Boolean, nullable=False, default=True)
    pause_requested = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(32), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    secrets_expire_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
