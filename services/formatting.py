from __future__ import annotations

from settings.config import USERNAME_BOT


def get_plural_form(num: int, form1: str, form2: str, form3: str) -> str:
    n = abs(num) % 100
    if 10 < n < 20:
        return form3
    return {1: form1, 2: form2, 3: form2, 4: form2}.get(n % 10, form3)


def format_months(months: int) -> str:
    if months <= 0:
        return "0 месяцев"
    return f"{months} {get_plural_form(months, 'месяц', 'месяца', 'месяцев')}"


def format_days(days: int) -> str:
    if days <= 0:
        return "0 дней"
    return f"{days} {get_plural_form(days, 'день', 'дня', 'дней')}"


def format_duration_days(days: int) -> str:
    return format_months(days // 30) if days % 30 == 0 else format_days(days)


def get_referral_link(user_id: int) -> str:
    return f"https://t.me/{USERNAME_BOT}?start=referral_{user_id}"


def get_telegram_gift_link(gift_id: str) -> str:
    return f"https://telegram.me/{USERNAME_BOT}?start=gift_{gift_id}"


def get_gift_link(user_id: int, gift_id: str) -> str:
    return get_telegram_gift_link(gift_id)


def get_site_gift_link(gift_id: str) -> str:
    from core.settings.web_config import get_site_url

    site_url = get_site_url()
    return f"{site_url}/gift/{gift_id}"


def build_renewal_done_text(
    tariff_name: str = "",
    traffic_limit: int = 0,
    device_limit: int = 0,
    expiry_date: str = "",
) -> str:
    """Собирает сообщение об успешном продлении по шаблону из файла текстов."""
    from handlers.utils import render_text
    from settings.texts import RENEWAL_DONE_TEXT, UNLIMITED_ROW_VALUE

    return render_text(
        RENEWAL_DONE_TEXT,
        tariff_name=tariff_name,
        traffic=f"{traffic_limit} ГБ" if traffic_limit else UNLIMITED_ROW_VALUE,
        devices=device_limit if device_limit else UNLIMITED_ROW_VALUE,
        expiry=expiry_date.replace(" года", "").strip(),
    )
