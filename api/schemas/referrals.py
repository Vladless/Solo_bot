from pydantic import BaseModel


class ReferralResponse(BaseModel):
    referred_tg_id: int
    referrer_tg_id: int
    reward_issued: bool = False

    class Config:
        from_attributes = True
