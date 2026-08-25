from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from ._base import Base, DictLikeMixin


class Key(DictLikeMixin, Base):
    __tablename__ = "keys"

    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, nullable=False, index=True
    )
    client_id = Column(String, primary_key=True)
    tg_id = Column(BigInteger, nullable=True, index=True)
    email = Column(String, unique=True)
    created_at = Column(BigInteger)
    expiry_time = Column(BigInteger, index=True)
    key = Column(String)
    server_id = Column(String, index=True)
    remnawave_link = Column(String)
    tariff_id = Column(Integer, ForeignKey("tariffs.id", ondelete="SET NULL"), index=True)
    is_frozen = Column(Boolean, default=False)
    alias = Column(String)
    notified = Column(Boolean, default=False)
    notified_24h = Column(Boolean, default=False)

    selected_device_limit = Column(Integer, nullable=True)
    selected_traffic_limit = Column(BigInteger, nullable=True)
    selected_price_rub = Column(Integer, nullable=True)

    current_device_limit = Column(Integer, nullable=True)
    current_traffic_limit = Column(BigInteger, nullable=True)


class KeyTrafficHistory(DictLikeMixin, Base):
    __tablename__ = "key_traffic_history"
    __table_args__ = (
        UniqueConstraint("client_id", "snapshot_date", name="uq_key_traffic_history_client_date"),
        Index("ix_key_traffic_history_client_date", "client_id", "snapshot_date"),
        Index("ix_key_traffic_history_date", "snapshot_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(128), nullable=False)
    tg_id = Column(BigInteger, nullable=True)
    used_gb = Column(Float, nullable=True)
    limit_gb = Column(Float, nullable=True)
    snapshot_date = Column(Date, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class KeyTrafficHourly(DictLikeMixin, Base):
    __tablename__ = "key_traffic_hourly"
    __table_args__ = (
        UniqueConstraint("client_id", "snapshot_hour", name="uq_key_traffic_hourly_client_hour"),
        Index("ix_key_traffic_hourly_client_hour", "client_id", "snapshot_hour"),
        Index("ix_key_traffic_hourly_hour", "snapshot_hour"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(128), nullable=False)
    tg_id = Column(BigInteger, nullable=True)
    used_gb = Column(Float, nullable=True)
    snapshot_hour = Column(DateTime, nullable=False)
