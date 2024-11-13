from sqlalchemy import BigInteger, Boolean, Column, Float, ForeignKey, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Connection(Base):
    """
    Модель для таблицы connections
    """

    __tablename__ = "connections"

    tg_id = Column(BigInteger, primary_key=True, nullable=False)
    balance = Column(Float, nullable=False, default=0.0)
    trial = Column(BigInteger, nullable=False, default=0)

    # Связь с ключами и рефералами
    keys = relationship("Key", back_populates="connection")
    referrals_received = relationship(
        "Referral", foreign_keys="Referral.referred_tg_id", back_populates="referred"
    )
    referrals_sent = relationship(
        "Referral", foreign_keys="Referral.referrer_tg_id", back_populates="referrer"
    )


class Key(Base):
    """
    Модель для таблицы keys
    """

    __tablename__ = "keys"

    tg_id = Column(
        BigInteger, ForeignKey("connections.tg_id"), primary_key=True, nullable=False
    )
    client_id = Column(Text, primary_key=True, nullable=False)
    email = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)
    expiry_time = Column(BigInteger, nullable=False)
    key = Column(Text, nullable=False)
    server_id = Column(Text, nullable=False, default="server1")
    notified = Column(Boolean, nullable=False, default=False)
    notified_24h = Column(Boolean, nullable=False, default=False)

    # Связь с подключением
    connection = relationship("Connection", back_populates="keys")


class Referral(Base):
    """
    Модель для таблицы referrals
    """

    __tablename__ = "referrals"

    referred_tg_id = Column(
        BigInteger, ForeignKey("connections.tg_id"), primary_key=True, nullable=False
    )
    referrer_tg_id = Column(BigInteger, ForeignKey("connections.tg_id"), nullable=False)
    reward_issued = Column(Boolean, default=False)

    # Связи с подключениями
    referred = relationship(
        "Connection", foreign_keys=[referred_tg_id], back_populates="referrals_received"
    )
    referrer = relationship(
        "Connection", foreign_keys=[referrer_tg_id], back_populates="referrals_sent"
    )
