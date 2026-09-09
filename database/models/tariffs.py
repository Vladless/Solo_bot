from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ._base import Base, DictLikeMixin


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
    description = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=True)
    vless = Column(Boolean, default=False)
    external_squad: Mapped[str | None] = mapped_column(String(64), nullable=True)

    configurable = Column(Boolean, nullable=False, server_default="false")

    cooldown_days = Column(Integer, nullable=False, server_default="0")

    visibility_rules = Column(JSONB, nullable=True)

    device_options = Column(JSONB, nullable=True)
    traffic_options_gb = Column(JSONB, nullable=True)

    device_step_rub = Column(Integer, nullable=True)
    device_overrides = Column(JSONB, nullable=True)

    traffic_step_rub = Column(Integer, nullable=True)
    traffic_overrides = Column(JSONB, nullable=True)


class TariffSubgroupSetting(DictLikeMixin, Base):
    __tablename__ = "tariff_subgroup_settings"

    id = Column(Integer, primary_key=True)
    group_code = Column(String, nullable=False)
    subgroup_title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, server_default=text("now()"))

    __table_args__ = (UniqueConstraint("group_code", "subgroup_title", name="uq_tariff_subgroup_setting"),)
