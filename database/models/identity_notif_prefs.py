from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    PrimaryKeyConstraint,
    String,
)

from ._base import Base, DictLikeMixin


class IdentityNotifPref(DictLikeMixin, Base):
    """Пользовательские настройки каналов доставки уведомлений."""

    __tablename__ = "identity_notif_prefs"

    identity_id = Column(
        String(36),
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel = Column(String(32), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (PrimaryKeyConstraint("identity_id", "channel"),)
