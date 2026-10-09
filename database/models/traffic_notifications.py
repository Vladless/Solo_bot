from sqlalchemy import JSON, BigInteger, Column, ForeignKeyConstraint, String

from ._base import Base, DictLikeMixin


class TrafficNotificationState(DictLikeMixin, Base):
    __tablename__ = "traffic_notification_states"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "client_id"],
            ["keys.user_id", "keys.client_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
    )

    user_id = Column(BigInteger, primary_key=True)
    client_id = Column(String, primary_key=True)
    meters = Column(JSON, nullable=False, default=dict)
    claim_token = Column(String(36), nullable=True)
    claim_until = Column(BigInteger, nullable=False, default=0)
    claim_targets = Column(JSON, nullable=False, default=dict)
