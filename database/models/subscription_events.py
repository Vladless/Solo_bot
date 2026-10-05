from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, Float, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB

from ._base import Base, DictLikeMixin


class SubscriptionEvent(DictLikeMixin, Base):
    """Append-only журнал жизненного цикла подписок. Никогда не удаляется.

    Источник истины для динамики: активные на любую дату, отток, новые/продления,
    разрезы по тарифам/серверам, когорты. Восполняет потерю данных при удалении ключей.
    """

    __tablename__ = "subscription_events"
    __table_args__ = (
        Index("ix_subscription_events_type_created", "event_type", "created_at"),
        Index("ix_subscription_events_created", "created_at"),
        Index("ix_subscription_events_client", "client_id"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    event_type = Column(String(24), nullable=False, index=True)
    user_id = Column(BigInteger, nullable=True, index=True)
    tg_id = Column(BigInteger, nullable=True, index=True)
    client_id = Column(String(128), nullable=True)
    tariff_id = Column(Integer, nullable=True)
    server_id = Column(String, nullable=True)
    price_rub = Column(Float, nullable=True)
    duration_days = Column(Integer, nullable=True)
    expiry_time = Column(BigInteger, nullable=True)
    was_expired = Column(Boolean, nullable=True)
    source = Column(String(32), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=True)
    created_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, server_default=text("CURRENT_TIMESTAMP"), index=True
    )


class DailySubscriptionMetric(DictLikeMixin, Base):
    """Дневной снапшот метрик подписок (быстрый кэш для дашборда)."""

    __tablename__ = "daily_subscription_metrics"

    snapshot_date = Column(Date, primary_key=True)
    active = Column(Integer, nullable=False, server_default=text("0"))
    created = Column(Integer, nullable=False, server_default=text("0"))
    renewed = Column(Integer, nullable=False, server_default=text("0"))
    expired = Column(Integer, nullable=False, server_default=text("0"))
    deleted = Column(Integer, nullable=False, server_default=text("0"))
    revenue_rub = Column(Float, nullable=False, server_default=text("0"))
    by_tariff = Column(JSONB, nullable=True)
    by_server = Column(JSONB, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=text("CURRENT_TIMESTAMP"))
