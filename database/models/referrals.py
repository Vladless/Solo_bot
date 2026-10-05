from sqlalchemy import BigInteger, Boolean, Column, ForeignKey

from ._base import Base, DictLikeMixin


class Referral(DictLikeMixin, Base):
    __tablename__ = "referrals"

    referred_user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    referrer_user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    referred_tg_id = Column(BigInteger, nullable=True, index=True)
    referrer_tg_id = Column(BigInteger, nullable=True, index=True)
    reward_issued = Column(Boolean, default=False)
