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


class PayHotSavedMethod(DictLikeMixin, Base):
    __tablename__ = "payhot_autopay_methods"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    mandate_id = Column(String(128), nullable=False, unique=True)
    merchant_mandate_reference = Column(String(255), nullable=False)
    customer_reference = Column(String(50), nullable=False)
    payment_method = Column(String(8), nullable=False)
    source_payment_id = Column(String(128), nullable=False)
    display_mask = Column(String(128), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    deleted_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "payment_method": self.payment_method,
            "display_mask": self.display_mask,
            "is_active": self.is_active,
            "deleted_at": self.deleted_at,
            "created_at": self.created_at,
        }


class PayHotSubscription(DictLikeMixin, Base):
    __tablename__ = "payhot_autopay_subscriptions"
    __table_args__ = (
        CheckConstraint("amount > 0 AND period_days > 0", name="ck_payhot_autopay_subscription_terms"),
        Index(
            "uq_payhot_autopay_active_owner_client",
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
    method_id = Column(String(36), ForeignKey("payhot_autopay_methods.id"), nullable=False)
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


class PayHotAutopayAttempt(DictLikeMixin, Base):
    __tablename__ = "payhot_autopay_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('prepared','unknown','pending','succeeded','canceled','applied','manual_review')",
            name="ck_payhot_autopay_attempt_status",
        ),
        Index(
            "uq_payhot_autopay_attempt_unfinished_subscription",
            "subscription_id",
            unique=True,
            postgresql_where=text("status NOT IN ('applied','canceled')"),
            sqlite_where=text("status NOT IN ('applied','canceled')"),
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    subscription_id = Column(Integer, ForeignKey("payhot_autopay_subscriptions.id"), nullable=False, index=True)
    method_id = Column(String(36), ForeignKey("payhot_autopay_methods.id"), nullable=False)
    payment_id = Column(String(128), nullable=False, unique=True)
    provider_charge_id = Column(String(128), nullable=True, unique=True)
    payload = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    intent = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    status = Column(String(32), nullable=False, default="prepared", index=True)
    reservation_released = Column(Boolean, nullable=False, default=False)
    consent_canceled = Column(Boolean, nullable=False, default=False)
    owner_deleted = Column(Boolean, nullable=False, default=False)
    notice_sent = Column(Boolean, nullable=False, default=False)
    payment_finalized = Column(Boolean, nullable=False, default=False)
    review_reason = Column(String, nullable=True)
    dispatch_attempts = Column(Integer, nullable=False, default=0)
    dispatched_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PayHotAutopayLog(DictLikeMixin, Base):
    __tablename__ = "payhot_autopay_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    attempt_id = Column(String(36), ForeignKey("payhot_autopay_attempts.id"), nullable=False, index=True)
    status = Column(String(32), nullable=False)
    reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
