import uuid

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    String,
    text as sql_text,
)

from ._base import Base, DictLikeMixin


class Identity(DictLikeMixin, Base):
    """Слой идентификации: к одному identity можно привязать email и/или Telegram (tg_id)."""

    __tablename__ = "identities"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=True, index=True)
    tg_id = Column(BigInteger, unique=True, nullable=True, index=True)
    google_sub = Column(String(64), unique=True, nullable=True, index=True)
    yandex_sub = Column(String(64), unique=True, nullable=True, index=True)
    api_token_hash = Column(String(64), nullable=True, index=True)
    token_issued_at = Column(DateTime, nullable=True)
    password_hash = Column(String(64), nullable=True)
    email_verified = Column(Boolean, nullable=False, server_default=sql_text("false"))
    is_admin = Column(Boolean, nullable=False, server_default=sql_text("false"))
    onboarding_completed_at = Column(DateTime, nullable=True)
    onboarding_stage = Column(String(32), nullable=True)
    signup_origin = Column(String(16), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def onboarding_completed(self) -> bool:
        return self.onboarding_completed_at is not None

    @property
    def password_set(self) -> bool:
        return bool(self.password_hash)
