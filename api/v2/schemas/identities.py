from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from utils.cpu_tasks import BCRYPT_MAX_PASSWORD_BYTES


def _validate_password_bytes(value: str) -> str:
    """Проверяет размер нового пароля до вычисления хеша."""
    if len(value.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(f"Пароль должен занимать не более {BCRYPT_MAX_PASSWORD_BYTES} байт UTF-8")
    return value


NewPassword = Annotated[str, Field(min_length=8), AfterValidator(_validate_password_bytes)]


class IdentityCreate(BaseModel):
    email: str | None = Field(None, description="Почта для привязки")
    tg_id: int | None = Field(None, description="Telegram ID для привязки")


class IdentityResponse(BaseModel):
    id: str
    email: str | None
    tg_id: int | None
    display_name: str | None = None
    username: str | None = None
    avatar_url: str | None = None
    is_admin: bool = False
    role: str = "user"
    email_verified: bool = False
    password_set: bool = False
    onboarding_completed: bool = False
    onboarding_stage: str | None = None
    created_at: datetime | None
    updated_at: datetime | None

    class Config:
        from_attributes = True


class RegisterByEmailRequest(BaseModel):
    email: str = Field(..., min_length=1)
    password: NewPassword = Field(..., description="Пароль (минимум 8 символов, не более 72 байт UTF-8)")
    referral_code: str | None = Field(None, min_length=1)
    turnstile_token: str | None = Field(default=None, description="Cloudflare Turnstile CAPTCHA token")


class RegisterResponse(BaseModel):
    identity_id: str


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(...)


class SetPasswordRequest(BaseModel):
    password: NewPassword = Field(..., description="Новый пароль (минимум 8 символов, не более 72 байт UTF-8)")
    password_confirm: NewPassword


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(...)
    password: NewPassword = Field(..., description="Новый пароль (минимум 8 символов, не более 72 байт UTF-8)")
    password_confirm: NewPassword


class LoginResponse(BaseModel):
    identity_id: str
    identity: IdentityResponse


class SendLoginCodeRequest(BaseModel):
    email: str = Field(..., min_length=1)
    allow_register: bool = Field(
        default=True,
        description="Если true и email новый — создать идентичность и отправить код (passwordless flow)",
    )
    turnstile_token: str | None = Field(default=None, description="Cloudflare Turnstile CAPTCHA token")


class LoginByCodeRequest(BaseModel):
    email: str = Field(..., min_length=1)
    code: str = Field(..., min_length=1)


class ConfirmPasswordResetRequest(BaseModel):
    email: str = Field(..., min_length=1)
    code: str = Field(..., min_length=1)
    password: NewPassword
    password_confirm: NewPassword


class LoginTelegramRequest(BaseModel):
    """Данные от Telegram Login Widget (кнопка «Войти через Telegram»)."""

    id: int = Field(..., description="Telegram user id (tg_id)")
    first_name: str = Field("")
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    auth_date: int = Field(..., description="Unix timestamp от Telegram")
    hash: str = Field(..., description="HMAC подпись для проверки на бэкенде")


class LinkTelegramRequest(BaseModel):
    """Данные от Telegram Login Widget — обязательны для доказательства владения аккаунтом при привязке."""

    id: int = Field(..., description="Telegram user id (tg_id)")
    first_name: str = Field("")
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    auth_date: int = Field(..., description="Unix timestamp от Telegram")
    hash: str = Field(..., description="HMAC подпись для проверки на бэкенде")


class IdentityAttachEmail(BaseModel):
    email: str = Field(..., min_length=1)


class LinkEmailSendCodeRequest(BaseModel):
    email: str = Field(..., min_length=1)


class LinkEmailConfirmRequest(BaseModel):
    email: str = Field(..., min_length=1)
    code: str = Field(..., min_length=1, max_length=16)


class IdentityAttachTelegram(BaseModel):
    tg_id: int = Field(...)


class IdentitySessionItem(BaseModel):
    id: str
    device_label: str | None = None
    ip: str | None = None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime | None = None
    is_current: bool = False


class IdentitySessionsResponse(BaseModel):
    sessions: list[IdentitySessionItem]
