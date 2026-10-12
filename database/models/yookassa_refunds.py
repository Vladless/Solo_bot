import uuid

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB

from ._base import Base, DictLikeMixin


class RefundOperation(DictLikeMixin, Base):
    __tablename__ = "yookassa_refund_operations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','prepared','unknown','pending','succeeded','canceled','failed','manual_review')",
            name="ck_yookassa_refund_operation_status",
        ),
        Index("ix_yookassa_refund_payment_created", "payment_record_id", "created_at"),
        Index("ix_yookassa_refund_poll", "status", "next_retry_at"),
        Index(
            "uq_yookassa_refund_live_payment",
            "payment_record_id",
            unique=True,
            postgresql_where=text("status IN ('prepared','unknown','pending','manual_review')"),
            sqlite_where=text("status IN ('prepared','unknown','pending','manual_review')"),
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    payment_record_id = Column(Integer, ForeignKey("payments.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id = Column(BigInteger, nullable=True)
    tg_id = Column(BigInteger, nullable=True)
    actor_id = Column(BigInteger, nullable=False)
    payment_id = Column(String(128), nullable=False)
    provider = Column(String(32), nullable=False)
    shop_id = Column(String(128), nullable=False)
    original_net = Column(Numeric(18, 2), nullable=False)
    original_gross = Column(Numeric(18, 2), nullable=False)
    original_refunded = Column(Numeric(18, 2), nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)
    balance_debit = Column(Numeric(18, 2), nullable=False)
    description = Column(String(250), nullable=False, default="")
    payload = Column(JSONB, nullable=False)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    refund_id = Column(String(128), nullable=True, unique=True)
    status = Column(String(32), nullable=False, default="draft", index=True)
    response = Column(JSONB, nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(String(512), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(36), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    first_submitted_at = Column(DateTime, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
