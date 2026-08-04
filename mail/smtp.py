from email.message import EmailMessage

import aiosmtplib

from logger import logger
from settings.config import (
    EMAIL_FROM,
    EMAIL_SMTP_HOST,
    EMAIL_SMTP_PASSWORD,
    EMAIL_SMTP_PORT,
    EMAIL_SMTP_USER,
    PROJECT_NAME,
)


_SMTP_TIMEOUT_SEC = 30.0
_SMTP_VALIDATE_CERTS = True


def smtp_configured() -> bool:
    return bool(EMAIL_SMTP_HOST and (EMAIL_FROM or EMAIL_SMTP_USER))


def _get_email_template(key: str, default: str) -> str:
    """Читает шаблон из WEB_CONFIG (настраиваемый админом), fallback на default."""
    try:
        from core.settings.web_config import WEB_CONFIG

        val = WEB_CONFIG.get(key)
        return str(val).strip() if val else default
    except Exception:
        return default


def _render(template: str, **kwargs: str) -> str:
    try:
        return template.format(**kwargs)
    except (KeyError, ValueError):
        return template


def _smtp_kwargs() -> dict:
    kwargs: dict = {
        "hostname": EMAIL_SMTP_HOST,
        "port": EMAIL_SMTP_PORT,
        "username": EMAIL_SMTP_USER or None,
        "password": EMAIL_SMTP_PASSWORD or None,
        "timeout": _SMTP_TIMEOUT_SEC,
        "validate_certs": _SMTP_VALIDATE_CERTS,
    }
    if EMAIL_SMTP_PORT == 465:
        kwargs["use_tls"] = True
        kwargs["start_tls"] = False
    else:
        kwargs["use_tls"] = False
        kwargs["start_tls"] = True
    return kwargs


async def send_login_code_email(to_addr: str, code: str) -> None:
    if not smtp_configured():
        raise RuntimeError("smtp_not_configured")
    from_addr = EMAIL_FROM or EMAIL_SMTP_USER
    project = PROJECT_NAME
    subject = _render(
        _get_email_template("EMAIL_LOGIN_SUBJECT", "{project}: код для входа"), project=project, code=code
    )
    body = _render(_get_email_template("EMAIL_LOGIN_BODY", "Код для входа: {code}"), project=project, code=code)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{project} <{from_addr}>"
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        await aiosmtplib.send(msg, **_smtp_kwargs())
    except Exception as exc:
        logger.warning(f"[SMTP] Отправка кода входа на {to_addr} не удалась: {exc}")
        raise


async def send_support_reply_email(to_addr: str, ticket_ref: str, reply: str) -> None:
    if not smtp_configured():
        raise RuntimeError("smtp_not_configured")
    from_addr = EMAIL_FROM or EMAIL_SMTP_USER
    project = PROJECT_NAME
    subject = _render(
        _get_email_template("EMAIL_SUPPORT_REPLY_SUBJECT", "{project}: ответ поддержки"),
        project=project,
        ref=ticket_ref,
    )
    body = _render(
        _get_email_template(
            "EMAIL_SUPPORT_REPLY_BODY",
            "Поддержка ответила по вашему обращению {ref}:\n\n{reply}\n\nОткройте личный кабинет, чтобы продолжить диалог.",
        ),
        project=project,
        ref=ticket_ref,
        reply=reply,
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{project} <{from_addr}>"
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        await aiosmtplib.send(msg, **_smtp_kwargs())
    except Exception as exc:
        logger.warning(f"[SMTP] Отправка ответа поддержки на {to_addr} не удалась: {exc}")
        raise


async def send_password_reset_code_email(to_addr: str, code: str) -> None:
    if not smtp_configured():
        raise RuntimeError("smtp_not_configured")
    from_addr = EMAIL_FROM or EMAIL_SMTP_USER
    project = PROJECT_NAME
    subject = _render(_get_email_template("EMAIL_RESET_SUBJECT", "{project}: сброс пароля"), project=project, code=code)
    body = _render(_get_email_template("EMAIL_RESET_BODY", "Код для сброса пароля: {code}"), project=project, code=code)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{project} <{from_addr}>"
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        await aiosmtplib.send(msg, **_smtp_kwargs())
    except Exception as exc:
        logger.warning(f"[SMTP] Отправка кода сброса пароля на {to_addr} не удалась: {exc}")
        raise


async def send_email_verify_code_email(to_addr: str, code: str) -> None:
    if not smtp_configured():
        raise RuntimeError("smtp_not_configured")
    from_addr = EMAIL_FROM or EMAIL_SMTP_USER
    project = PROJECT_NAME
    subject = _render(
        _get_email_template("EMAIL_VERIFY_SUBJECT", "{project}: подтверждение email"), project=project, code=code
    )
    body = _render(
        _get_email_template("EMAIL_VERIFY_BODY", "Код подтверждения email: {code}"), project=project, code=code
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{project} <{from_addr}>"
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        await aiosmtplib.send(msg, **_smtp_kwargs())
    except Exception as exc:
        logger.warning(f"[SMTP] Отправка кода подтверждения email на {to_addr} не удалась: {exc}")
        raise


async def send_email_link_code_email(to_addr: str, code: str) -> None:
    if not smtp_configured():
        raise RuntimeError("smtp_not_configured")
    from_addr = EMAIL_FROM or EMAIL_SMTP_USER
    project = PROJECT_NAME
    subject = _render(
        _get_email_template("EMAIL_LINK_SUBJECT", "{project}: подтверждение привязки email"), project=project, code=code
    )
    body = _render(_get_email_template("EMAIL_LINK_BODY", "Код для привязки email: {code}"), project=project, code=code)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{project} <{from_addr}>"
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        await aiosmtplib.send(msg, **_smtp_kwargs())
    except Exception as exc:
        logger.warning(f"[SMTP] Отправка кода привязки email на {to_addr} не удалась: {exc}")
        raise
