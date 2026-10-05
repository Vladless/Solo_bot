from pydantic import BaseModel


class ReferralResponse(BaseModel):
    referred_user_id: int
    referrer_user_id: int
    reward_issued: bool = False

    class Config:
        from_attributes = True
