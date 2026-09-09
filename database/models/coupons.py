from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    text as sql_text,
)

from ._base import Base, DictLikeMixin


class Coupon(DictLikeMixin, Base):
    __tablename__ = "coupons"

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True)
    amount = Column(Integer)
    usage_limit = Column(Integer)
    usage_count = Column(Integer, default=0)
    is_used = Column(Boolean, default=False)
    days = Column(Integer, nullable=True)
    new_users_only = Column(Boolean, nullable=False, server_default=sql_text("false"))

    percent = Column(Integer, nullable=True)
    max_discount_amount = Column(Integer, nullable=True)
    min_order_amount = Column(Integer, nullable=True)


class CouponUsage(DictLikeMixin, Base):
    __tablename__ = "coupon_usages"

    coupon_id = Column(Integer, ForeignKey("coupons.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    tg_id = Column(BigInteger, nullable=True, index=True)
    used_at = Column(DateTime, default=datetime.utcnow)
