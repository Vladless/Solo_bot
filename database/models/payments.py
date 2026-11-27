from datetime import UTC, datetime

from sqlalchemy import BigInteger, Column, DateTime, Float, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB

from ._base import Base, DictLikeMixin


class Payment(DictLikeMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        Index("ix_payments_status_created", "status", "created_at"),
        Index("ix_payments_created", "created_at"),
    )

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


class PaymentOperationReceipt(DictLikeMixin, Base):
    __tablename__ = "payment_operation_receipts"

    provider = Column(String(32), primary_key=True)
    operation_id = Column(String(128), primary_key=True)
    invoice_payment_id = Column(Integer, ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_record_id = Column(Integer, ForeignKey("payments.id", ondelete="SET NULL"), nullable=True, index=True)
    invoice_label = Column(String(128), nullable=False)
    amount_received = Column(Numeric(18, 2), nullable=False)
    amount_withdrawn = Column(Numeric(18, 2), nullable=False)
    credited_amount = Column(Numeric(18, 2), nullable=False)
    currency = Column(String(10), nullable=False)
    operation_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
