from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    text as sql_text,
)

from ._base import Base, DictLikeMixin


class DailyBonusClaim(DictLikeMixin, Base):
    __tablename__ = "daily_bonus_claims"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    tg_id = Column(BigInteger, nullable=True)
    amount = Column(Float, nullable=False, default=0.0, server_default=sql_text("0"))
    streak = Column(Integer, nullable=False, default=1, server_default=sql_text("1"))
    source = Column(String(16), nullable=False, default="web", server_default=sql_text("'web'"))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=sql_text("CURRENT_TIMESTAMP"))

    __table_args__ = (Index("ix_daily_bonus_claims_user_created", "user_id", "created_at"),)
