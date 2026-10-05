from datetime import datetime


try:
    from modules.partner_program.settings import PARTNER_BONUS_PERCENTAGES
except Exception:
    PARTNER_BONUS_PERCENTAGES = {1: 0.0}


def parse_percent(value: float) -> float | None:
    """Процент партнёра в шкале 0-100: доля 0-1 разворачивается, мусор отсекается."""
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 <= val <= 1.0:
        val *= 100.0
    if 0.0 <= val <= 100.0:
        return val
    return None


def default_partner_percent() -> float:
    """Процент первого уровня из настроек партнёрской программы."""
    try:
        return float(PARTNER_BONUS_PERCENTAGES.get(1, 0.0)) * 100.0
    except Exception:
        return 0.0


def row_dt_iso(value) -> str | None:
    """Дата строки в ISO, если это дата."""
    if isinstance(value, datetime):
        return value.isoformat()
    return None
