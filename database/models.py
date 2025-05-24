from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

Base = declarative_base()


class DictLikeMixin:
    def __getitem__(self, key):
        return getattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def to_dict(self):
        return {
            column.name: getattr(self, column.name) for column in self.__table__.columns
        }


class User(DictLikeMixin, Base):
    __tablename__ = "users"

    tg_id = Column(BigInteger, primary_key=True)
    username = Column(String)
    first_name = Column(String)
    last_name = Column(String)
    language_code = Column(String)
    is_bot = Column(Boolean, default=False)
    balance = Column(Float, default=0.0)
    trial = Column(Integer, default=0)
    source_code = Column(String, ForeignKey("tracking_sources.code"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Key(DictLikeMixin, Base):
    __tablename__ = "keys"

    tg_id = Column(BigInteger, ForeignKey("users.tg_id"), nullable=False)
    client_id = Column(String, primary_key=True)
    email = Column(String, unique=True)
    created_at = Column(BigInteger)
    expiry_time = Column(BigInteger)
    key = Column(String)
    server_id = Column(String)
    remnawave_link = Column(String)
    tariff_id = Column(Integer, ForeignKey("tariffs.id"))
    is_frozen = Column(Boolean, default=False)
    alias = Column(String)
    notified = Column(Boolean, default=False)
    notified_24h = Column(Boolean, default=False)


class Tariff(DictLikeMixin, Base):
    __tablename__ = "tariffs"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    group_code = Column(String)
    duration_days = Column(Integer)
    price_rub = Column(Integer)
    traffic_limit = Column(BigInteger, nullable=True)
    device_limit = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Server(DictLikeMixin, Base):
    __tablename__ = "servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cluster_name = Column(String)
    server_name = Column(String, unique=True)
    api_url = Column(String)
    subscription_url = Column(String)
    inbound_id = Column(String)
    panel_type = Column(String)
    max_keys = Column(Integer)
    tariff_group = Column(String)
    enabled = Column(Boolean, default=True)


class Payment(DictLikeMixin, Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    tg_id = Column(BigInteger, ForeignKey("users.tg_id"))
    amount = Column(Float)
    payment_system = Column(String)
    status = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class Coupon(DictLikeMixin, Base):
    __tablename__ = "coupons"

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True)
    amount = Column(Integer)
    usage_limit = Column(Integer)
    usage_count = Column(Integer, default=0)
    is_used = Column(Boolean, default=False)
    days = Column(Integer, nullable=True)


class CouponUsage(DictLikeMixin, Base):
    __tablename__ = "coupon_usages"

    coupon_id = Column(Integer, ForeignKey("coupons.id"), primary_key=True)
    user_id = Column(BigInteger, primary_key=True)
    used_at = Column(DateTime, default=datetime.utcnow)


class Referral(DictLikeMixin, Base):
    __tablename__ = "referrals"

    referred_tg_id = Column(BigInteger, ForeignKey("users.tg_id"), primary_key=True)
    referrer_tg_id = Column(BigInteger, ForeignKey("users.tg_id"), primary_key=True)
    reward_issued = Column(Boolean, default=False)


class Notification(DictLikeMixin, Base):
    __tablename__ = "notifications"

    tg_id = Column(BigInteger, ForeignKey("users.tg_id"), primary_key=True)
    notification_type = Column(String, primary_key=True)
    last_notification_time = Column(DateTime, default=datetime.utcnow)


class Gift(DictLikeMixin, Base):
    __tablename__ = "gifts"

    gift_id = Column(String, primary_key=True)
    sender_tg_id = Column(BigInteger, ForeignKey("users.tg_id"))
    recipient_tg_id = Column(BigInteger, ForeignKey("users.tg_id"), nullable=True)
    selected_months = Column(Integer)
    expiry_time = Column(DateTime)
    gift_link = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_used = Column(Boolean, default=False)
    tariff_id: Mapped[int | None] = mapped_column(ForeignKey("tariffs.id"))


class ManualBan(DictLikeMixin, Base):
    __tablename__ = "manual_bans"

    tg_id = Column(BigInteger, primary_key=True)
    banned_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    reason = Column(Text)
    banned_by = Column(BigInteger)
    until = Column(DateTime(timezone=True), nullable=True)


class TemporaryData(DictLikeMixin, Base):
    __tablename__ = "temporary_data"

    tg_id = Column(BigInteger, primary_key=True)
    state = Column(String)
    data = Column(JSON)
    updated_at = Column(DateTime, default=datetime.utcnow)


class BlockedUser(DictLikeMixin, Base):
    __tablename__ = "blocked_users"

    tg_id = Column(BigInteger, primary_key=True)


class TrackingSource(DictLikeMixin, Base):
    __tablename__ = "tracking_sources"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    code = Column(String, unique=True)
    type = Column(String)
    created_by = Column(BigInteger)
    created_at = Column(DateTime, default=datetime.utcnow)


class Log(DictLikeMixin, Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True)
    event_type = Column(String)
    tg_id = Column(BigInteger)
    details = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
