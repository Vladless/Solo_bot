import secrets

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from ._base import Base, DictLikeMixin


class Admin(Base):
    __tablename__ = "admins"

    tg_id = Column(BigInteger, primary_key=True)
    token = Column(String, unique=True, nullable=True)
    description = Column(String, nullable=True)
    role = Column(String, nullable=False, default="admin")
    permissions = Column(JSONB, nullable=False, default=list, server_default="[]")
    added_at = Column(DateTime, default=datetime.utcnow)

    @staticmethod
    def generate_token() -> str:
        return secrets.token_urlsafe(32)


class Setting(DictLikeMixin, Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(JSONB, nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
