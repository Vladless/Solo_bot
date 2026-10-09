import re
import secrets

from mail import send_email_link_code_email, smtp_configured
from services.errors import ServiceError
from settings.config import REDIS_URL
from utils.web_email_codes import EmailCodeFlow, normalize_email


bot_email_link_codes = EmailCodeFlow("bot_email_link", ip_max_sends=20)


def _challenge_key(identity_id: str, email: str, request_id: str) -> str:
    """Связывает код с аккаунтом и адресом почты."""
    return f"{identity_id}:{request_id}:{normalize_email(email)}"


def email_binding_codes_configured() -> bool:
    """Проверяет наличие настроек почты и хранилища кодов."""
    return smtp_configured() and bool(str(REDIS_URL or "").strip())


async def email_binding_codes_available() -> bool:
    """Проверяет доступность отправки и хранения кодов."""
    return email_binding_codes_configured() and await bot_email_link_codes.redis_ready()


async def send_email_binding_code(identity_id: str, email: str, request_id: str) -> None:
    """Отправляет код привязки с ограничением повторных запросов."""
    email = normalize_email(email)
    if not await email_binding_codes_available():
        raise ServiceError("Отправка кода временно недоступна. Попробуйте позже.", "unavailable")
    if not await bot_email_link_codes.try_acquire_cooldown(email):
        raise ServiceError("Код уже отправлен. Повторная отправка доступна через минуту.", "cooldown")
    if not await bot_email_link_codes.try_consume_ip_budget(identity_id):
        await bot_email_link_codes.release_cooldown(email)
        raise ServiceError("Слишком много запросов. Попробуйте позже.", "rate_limit")
    if not await bot_email_link_codes.try_consume_email_send_budget(email):
        await bot_email_link_codes.release_cooldown(email)
        raise ServiceError("Слишком много писем на этот адрес. Попробуйте позже.", "rate_limit")
    challenge = _challenge_key(identity_id, email, request_id)
    code = "".join(secrets.choice("0123456789") for _ in range(6))
    if not await bot_email_link_codes.store_code(challenge, code):
        await bot_email_link_codes.release_cooldown(email)
        raise ServiceError("Не удалось сохранить код. Попробуйте позже.", "unavailable")
    try:
        await send_email_link_code_email(email, code)
    except Exception:
        await bot_email_link_codes.release_cooldown(email)
        await bot_email_link_codes.delete_code(challenge)
        raise ServiceError(
            "Не удалось отправить письмо. Проверьте адрес и попробуйте ещё раз.", "send_failed"
        ) from None


async def confirm_email_binding_code(identity_id: str, email: str, request_id: str, code: str) -> bool:
    """Проверяет и однократно погашает код привязки."""
    if not await bot_email_link_codes.redis_ready():
        raise ServiceError("Проверка кода временно недоступна. Попробуйте позже.", "unavailable")
    challenge = _challenge_key(identity_id, email, request_id)
    if not await bot_email_link_codes.try_consume_verify_budget(f"{identity_id}:{normalize_email(email)}"):
        raise ServiceError("Слишком много попыток. Попробуйте позже.", "rate_limit")
    if re.fullmatch(r"[0-9]{6}", code) is None:
        return False
    return await bot_email_link_codes.verify_and_consume_code(challenge, code)


async def discard_email_binding_code(identity_id: str, email: str, request_id: str) -> None:
    """Удаляет код отменённой привязки."""
    await bot_email_link_codes.delete_code(_challenge_key(identity_id, email, request_id))
