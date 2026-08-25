import uuid

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ._base import Base, DictLikeMixin


class Gift(DictLikeMixin, Base):
    __tablename__ = "gifts"

    gift_id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    sender_user_id = Column(BigInteger, nullable=True)
    recipient_user_id = Column(BigInteger, nullable=True)
    sender_tg_id = Column(BigInteger, nullable=True, index=True)
    recipient_tg_id = Column(BigInteger, nullable=True, index=True)
    selected_months = Column(Integer)
    expiry_time = Column(DateTime)
    gift_link = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_used = Column(Boolean, default=False)
    is_unlimited = Column(Boolean, default=False)
    max_usages = Column(Integer, nullable=True)
    tariff_id: Mapped[int | None] = mapped_column(ForeignKey("tariffs.id"))

    selected_device_limit = Column(Integer, nullable=True)
    selected_traffic_gb = Column(Integer, nullable=True)
    selected_price_rub = Column(Integer, nullable=True)


class GiftUsage(DictLikeMixin, Base):
    __tablename__ = "gift_usages"

    gift_id = Column(String, ForeignKey("gifts.gift_id"), primary_key=True)
    user_id = Column(BigInteger, nullable=False, primary_key=True)
    tg_id = Column(BigInteger, nullable=True, index=True)
    used_at = Column(DateTime, default=datetime.utcnow)
