import secrets
import uuid

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
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declarative_base, mapped_column, relationship


Base = declarative_base()


class DictLikeMixin:
    def __getitem__(self, key):
        return getattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


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
    preferred_currency = Column(String(10), nullable=False, server_default="RUB", index=True)
    source_code = Column(
        String,
        ForeignKey(
            "tracking_sources.code",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        nullable=True,
    )
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
    tariff_id = Column(Integer, ForeignKey("tariffs.id", ondelete="SET NULL"))
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
    subgroup_title = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=True)
    vless = Column(Boolean, default=False)


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

    subgroups = relationship("ServerSubgroup", back_populates="server", cascade="all, delete-orphan")
    groups = relationship("ServerSpecialgroup", back_populates="server", cascade="all, delete-orphan")


class ServerSubgroup(DictLikeMixin, Base):
    __tablename__ = "server_subgroups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(Integer, ForeignKey("servers.id", ondelete="CASCADE"), index=True, nullable=False)
    group_code = Column(String, nullable=False)
    subgroup_title = Column(String, nullable=False)

    server = relationship("Server", back_populates="subgroups")

    __table_args__ = (UniqueConstraint("server_id", "subgroup_title", name="uq_server_subgroup"),)


class ServerSpecialgroup(DictLikeMixin, Base):
    __tablename__ = "server_specialgroups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(Integer, ForeignKey("servers.id", ondelete="CASCADE"), index=True, nullable=False)
    group_code = Column(String, nullable=False)

    server = relationship("Server")

    __table_args__ = (UniqueConstraint("server_id", "group_code", name="uq_server_group"),)


class Payment(DictLikeMixin, Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    tg_id = Column(BigInteger, ForeignKey("users.tg_id"))
    amount = Column(Float)
    payment_system = Column(String)
    status = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    original_amount = Column(Numeric(18, 8), nullable=True)
    currency = Column(String(10), nullable=False, server_default="RUB")
    payment_id = Column(String(128), nullable=True, index=True)
    metadata_ = Column("metadata", JSONB, nullable=True)


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

    coupon_id = Column(Integer, ForeignKey("coupons.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(BigInteger, primary_key=True)
    used_at = Column(DateTime, default=datetime.utcnow)


class Referral(DictLikeMixin, Base):
    __tablename__ = "referrals"

    referred_tg_id = Column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), primary_key=True)
    referrer_tg_id = Column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), primary_key=True)
    reward_issued = Column(Boolean, default=False)


class Notification(DictLikeMixin, Base):
    __tablename__ = "notifications"

    tg_id = Column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), primary_key=True)
    notification_type = Column(String, primary_key=True)
    last_notification_time = Column(DateTime, default=datetime.utcnow)


class Gift(DictLikeMixin, Base):
    __tablename__ = "gifts"

    gift_id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    sender_tg_id = Column(BigInteger, ForeignKey("users.tg_id"))
    recipient_tg_id = Column(BigInteger, ForeignKey("users.tg_id"), nullable=True)
    selected_months = Column(Integer)
    expiry_time = Column(DateTime)
    gift_link = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_used = Column(Boolean, default=False)
    is_unlimited = Column(Boolean, default=False)
    max_usages = Column(Integer, nullable=True)
    tariff_id: Mapped[int | None] = mapped_column(ForeignKey("tariffs.id"))


class GiftUsage(DictLikeMixin, Base):
    __tablename__ = "gift_usages"

    gift_id = Column(String, ForeignKey("gifts.gift_id"), primary_key=True)
    tg_id = Column(BigInteger, primary_key=True)
    used_at = Column(DateTime, default=datetime.utcnow)


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


class Admin(Base):
    __tablename__ = "admins"

    tg_id = Column(BigInteger, primary_key=True)
    token = Column(String, unique=True, nullable=True)
    description = Column(String, nullable=True)
    role = Column(String, nullable=False, default="admin")
    added_at = Column(DateTime, default=datetime.utcnow)

    @staticmethod
    def generate_token() -> str:
        return secrets.token_urlsafe(32)
