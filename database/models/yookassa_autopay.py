import uuid

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from ._base import Base, DictLikeMixin


class YooKassaSavedCard(DictLikeMixin, Base):
    __tablename__ = "yookassa_autopay_saved_cards"

    id = Column(String, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    tg_id = Column(BigInteger, nullable=True, index=True)
    card_type = Column(String)
    card_mask = Column(String)
    bank_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class YooKassaSubscription(DictLikeMixin, Base):
    __tablename__ = "yookassa_autopay_subscriptions"
    __table_args__ = (Index("ix_yookassa_autopay_subscriptions_owner_client", "user_id", "client_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    tg_id = Column(BigInteger, nullable=True, index=True)
    client_id = Column(String, nullable=False, index=True)
    card_id = Column(String, nullable=False)
    tariff_id = Column(Integer, nullable=True)
    period_days = Column(Integer, nullable=True)
    amount = Column(Float, nullable=False)
    accepted_gross_amount = Column(Numeric(18, 2), nullable=True)
    next_payment_date = Column(DateTime, nullable=False, index=True)
    retry_count = Column(Integer, default=0)
    last_attempt_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    is_processing = Column(Boolean, default=False, index=True)
    last_payment_id = Column(String, nullable=True)
    canceled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class YooKassaPayment(DictLikeMixin, Base):
    __tablename__ = "yookassa_autopay_payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    payment_id = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    tg_id = Column(BigInteger, nullable=True, index=True)
    amount = Column(Float, nullable=False)
    status = Column(String, nullable=False, index=True)
    payment_method_id = Column(String, nullable=True)
    confirmation_url = Column(String, nullable=True)
    is_autopay = Column(Boolean, default=False)
    subscription_id = Column(Integer, nullable=True)
    metadata_ = Column("metadata", Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class YooKassaLog(DictLikeMixin, Base):
    __tablename__ = "yookassa_autopay_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    tg_id = Column(BigInteger, nullable=True, index=True)
    action = Column(String, nullable=False)
    details = Column(Text, nullable=True)
    level = Column(String, default="info")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class YooKassaAutopayAttempt(DictLikeMixin, Base):
    __tablename__ = "yookassa_autopay_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('prepared','unknown','pending','succeeded','canceled','applied','manual_review')",
            name="ck_yookassa_autopay_attempt_status",
        ),
        Index("ix_yookassa_autopay_attempt_owner_created", "user_id", "created_at"),
        Index(
            "uq_yookassa_autopay_attempt_unfinished_subscription",
            "subscription_id",
            unique=True,
            postgresql_where=text("subscription_id IS NOT NULL AND status NOT IN ('applied','canceled')"),
            sqlite_where=text("subscription_id IS NOT NULL AND status NOT IN ('applied','canceled')"),
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    subscription_id = Column(
        Integer, ForeignKey("yookassa_autopay_subscriptions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    payload = Column(JSONB, nullable=False)
    intent = Column(JSONB, nullable=False, default=dict)
    status = Column(String(32), nullable=False, default="prepared", index=True)
    payment_id = Column(String(128), nullable=True, unique=True)
    confirmation_url = Column(String, nullable=True)
    reservation_released = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    consent_canceled = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
