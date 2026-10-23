import uuid

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from ._base import Base, DictLikeMixin


class KassaISavedMethod(DictLikeMixin, Base):
    __tablename__ = "kassai_autopay_methods"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    token = Column(String, nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    source_payment_id = Column(String(128), nullable=False, unique=True)
    is_active = Column(Boolean, nullable=False, default=True)
    deleted_at = Column(DateTime, nullable=True)
    revoke_pending = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "is_active": self.is_active,
            "deleted_at": self.deleted_at,
            "created_at": self.created_at,
        }


class KassaISubscription(DictLikeMixin, Base):
    __tablename__ = "kassai_autopay_subscriptions"
    __table_args__ = (
        CheckConstraint("amount >= 1 AND period_days > 0", name="ck_kassai_autopay_subscription_terms"),
        Index(
            "uq_kassai_autopay_active_owner_client",
            "user_id",
            "client_id",
            unique=True,
            postgresql_where=text("is_active IS TRUE"),
            sqlite_where=text("is_active IS TRUE"),
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    client_id = Column(String, nullable=False, index=True)
    method_id = Column(String(36), ForeignKey("kassai_autopay_methods.id"), nullable=False)
    tariff_id = Column(Integer, nullable=False)
    period_days = Column(Integer, nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)
    consent = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    next_payment_date = Column(DateTime, nullable=False, index=True)
    retry_count = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    canceled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KassaIAutopayAttempt(DictLikeMixin, Base):
    __tablename__ = "kassai_autopay_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('prepared','unknown','pending','succeeded','canceled','applied','manual_review')",
            name="ck_kassai_autopay_attempt_status",
        ),
        Index(
            "uq_kassai_autopay_attempt_unfinished_subscription",
            "subscription_id",
            unique=True,
            postgresql_where=text("subscription_id IS NOT NULL AND status NOT IN ('applied','canceled')"),
            sqlite_where=text("subscription_id IS NOT NULL AND status NOT IN ('applied','canceled')"),
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    subscription_id = Column(Integer, ForeignKey("kassai_autopay_subscriptions.id"), nullable=True, index=True)
    method_id = Column(String(36), ForeignKey("kassai_autopay_methods.id"), nullable=True)
    payment_id = Column(String(128), nullable=False, unique=True)
    provider_order_id = Column(String(128), nullable=True)
    payload = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    intent = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    status = Column(String(32), nullable=False, default="prepared", index=True)
    confirmation_url = Column(String, nullable=True)
    fulfilled_client_id = Column(String, nullable=True)
    reservation_released = Column(Boolean, nullable=False, default=False)
    consent_canceled = Column(Boolean, nullable=False, default=False)
    autopay_consent_applied = Column(Boolean, nullable=False, default=False)
    owner_deleted = Column(Boolean, nullable=False, default=False)
    payment_finalized = Column(Boolean, nullable=False, default=False)
    notice_sent = Column(Boolean, nullable=False, default=False)
    review_reason = Column(String, nullable=True)
    dispatched_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
