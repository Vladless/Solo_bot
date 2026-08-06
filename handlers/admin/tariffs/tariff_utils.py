from aiogram.types import InlineKeyboardMarkup

from database.models import Tariff
from services.tariffs.visibility import describe_visibility

from ..panel.headers import quote
from .keyboard import build_single_tariff_kb


MAX_TARIFF_NAME_LENGTH = 40
MAX_SUBGROUP_TITLE_LENGTH = 40


def validate_tariff_name(name: str) -> tuple[bool, str]:
    if len(name) > MAX_TARIFF_NAME_LENGTH:
        return False, f"Название тарифа слишком длинное. Максимум {MAX_TARIFF_NAME_LENGTH} символов."
    return True, ""


def validate_subgroup_title(title: str) -> tuple[bool, str]:
    if len(title) > MAX_SUBGROUP_TITLE_LENGTH:
        return False, f"Название подгруппы слишком длинное. Максимум {MAX_SUBGROUP_TITLE_LENGTH} символов."
    return True, ""


def tariff_to_dict(tariff) -> dict:
    if isinstance(tariff, dict):
        return tariff
    return {
        "id": tariff.id,
        "name": tariff.name,
        "price_rub": tariff.price_rub,
        "group_code": tariff.group_code,
        "subgroup_title": tariff.subgroup_title,
        "sort_order": tariff.sort_order,
    }


async def check_tariff_price_monotonicity(session, tariff) -> list[str]:
    """Мягкая проверка прайса на монотонность.

    В рамках одной длительности тариф, дающий не меньше ресурсов (устройства и
    трафик), не должен стоить дешевле. Иначе при смене тарифа клиент сможет
    получить больше за меньшие деньги. Возвращает список предупреждений (пусто — ок).
    """
    from sqlalchemy import select

    group = getattr(tariff, "group_code", None)
    price = int(getattr(tariff, "price_rub", 0) or 0)
    duration = int(getattr(tariff, "duration_days", 0) or 0)
    if not group or price <= 0 or duration <= 0:
        return []

    inf = float("inf")

    def _res(value) -> float:
        return inf if value in (None, 0) else int(value)

    def _lbl(value: float) -> str:
        return "∞" if value == inf else str(int(value))

    dev = _res(getattr(tariff, "device_limit", None))
    trf = _res(getattr(tariff, "traffic_limit", None))

    result = await session.execute(
        select(Tariff).where(
            Tariff.group_code == group,
            Tariff.is_active.is_(True),
            Tariff.id != getattr(tariff, "id", None),
            Tariff.duration_days == duration,
        )
    )
    name = getattr(tariff, "name", "")
    warnings: list[str] = []
    for other in result.scalars().all():
        o_price = int(other.price_rub or 0)
        if o_price <= 0:
            continue
        o_dev = _res(other.device_limit)
        o_trf = _res(other.traffic_limit)
        if dev >= o_dev and trf >= o_trf and price < o_price:
            warnings.append(
                f"«{name}» ({price} ₽, {_lbl(dev)} устр / {_lbl(trf)} ГБ) — ресурсов не меньше, "
                f"чем у «{other.name}» ({o_price} ₽, {_lbl(o_dev)} / {_lbl(o_trf)}), но цена ниже"
            )
        elif o_dev >= dev and o_trf >= trf and o_price < price:
            warnings.append(
                f"«{other.name}» — {o_price} ₽ за {_lbl(o_dev)} устр. и {_lbl(o_trf)} ГБ.\n"
                f"У «{name}» ресурсов не больше, а цена выше: {price} ₽ "
                f"за {_lbl(dev)} и {_lbl(trf)}."
            )
    return warnings


def format_price_monotonicity_warning(warnings: list[str]) -> str:
    """Блок предупреждения для добавления к карточке тарифа. Пусто — если нет проблем."""
    if not warnings:
        return ""
    return "\n\n" + "\n\n".join([
        "⚠️ <b>Возможная ошибка в прайсе</b>",
        quote("\n\n".join(warnings)),
        quote("При смене тарифа клиент получит больше ресурсов за меньшие деньги. Проверьте цены в группе."),
    ])


def render_tariff_card(tariff: Tariff) -> tuple[str, InlineKeyboardMarkup]:
    traffic_text = f"{tariff.traffic_limit} ГБ" if tariff.traffic_limit else "Безлимит"
    device_text = f"{tariff.device_limit}" if tariff.device_limit is not None else "Безлимит"
    sort_order = getattr(tariff, "sort_order", 1)
    vless_text = "Да" if getattr(tariff, "vless", False) else "Нет"
    configurable = bool(getattr(tariff, "configurable", False))
    configurable_text = "Включен" if configurable else "Выключен"
    external_squad_text = getattr(tariff, "external_squad", None) or "Не задан"
    cooldown_days = int(getattr(tariff, "cooldown_days", 0) or 0)
    cooldown_text = f"раз в {cooldown_days} дн." if cooldown_days > 0 else "Без задержки"
    visibility_text = describe_visibility(getattr(tariff, "visibility_rules", None))
    description_raw = getattr(tariff, "description", None)
    description_safe = (
        description_raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if description_raw
        else "Не задано"
    )

    text = (
        f"<b>📄 Тариф: {tariff.name}</b>\n"
        f"🆔 ID: <code>{tariff.id}</code>\n\n"
        f"📁 Группа: <code>{tariff.group_code}</code>\n"
        f"📅 Длительность: <b>{tariff.duration_days} дней</b>\n"
        f"💰 Стоимость: <b>{tariff.price_rub}₽</b>\n"
        f"📦 Трафик: <b>{traffic_text}</b>\n"
        f"📱 Устройств: <b>{device_text}</b>\n"
        f"🔗 VLESS: <b>{vless_text}</b>\n"
        f"⚙️ Конфигуратор: <b>{configurable_text}</b>\n"
        f"Внешний сквад: <b>{external_squad_text}</b>\n"
        f"⏳ Задержка покупки: <b>{cooldown_text}</b>\n"
        f"👁 Видимость: <b>{visibility_text}</b>\n"
        f"📄 Описание: {description_safe}\n"
        f"🔢 Позиция: <b>{sort_order}</b>\n"
        f"{'✅ Активен' if tariff.is_active else '⛔ Отключен'}"
    )

    return text, build_single_tariff_kb(tariff.id, tariff.group_code, configurable=configurable)
