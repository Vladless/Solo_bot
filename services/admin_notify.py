from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pytz import timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.client_origin import (
    INVITE_PARTNER,
    INVITE_REFERRAL,
    INVITE_UTM,
    ORIGIN_API,
    ORIGIN_BOT,
    ORIGIN_PWA,
    ORIGIN_WEB,
    ORIGIN_WEBAPP,
    client_invite,
    client_origin,
)
from core.executor import spawn
from database.models import Identity, Referral, User
from handlers.admin.users.keyboard import AdminUserEditorCallback
from logger import logger


MOSCOW_TZ = timezone("Europe/Moscow")


def _now_label() -> str:
    """Момент события по московскому времени: дата и время до секунд."""
    return datetime.now(MOSCOW_TZ).strftime("%d.%m.%y %H:%M:%S")


ORIGIN_LABELS = {
    ORIGIN_BOT: "бот",
    ORIGIN_WEB: "сайт",
    ORIGIN_WEBAPP: "Telegram WebApp",
    ORIGIN_PWA: "приложение сайта",
    ORIGIN_API: "неизвестно",
}


def origin_label(origin: object) -> str:
    """Название канала для админа: бот, сайт, WebApp или приложение."""
    return ORIGIN_LABELS.get(str(origin or "").strip().lower(), "неизвестно")


INVITE_LABELS = {
    INVITE_REFERRAL: "реферал",
    INVITE_PARTNER: "партнёр",
    INVITE_UTM: "метка",
}

INVITE_DIRECT = "прямой запуск"


def _who(username: object, tg_id: object, user_id: object) -> str:
    """Как назвать пригласившего: ник, иначе telegram, иначе номер клиента."""
    nick = str(username or "").strip().lstrip("@")
    if nick:
        return f"@{nick}"
    if tg_id:
        return f"tg {tg_id}"
    return f"id {user_id}"


async def _partner_referrer(session: AsyncSession, tg_id: int) -> object | None:
    try:
        from modules.partner_program.models import Partner
    except Exception:
        return None
    return await session.scalar(select(Partner.partner_tg_id).where(Partner.joined_tg_id == int(tg_id)).limit(1))


async def resolve_attribution(session: AsyncSession, card: dict[str, object]) -> dict[str, object]:
    """Откуда клиент: реферал, партнёр, рекламная метка или прямой запуск.

    Сначала смотрим записи в базе, а если их ещё нет — метку приглашения текущего запроса:
    реферал и партнёр записываются уже после создания клиента.
    """
    user_id = int(card["id"])
    tg_id = card.get("tg_id")

    referrer = (
        await session.execute(
            select(User.id, User.tg_id, User.username)
            .join(Referral, Referral.referrer_user_id == User.id)
            .where(Referral.referred_user_id == user_id)
            .limit(1)
        )
    ).first()
    if referrer is not None:
        return {"kind": INVITE_REFERRAL, "who": _who(referrer.username, referrer.tg_id, referrer.id)}

    if tg_id:
        partner_tg = await _partner_referrer(session, int(tg_id))
        if partner_tg:
            partner = (
                await session.execute(
                    select(User.id, User.tg_id, User.username).where(User.tg_id == int(partner_tg)).limit(1)
                )
            ).first()
            who = _who(partner.username, partner.tg_id, partner.id) if partner else f"tg {partner_tg}"
            return {"kind": INVITE_PARTNER, "who": who}

    invite = client_invite()
    if invite is not None:
        kind, ref = invite
        if kind == INVITE_UTM:
            return {"kind": INVITE_UTM, "who": None}
        inviter = (
            (
                await session.execute(select(User.id, User.tg_id, User.username).where(User.tg_id == int(ref)).limit(1))
            ).first()
            if ref.lstrip("-").isdigit()
            else None
        )
        who = _who(inviter.username, inviter.tg_id, inviter.id) if inviter else f"tg {ref}"
        return {"kind": kind, "who": who}

    if card.get("source_code"):
        return {"kind": INVITE_UTM, "who": None}

    return {"kind": None, "who": None}


def _notifications_enabled(key: str) -> bool:
    from core.bootstrap import NOTIFICATIONS_CONFIG

    return bool((NOTIFICATIONS_CONFIG or {}).get(key, False))


def _site() -> tuple[bool, str]:
    from core.bootstrap import WEB_CONFIG

    config = WEB_CONFIG or {}
    url = str(config.get("SITE_URL") or "").strip().rstrip("/")
    return bool(config.get("WEB_ENABLED", False)) and bool(url), url


def build_client_keyboard(user_id: int, client_ref: int | None) -> InlineKeyboardMarkup:
    """Переходы к клиенту: карточка в боте и та же карточка на сайте.

    Кнопка сайта появляется, только когда сайт включён и его адрес задан: иначе она ведёт в пустоту.
    """
    rows = [
        [
            InlineKeyboardButton(
                text="Открыть в боте",
                callback_data=AdminUserEditorCallback(action="users_editor", user_id=user_id).pack(),
            )
        ]
    ]
    site_enabled, site_url = _site()
    if site_enabled:
        ref = client_ref if client_ref is not None else user_id
        rows.append([InlineKeyboardButton(text="Открыть на сайте", url=f"{site_url}/admin/users?ref={ref}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def load_client_card(session: AsyncSession, user_id: int) -> dict[str, object]:
    """Данные клиента для уведомления: ник, почта, telegram и рекламная метка."""
    row = (
        await session.execute(
            select(User.id, User.tg_id, User.username, User.source_code, Identity.email, Identity.signup_origin)
            .outerjoin(Identity, Identity.id == User.identity_id)
            .where(User.id == int(user_id))
            .limit(1)
        )
    ).first()
    if row is None:
        return {
            "id": int(user_id),
            "tg_id": None,
            "username": None,
            "source_code": None,
            "email": None,
            "signup_origin": None,
        }
    return {
        "id": int(row.id),
        "tg_id": int(row.tg_id) if row.tg_id is not None and row.tg_id > 0 else None,
        "username": row.username,
        "source_code": row.source_code,
        "email": row.email,
        "signup_origin": row.signup_origin,
    }


def _money(amount: float) -> str:
    """Сумма без лишних нулей: 609 ₽, 609.50 ₽."""
    value = float(amount)
    return f"{value:.0f} ₽" if value == int(value) else f"{value:.2f} ₽"


def _row(*parts: str) -> str:
    """Строка уведомления: заполненные части через точку-разделитель."""
    return " · ".join(part for part in parts if part)


def _client_row(card: dict[str, object], *, site_enabled: bool) -> str:
    """Кто клиент: ник, наш номер, telegram и почта — каждый контакт кликабельный.

    Почта бывает только у клиента с сайта, поэтому без сайта её в строке нет.
    """
    username = str(card.get("username") or "").strip().lstrip("@")
    email = str(card.get("email") or "").strip()
    tg_id = card.get("tg_id")
    return "👤 " + _row(
        f'<a href="https://t.me/{username}">@{username}</a>' if username else "",
        f"клиент №{card.get('id')}",
        f'<a href="tg://user?id={tg_id}">tg {tg_id}</a>' if tg_id else "",
        f'<a href="mailto:{email}">{email}</a>' if site_enabled and email else "",
    )


async def _send_to_admins(text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        from settings.config import ADMIN_ID
    except Exception:
        return
    if not ADMIN_ID:
        return
    try:
        from bot import bot
    except Exception:
        logger.debug("[AdminNotify] bot недоступен")
        return
    for admin_id in ADMIN_ID:
        try:
            await bot.send_message(admin_id, text, reply_markup=markup)
        except Exception as exc:
            logger.warning("[AdminNotify] не отправлено админу {}: {}", admin_id, exc)


def _origin_row(card: dict[str, object], *, origin: object, attribution: dict[str, object] | None) -> str:
    """Откуда клиент: вид привлечения с пригласившим или меткой и канал входа."""
    kind = str((attribution or {}).get("kind") or "")
    who = str((attribution or {}).get("who") or "")
    source = str(card.get("source_code") or "").strip()
    label = INVITE_LABELS.get(kind, INVITE_DIRECT)
    detail = who if who else (source if kind == INVITE_UTM else "")
    return "🧭 " + _row(
        f"{label} {detail}" if detail else label,
        f"метка {source}" if source and kind != INVITE_UTM else "",
        origin_label(origin),
    )


def build_new_client_text(
    card: dict[str, object],
    *,
    origin: object,
    site_enabled: bool,
    attribution: dict[str, object] | None = None,
) -> str:
    """Уведомление о новом клиенте: кто, откуда и когда — по строке на факт."""
    return "\n".join([
        "🆕 <b>Новый пользователь</b>",
        "",
        _client_row(card, site_enabled=site_enabled),
        _origin_row(card, origin=origin, attribution=attribution),
        f"🕐 {_now_label()}",
    ])


def build_payment_text(
    card: dict[str, object],
    *,
    amount: float,
    payment_system: str,
    origin: object,
    site_enabled: bool,
    payment_id: str | None = None,
    internal_id: int | None = None,
) -> str:
    """Уведомление об оплате: сумма и касса первой строкой, ниже клиент, время и счёт кассы."""
    invoice = str(payment_id or "").strip()
    numbers = _row(
        f"платёж №{internal_id}" if internal_id is not None else "",
        f"<code>{invoice}</code>" if invoice else "",
    )
    rows = [
        "💰 <b>Успешная оплата</b>",
        "",
        "💵 " + _row(f"<b>{_money(amount)}</b>", payment_system or "касса неизвестна", origin_label(origin)),
        _client_row(card, site_enabled=site_enabled),
        f"🕐 {_now_label()}",
    ]
    if numbers:
        rows.append(f"🧾 {numbers}")
    return "\n".join(rows)


async def notify_new_client(session: AsyncSession, user_id: int) -> None:
    """Уведомление админам о новом клиенте с каналом, откуда он пришёл."""
    if not _notifications_enabled("ADMIN_NEW_USER_ENABLED"):
        return
    card = await load_client_card(session, user_id)
    site_enabled, _ = _site()
    markup = build_client_keyboard(int(card["id"]), card.get("tg_id"))
    text = build_new_client_text(
        card,
        origin=card.get("signup_origin") or client_origin(),
        site_enabled=site_enabled,
        attribution=await resolve_attribution(session, card),
    )
    spawn(_send_to_admins(text, markup))


async def notify_payment(
    session: AsyncSession,
    user_id: int,
    *,
    amount: float,
    payment_system: str,
    origin: object = None,
    payment_id: str | None = None,
    internal_id: int | None = None,
) -> None:
    """Уведомление админам об успешной оплате с каналом, откуда платили."""
    if not _notifications_enabled("ADMIN_PAYMENT_ENABLED"):
        return
    card = await load_client_card(session, user_id)
    site_enabled, _ = _site()
    markup = build_client_keyboard(int(card["id"]), card.get("tg_id"))
    text = build_payment_text(
        card,
        amount=amount,
        payment_system=payment_system,
        origin=origin or client_origin(),
        site_enabled=site_enabled,
        payment_id=payment_id,
        internal_id=internal_id,
    )
    spawn(_send_to_admins(text, markup))
