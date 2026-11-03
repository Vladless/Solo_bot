from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB

from ._base import Base, DictLikeMixin


class YooKassaPageIntent(DictLikeMixin, Base):
    __tablename__ = "yookassa_page_intents"
    __table_args__ = (Index("ix_yookassa_page_intents_expires_at", "expires_at"),)

    id = Column(String(36), primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    invoice = Column(JSONB, nullable=False)
    methods = Column(JSONB, nullable=False)
    attempts = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
