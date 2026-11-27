from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    text as sql_text,
)

from ._base import Base, DictLikeMixin


class News(DictLikeMixin, Base):
    __tablename__ = "news"
    __table_args__ = (Index("ix_news_active_created", "is_active", "created_at"),)

    id = Column(Integer, primary_key=True)
    title = Column(String(120), nullable=False)
    content = Column(String(1000), nullable=False)
    image_url = Column(String(2048), nullable=True)
    image_mode = Column(String(16), nullable=False, default="icon", server_default=sql_text("'icon'"))
    link_url = Column(String(2048), nullable=True)
    link_text = Column(String(80), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default=sql_text("true"))
    starts_at = Column(DateTime(timezone=True), nullable=True)
    ends_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=sql_text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=sql_text("CURRENT_TIMESTAMP"),
    )
