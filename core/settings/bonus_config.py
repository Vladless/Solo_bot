from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting
from database.settings_cache import settings_cache

from ..defaults import DEFAULT_BONUS_CONFIG
from .runtime_sync import publish_runtime_config, register_runtime_config


BONUS_CONFIG: dict[str, Any] = DEFAULT_BONUS_CONFIG.copy()
BONUS_SETTING_KEY = "BONUS_CONFIG"
register_runtime_config(BONUS_SETTING_KEY, BONUS_CONFIG)

BONUS_MODES: tuple[str, ...] = ("fixed", "range", "streak")


BONUS_SECTION_SLUG = "bonus"
BONUS_SECTION_TITLE = "Бонусы"
BONUS_SECTION_DESCRIPTION = (
    "Ежедневный бонус на баланс в личном кабинете на сайте: сколько давать, как часто и кому. "
    "В боте такой кнопки нет — бонус работает только в веб-кабинете."
)

BONUS_TITLES: dict[str, str] = {
    "DAILY_BONUS_ENABLED": "Ежедневный бонус",
    "DAILY_BONUS_MODE": "Размер бонуса",
    "DAILY_BONUS_AMOUNT": "Одинаковая сумма, ₽",
    "DAILY_BONUS_MIN_AMOUNT": "Случайная сумма: от, ₽",
    "DAILY_BONUS_MAX_AMOUNT": "Случайная сумма: до, ₽",
    "DAILY_BONUS_STREAK_LADDER": "Суммы за серию дней, ₽",
    "DAILY_BONUS_STREAK_RESTART": "Начинать серию заново после последней суммы",
    "DAILY_BONUS_STREAK_KEEP_HOURS": "Серия не рвётся, часов",
    "DAILY_BONUS_COOLDOWN_HOURS": "Ждать между бонусами, часов",
    "DAILY_BONUS_CLAIMS_PER_PERIOD": "Бонусов за период",
    "DAILY_BONUS_PERIOD_HOURS": "Длина периода, часов",
    "DAILY_BONUS_REQUIRE_SUBSCRIPTION": "Только клиентам с подпиской",
    "DAILY_BONUS_MIN_ACCOUNT_AGE_HOURS": "Аккаунт должен быть старше, часов",
}

BONUS_HINTS: dict[str, str] = {
    "DAILY_BONUS_ENABLED": (
        "Включает в кабинете карточку «Забрать бонус»: клиент нажимает кнопку и деньги "
        "падают ему на баланс. Выключено — карточка не показывается вообще."
    ),
    "DAILY_BONUS_MODE": (
        "Как считать сумму: одинаковая каждый раз, случайная в пределах «от — до» или растущая за серию дней подряд."
    ),
    "DAILY_BONUS_AMOUNT": (
        "Сколько давать за один бонус, если размер «одинаковый каждый раз». "
        "Ею же подстрахуемся, если суммы за серию не заданы."
    ),
    "DAILY_BONUS_MIN_AMOUNT": "Нижняя граница случайной суммы. Нужна только для размера «случайный».",
    "DAILY_BONUS_MAX_AMOUNT": "Верхняя граница случайной суммы. Нужна только для размера «случайный».",
    "DAILY_BONUS_STREAK_LADDER": (
        "Через запятую: сколько дать в 1-й день серии, во 2-й, в 3-й и так далее. "
        "Например «5, 7, 10, 15»: на четвёртый день и дальше будет по 15 ₽. "
        "Нужно только для размера «растёт за серию»."
    ),
    "DAILY_BONUS_STREAK_RESTART": (
        "Включено — после последней суммы серия начинается с первого дня, и клиент снова идёт по лестнице снизу. "
        "Выключено — дойдя до конца, клиент получает последнюю сумму сколько угодно раз. "
        "Работает только для размера «растёт за серию»."
    ),
    "DAILY_BONUS_STREAK_KEEP_HOURS": (
        "Сколько времени после бонуса у клиента есть, чтобы забрать следующий и продолжить серию. "
        "Пропустил — серия начинается с первого дня. При суточном кулдауне разумно 48. "
        "0 — серия не сбрасывается никогда."
    ),
    "DAILY_BONUS_COOLDOWN_HOURS": (
        "Через сколько часов после получения кнопка снова станет активной. 24 — «раз в сутки». "
        "До этого клиент видит таймер до следующего бонуса."
    ),
    "DAILY_BONUS_CLAIMS_PER_PERIOD": (
        "Сколько раз бонус можно забрать внутри периода из следующего поля. Обычно 1: один бонус в сутки."
    ),
    "DAILY_BONUS_PERIOD_HOURS": (
        "Окно, в котором считается лимит из предыдущего поля. 24 часа и лимит 1 — "
        "это «не больше одного бонуса в сутки»."
    ),
    "DAILY_BONUS_REQUIRE_SUBSCRIPTION": (
        "Включено — бонус доступен только клиентам с активной подпиской. Выключено — всем, в том числе без подписки."
    ),
    "DAILY_BONUS_MIN_ACCOUNT_AGE_HOURS": (
        "Защита от свежих пустых регистраций: 24 — бонус откроется через сутки после регистрации. 0 — доступен сразу."
    ),
}

BONUS_MODE_OPTIONS: list[dict[str, str]] = [
    {"value": "fixed", "label": "Одинаковая сумма каждый раз"},
    {"value": "range", "label": "Случайная сумма в диапазоне"},
    {"value": "streak", "label": "Растёт за серию дней подряд"},
]


@dataclass(frozen=True)
class DailyBonusRules:
    enabled: bool
    mode: str
    amount: float
    min_amount: float
    max_amount: float
    ladder: tuple[float, ...]
    streak_restart: bool
    streak_keep_hours: int
    cooldown_hours: int
    claims_per_period: int
    period_hours: int
    require_subscription: bool
    min_account_age_hours: int


def _to_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result >= 0 else default


def _to_int(value: Any, default: int) -> int:
    try:
        result = int(float(value))
    except (TypeError, ValueError):
        return default
    return result if result >= 0 else default


def parse_ladder(raw: Any) -> tuple[float, ...]:
    """Лестница серии: «5, 7, 10» или список чисел → суммы по дням серии."""
    if isinstance(raw, list | tuple):
        parts: list[str] = [str(item) for item in raw]
    else:
        parts = str(raw or "").replace(";", ",").split(",")
    amounts: list[float] = []
    for part in parts:
        cleaned = part.strip()
        if not cleaned:
            continue
        try:
            value = float(cleaned)
        except ValueError:
            continue
        if value > 0:
            amounts.append(round(value, 2))
    return tuple(amounts)


def resolve_daily_bonus_rules() -> DailyBonusRules:
    """Правила ежедневного бонуса из BONUS_CONFIG, приведённые к безопасным значениям."""
    mode = str(BONUS_CONFIG.get("DAILY_BONUS_MODE") or "fixed").strip().lower()
    if mode not in BONUS_MODES:
        mode = "fixed"
    amount = _to_float(BONUS_CONFIG.get("DAILY_BONUS_AMOUNT"), 0.0)
    min_amount = _to_float(BONUS_CONFIG.get("DAILY_BONUS_MIN_AMOUNT"), 0.0)
    max_amount = _to_float(BONUS_CONFIG.get("DAILY_BONUS_MAX_AMOUNT"), min_amount)
    if max_amount < min_amount:
        min_amount, max_amount = max_amount, min_amount
    ladder = parse_ladder(BONUS_CONFIG.get("DAILY_BONUS_STREAK_LADDER"))
    return DailyBonusRules(
        enabled=bool(BONUS_CONFIG.get("DAILY_BONUS_ENABLED", False)),
        mode=mode,
        amount=round(amount, 2),
        min_amount=round(min_amount, 2),
        max_amount=round(max_amount, 2),
        ladder=ladder,
        streak_restart=bool(BONUS_CONFIG.get("DAILY_BONUS_STREAK_RESTART", True)),
        streak_keep_hours=_to_int(BONUS_CONFIG.get("DAILY_BONUS_STREAK_KEEP_HOURS"), 48),
        cooldown_hours=max(1, _to_int(BONUS_CONFIG.get("DAILY_BONUS_COOLDOWN_HOURS"), 24)),
        claims_per_period=max(1, _to_int(BONUS_CONFIG.get("DAILY_BONUS_CLAIMS_PER_PERIOD"), 1)),
        period_hours=max(1, _to_int(BONUS_CONFIG.get("DAILY_BONUS_PERIOD_HOURS"), 24)),
        require_subscription=bool(BONUS_CONFIG.get("DAILY_BONUS_REQUIRE_SUBSCRIPTION", False)),
        min_account_age_hours=_to_int(BONUS_CONFIG.get("DAILY_BONUS_MIN_ACCOUNT_AGE_HOURS"), 0),
    )


def is_daily_bonus_enabled() -> bool:
    return bool(BONUS_CONFIG.get("DAILY_BONUS_ENABLED", False))


async def load_bonus_config(session: AsyncSession) -> None:
    stmt = select(Setting).where(Setting.key == BONUS_SETTING_KEY)
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        bonus_config = DEFAULT_BONUS_CONFIG.copy()
        setting = Setting(
            key=BONUS_SETTING_KEY,
            value=bonus_config,
            description="Бонусы пользователям",
        )
        session.add(setting)
    else:
        stored = setting.value or {}
        bonus_config = DEFAULT_BONUS_CONFIG.copy()
        bonus_config.update(stored)
        setting.value = bonus_config

    BONUS_CONFIG.clear()
    BONUS_CONFIG.update(bonus_config)
    await session.flush()


async def update_bonus_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    stmt = select(Setting).where(Setting.key == BONUS_SETTING_KEY)
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = Setting(
            key=BONUS_SETTING_KEY,
            value=new_values,
            description="Бонусы пользователям",
        )
        session.add(setting)
    else:
        setting.value = new_values

    await session.commit()

    bonus_config = DEFAULT_BONUS_CONFIG.copy()
    bonus_config.update(new_values)

    BONUS_CONFIG.clear()
    BONUS_CONFIG.update(bonus_config)
    settings_cache.update(BONUS_SETTING_KEY, bonus_config)
    await publish_runtime_config(BONUS_SETTING_KEY, bonus_config)
