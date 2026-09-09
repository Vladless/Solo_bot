from sqlalchemy import (
    BigInteger,
    Column,
    Index,
    Integer,
    String,
    text as sql_text,
)

from ._base import Base, DictLikeMixin


class RateLimitCounter(DictLikeMixin, Base):
    """Счётчик запросов в окне: общий на все процессы, когда Redis недоступен."""

    __tablename__ = "rate_limit_counters"
    __table_args__ = (Index("ix_rate_limit_counters_window", "window_start"),)

    bucket = Column(String(255), primary_key=True)
    window_start = Column(BigInteger, primary_key=True)
    count = Column(Integer, nullable=False, server_default=sql_text("0"))
