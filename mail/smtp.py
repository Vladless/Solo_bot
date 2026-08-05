import re

from datetime import datetime
from email.message import EmailMessage
from html import escape as html_escape

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


def _apply_sender(msg: EmailMessage) -> None:
    addr = EMAIL_FROM or EMAIL_SMTP_USER
    name = _get_email_template("EMAIL_FROM_NAME", PROJECT_NAME)
    msg["From"] = f"{name} <{addr}>"
    reply_to = _get_email_template("EMAIL_REPLY_TO", "").strip()
    if reply_to:
        msg["Reply-To"] = reply_to


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
    project = PROJECT_NAME
    subject = _render(
        _get_email_template("EMAIL_LOGIN_SUBJECT", "{project}: код для входа"), project=project, code=code
    )
    body = _render(_get_email_template("EMAIL_LOGIN_BODY", "Код для входа: {code}"), project=project, code=code)
    msg = EmailMessage()
    msg["Subject"] = subject
    _apply_sender(msg)
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
    _apply_sender(msg)
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
    project = PROJECT_NAME
    subject = _render(_get_email_template("EMAIL_RESET_SUBJECT", "{project}: сброс пароля"), project=project, code=code)
    body = _render(_get_email_template("EMAIL_RESET_BODY", "Код для сброса пароля: {code}"), project=project, code=code)
    msg = EmailMessage()
    msg["Subject"] = subject
    _apply_sender(msg)
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
    project = PROJECT_NAME
    subject = _render(
        _get_email_template("EMAIL_VERIFY_SUBJECT", "{project}: подтверждение email"), project=project, code=code
    )
    body = _render(
        _get_email_template("EMAIL_VERIFY_BODY", "Код подтверждения email: {code}"), project=project, code=code
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    _apply_sender(msg)
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        await aiosmtplib.send(msg, **_smtp_kwargs())
    except Exception as exc:
        logger.warning(f"[SMTP] Отправка кода подтверждения email на {to_addr} не удалась: {exc}")
        raise


DEFAULT_BROADCAST_TEMPLATE = (
    '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
    '<body style="margin:0;padding:24px 0;background:#f4f5f7;'
    'font-family:Arial,Helvetica,sans-serif;">'
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">'
    '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
    'style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;">'
    '<tr><td style="background:#111827;padding:20px 28px;">'
    '<span style="color:#ffffff;font-size:18px;font-weight:bold;">{project}</span></td></tr>'
    "{image}"
    '<tr><td style="padding:28px;color:#1f2937;font-size:15px;line-height:1.6;">{content}</td></tr>'
    "{cta}"
    '<tr><td style="background:#f9fafb;padding:18px 28px;color:#9ca3af;font-size:12px;">'
    "© {year} {project}</td></tr>"
    "</table></td></tr></table></body></html>"
)


def _html_to_text(html: str) -> str:
    text = re.sub(r'<tg-emoji emoji-id="[^"]*">([^<]*)</tg-emoji>', r"\1", html)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&nbsp;", " ")
    return text.strip()


def _telegram_html_to_email(html: str) -> str:
    body = re.sub(r'<tg-emoji emoji-id="[^"]*">([^<]*)</tg-emoji>', r"\1", html)
    body = body.replace("\r\n", "\n").replace("\n", "<br>")
    return body


def build_broadcast_email(text_html: str, image_url: str | None = None) -> tuple[str, str, str]:
    project = PROJECT_NAME
    plain = _html_to_text(text_html)
    first_line = plain.split("\n", 1)[0].strip()
    title = first_line or project
    subject = _render(_get_email_template("EMAIL_BROADCAST_SUBJECT", "{project}: {title}"), project=project, title=title)

    site_url = ""
    try:
        from core.settings.web_config import get_site_url

        site_url = get_site_url()
    except Exception:
        site_url = ""

    image_block = (
        f'<tr><td style="padding:0;"><img src="{html_escape(image_url)}" alt="" '
        f'style="width:100%;display:block;"></td></tr>'
        if image_url
        else ""
    )
    cta_block = (
        f'<tr><td style="padding:0 28px 28px;"><a href="{html_escape(site_url)}" '
        f'style="display:inline-block;background:#2563eb;color:#ffffff;text-decoration:none;'
        f'padding:12px 22px;border-radius:8px;font-size:14px;">Открыть личный кабинет</a></td></tr>'
        if site_url
        else ""
    )

    template = _get_email_template("EMAIL_BROADCAST_TEMPLATE", DEFAULT_BROADCAST_TEMPLATE)
    html = (
        template.replace("{project}", html_escape(project))
        .replace("{image}", image_block)
        .replace("{cta}", cta_block)
        .replace("{site_url}", html_escape(site_url))
        .replace("{year}", str(datetime.now().year))
        .replace("{content}", _telegram_html_to_email(text_html))
    )
    return subject, html, plain


async def send_broadcast_email(to_addr: str, subject: str, html_body: str, text_body: str) -> None:
    if not smtp_configured():
        raise RuntimeError("smtp_not_configured")
    msg = EmailMessage()
    msg["Subject"] = subject
    _apply_sender(msg)
    msg["To"] = to_addr
    msg.set_content(text_body or " ")
    msg.add_alternative(html_body, subtype="html")
    await aiosmtplib.send(msg, **_smtp_kwargs())


async def send_email_link_code_email(to_addr: str, code: str) -> None:
    if not smtp_configured():
        raise RuntimeError("smtp_not_configured")
    project = PROJECT_NAME
    subject = _render(
        _get_email_template("EMAIL_LINK_SUBJECT", "{project}: подтверждение привязки email"), project=project, code=code
    )
    body = _render(_get_email_template("EMAIL_LINK_BODY", "Код для привязки email: {code}"), project=project, code=code)
    msg = EmailMessage()
    msg["Subject"] = subject
    _apply_sender(msg)
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        await aiosmtplib.send(msg, **_smtp_kwargs())
    except Exception as exc:
        logger.warning(f"[SMTP] Отправка кода привязки email на {to_addr} не удалась: {exc}")
        raise
