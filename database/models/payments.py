from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Float, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB

from ._base import Base, DictLikeMixin


class Payment(DictLikeMixin, Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    tg_id = Column(BigInteger, nullable=True, index=True)
    amount = Column(Float)
    payment_system = Column(String)
    status = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    original_amount = Column(Numeric(18, 8), nullable=True)
    currency = Column(String(10), nullable=False, server_default="RUB")
    payment_id = Column(String(128), nullable=True, index=True)
    metadata_ = Column("metadata", JSONB, nullable=True)
