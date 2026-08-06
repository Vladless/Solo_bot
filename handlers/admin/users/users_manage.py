import html
import re

from datetime import datetime, timezone

import pytz

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from aiogram.utils.formatting import BlockQuote, Bold, Code, Text
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    update_trial,
)
from database.access.resolution import resolve_user_optional
from database.models import Admin, Identity, Key, ManualBan, Payment, Referral, Tariff, User
from database.subscription_events import get_user_subscription_history, resolve_user_ref_by_client_id
from filters.admin import IsAdminFilter
from logger import logger
from settings.config import USERNAME_BOT
from utils.csv_export import export_referrals_csv

from ..panel.headers import card, menu_text, quote, section
from ..panel.keyboard import (
    AdminPanelCallback,
    build_admin_back_btn,
    build_admin_back_kb,
)
from .keyboard import (
    SITE_TAB_LABELS,
    AdminUserEditorCallback,
    build_editor_kb,
    build_user_edit_kb,
    build_user_site_send_kb,
    build_user_site_tabs_kb,
)
from .users_states import UserEditorState


MOSCOW_TZ = pytz.timezone("Europe/Moscow")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

router = Router()

SEARCH_PAGE_SIZE = 8
SEARCH_LIMIT_PER_SOURCE = 60


async def _fetch_search_candidates(session: AsyncSession, uid_reasons: dict[int, set[str]]) -> list[dict]:
    if not uid_reasons:
        return []
    uids = list(uid_reasons.keys())
    rows = (
        await session.execute(
            select(User.id, User.tg_id, User.username, User.first_name, User.last_name, Identity.email)
            .join(Identity, User.identity_id == Identity.id, isouter=True)
            .where(User.id.in_(uids))
        )
    ).all()
    key_owners = {
        r[0] for r in (await session.execute(select(Key.user_id).where(Key.user_id.in_(uids)).distinct())).all()
    }
    results = []
    for uid, tg, username, first, last, email in rows:
        bits = []
        display_name = " ".join(p for p in (first, last) if p)
        if display_name:
            bits.append(display_name)
        if username:
            bits.append(f"@{username}")
        bits.append(f"tg {tg}" if tg is not None else f"id {uid}")
        if email:
            bits.append(email)
        prefix = "👤🔑" if uid in key_owners else "👤"
        label = (f"{prefix} " + " · ".join(bits))[:64]
        ref = tg if tg is not None else uid
        results.append({"ref": int(ref), "label": label})
    results.sort(key=lambda c: c["label"].lower())
    return results


async def smart_user_search(session: AsyncSession, raw: str) -> list[dict]:
    """Ярусный поиск клиента по любым данным."""
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("@") and len(raw) > 1:
        raw = raw[1:].strip()
    like = f"%{raw}%"
    lowered = raw.lower()
    is_digit = raw.isdigit()
    is_uuid = bool(UUID_RE.match(raw))

    uid_reasons: dict[int, set[str]] = {}

    def note(uid, reason: str) -> None:
        if uid is None:
            return
        uid_reasons.setdefault(int(uid), set()).add(reason)

    user_conds = [User.username.ilike(like), User.first_name.ilike(like), User.last_name.ilike(like)]
    if is_digit:
        user_conds += [User.tg_id == int(raw), User.id == int(raw)]
    for (uid,) in (await session.execute(select(User.id).where(or_(*user_conds)).limit(SEARCH_LIMIT_PER_SOURCE))).all():
        note(uid, "профиль")

    key_conds = [
        Key.email.ilike(like),
        Key.alias.ilike(like),
        Key.remnawave_link.ilike(like),
        func.lower(Key.client_id) == lowered,
    ]
    for (uid,) in (
        await session.execute(select(Key.user_id).where(or_(*key_conds)).limit(SEARCH_LIMIT_PER_SOURCE))
    ).all():
        note(uid, "подписка")

    pay_conds = [Payment.payment_id == raw]
    if is_digit and int(raw) <= 2_147_483_647:
        pay_conds.append(Payment.id == int(raw))
    pay_rows = (
        await session.execute(
            select(Payment.user_id, Payment.tg_id).where(or_(*pay_conds)).limit(SEARCH_LIMIT_PER_SOURCE)
        )
    ).all()
    pending_tg: set[int] = set()
    for u_id, tg in pay_rows:
        if u_id is not None:
            note(u_id, "платеж")
        elif tg is not None:
            pending_tg.add(int(tg))
    if pending_tg:
        for (uid,) in (await session.execute(select(User.id).where(User.tg_id.in_(pending_tg)))).all():
            note(uid, "платеж")

    if uid_reasons:
        return await _fetch_search_candidates(session, uid_reasons)

    id_conds = [Identity.email.ilike(like), Identity.google_sub == raw, Identity.yandex_sub == raw]
    if is_uuid:
        id_conds.append(func.lower(Identity.id) == lowered)
    ident_rows = (
        await session.execute(select(Identity.id, Identity.tg_id).where(or_(*id_conds)).limit(SEARCH_LIMIT_PER_SOURCE))
    ).all()
    ident_ids = [iid for iid, _ in ident_rows]
    ident_tgs = {int(tg) for _, tg in ident_rows if tg is not None}
    if ident_ids:
        for (uid,) in (await session.execute(select(User.id).where(User.identity_id.in_(ident_ids)))).all():
            note(uid, "веб-аккаунт")
    if ident_tgs:
        for (uid,) in (await session.execute(select(User.id).where(User.tg_id.in_(ident_tgs)))).all():
            note(uid, "веб-аккаунт")

    if is_uuid:
        ref, _src = await resolve_user_ref_by_client_id(session, raw)
        if ref is not None:
            u = await resolve_user_optional(session, ref)
            if u is not None:
                note(u.id, "история")

    return await _fetch_search_candidates(session, uid_reasons)


async def search_from_forward(session: AsyncSession, fwd) -> list[dict]:
    """Поиск по пересланному сообщению: tg_id (приоритет) + username + имя/фамилия."""
    uid_reasons: dict[int, set[str]] = {}

    def note(uid, reason: str) -> None:
        if uid is None:
            return
        uid_reasons.setdefault(int(uid), set()).add(reason)

    for (uid,) in (await session.execute(select(User.id).where(User.tg_id == int(fwd.id)))).all():
        note(uid, "профиль")

    username = getattr(fwd, "username", None)
    if username:
        for (uid,) in (
            await session.execute(
                select(User.id).where(func.lower(User.username) == username.lower()).limit(SEARCH_LIMIT_PER_SOURCE)
            )
        ).all():
            note(uid, "username")

    first, last = getattr(fwd, "first_name", None), getattr(fwd, "last_name", None)
    name_conds = []
    if first and last:
        name_conds.append(and_(User.first_name.ilike(f"%{first}%"), User.last_name.ilike(f"%{last}%")))
    elif first:
        name_conds.append(User.first_name.ilike(f"%{first}%"))
    elif last:
        name_conds.append(User.last_name.ilike(f"%{last}%"))
    if name_conds:
        for (uid,) in (
            await session.execute(select(User.id).where(or_(*name_conds)).limit(SEARCH_LIMIT_PER_SOURCE))
        ).all():
            note(uid, "имя")

    return await _fetch_search_candidates(session, uid_reasons)


async def _render_search_results(target: types.Message, results: list[dict], query: str, page: int, edit: bool) -> None:
    pages = max(1, (len(results) + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)
    page = max(1, min(page, pages))
    start = (page - 1) * SEARCH_PAGE_SIZE
    chunk = results[start : start + SEARCH_PAGE_SIZE]

    text = menu_text(
        "Результаты поиска",
        "Выберите нужного клиента.",
        section("🔍 Поиск", f"Запрос: {html.escape(query)}", f"Найдено: {len(results)}"),
    )
    builder = InlineKeyboardBuilder()
    for c in chunk:
        builder.row(
            InlineKeyboardButton(
                text=c["label"],
                callback_data=AdminUserEditorCallback(action="users_editor", tg_id=int(c["ref"]), edit=True).pack(),
            )
        )
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(text="◀️", callback_data=AdminPanelCallback(action="search_page", page=page - 1).pack())
        )
    if pages > 1:
        nav.append(
            InlineKeyboardButton(
                text=f"{page}/{pages}", callback_data=AdminPanelCallback(action="search_page", page=page).pack()
            )
        )
    if page < pages:
        nav.append(
            InlineKeyboardButton(text="▶️", callback_data=AdminPanelCallback(action="search_page", page=page + 1).pack())
        )
    if nav:
        builder.row(*nav)
    builder.row(build_admin_back_btn())

    markup = builder.as_markup()
    if edit:
        await target.edit_text(text=text, reply_markup=markup)
    else:
        await target.answer(text=text, reply_markup=markup)


@router.callback_query(
    AdminPanelCallback.filter(F.action == "search_user"),
    IsAdminFilter(),
)
async def handle_search_user(callback_query: CallbackQuery, state: FSMContext):
    text = menu_text(
        "Поиск клиента",
        "Пришлите любые данные — бот найдёт карточку.",
        card(
            section(
                "🔎 Что подойдёт",
                "ID, username или имя",
                "почта веб-кабинета",
                "UUID веб-аккаунта",
                "ID, ссылка или имя подписки",
                "ID платежа",
            ),
            section("✉️ Или так", "перешлите сообщение клиента"),
        ),
    )

    await state.set_state(UserEditorState.waiting_for_user_data)
    await callback_query.message.edit_text(text=text, reply_markup=build_admin_back_kb())


@router.message(UserEditorState.waiting_for_user_data, IsAdminFilter())
async def handle_user_data_input(message: Message, state: FSMContext, session: AsyncSession):
    kb = build_admin_back_kb()

    if message.forward_from:
        fwd = message.forward_from
        raw = (
            (f"@{fwd.username}" if fwd.username else None)
            or " ".join(p for p in (fwd.first_name, fwd.last_name) if p)
            or str(fwd.id)
        )
        results = await search_from_forward(session, fwd)
    elif message.forward_sender_name:
        raw = message.forward_sender_name.strip()
        results = await smart_user_search(session, raw)
    elif message.text:
        raw = message.text.strip()
        results = await smart_user_search(session, raw)
    else:
        await message.answer(
            text=menu_text("Клиент", "Пришлите текст или перешлите сообщение клиента.", markup=kb),
            reply_markup=kb,
        )
        return

    if not results:
        await message.answer(text=menu_text("Клиент", "Ничего не найдено.", markup=kb), reply_markup=kb)
        return

    if len(results) == 1:
        await process_user_search(message, state, session, results[0]["ref"], actor_tg_id=message.from_user.id)
        return

    await state.update_data(search_results=results, search_query=raw)
    await _render_search_results(message, results, raw, page=1, edit=False)


@router.callback_query(
    AdminPanelCallback.filter(F.action == "search_page"),
    IsAdminFilter(),
    flags={"popup": True},
)
async def handle_search_page(
    callback_query: CallbackQuery,
    callback_data: AdminPanelCallback,
    state: FSMContext,
):
    data = await state.get_data()
    results = data.get("search_results")
    query = data.get("search_query", "")
    if not results:
        await callback_query.answer("Список устарел, повторите поиск", show_alert=True)
        return
    await _render_search_results(callback_query.message, results, query, page=callback_data.page, edit=True)
    await callback_query.answer()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_send_message"),
    IsAdminFilter(),
)
async def handle_send_message(
    callback_query: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    state: FSMContext,
):
    tg_id = callback_data.tg_id

    await callback_query.message.edit_text(
        text=(
            menu_text(
                "Сообщение клиенту",
                "Пришлите то, что нужно отправить.",
                section("📨 Подойдёт", "текст", "картинка", "текст с картинкой"),
                quote("Форматирование штатное телеграмное: жирный, курсив и прочее."),
                markup=build_editor_kb(tg_id),
            )
        ),
        reply_markup=build_editor_kb(tg_id),
    )

    await state.update_data(tg_id=tg_id)
    await state.set_state(UserEditorState.waiting_for_message_text)


@router.message(UserEditorState.waiting_for_message_text, IsAdminFilter())
async def handle_message_text_input(message: Message, state: FSMContext):
    data = await state.get_data()
    tg_id = data.get("tg_id")
    text_message = message.html_text or message.text or message.caption or ""
    photo = message.photo[-1].file_id if message.photo else None

    max_len = 1024 if photo else 4096
    if len(text_message) > max_len:
        await message.answer(
            menu_text(
                "Сообщение клиенту",
                "⚠️ Сообщение слишком длинное.",
                section("📏 Длина", f"Максимум: {max_len}", f"Сейчас: {len(text_message)}"),
                markup=build_editor_kb(tg_id),
            ),
            reply_markup=build_editor_kb(tg_id),
        )
        await state.clear()
        return

    await state.update_data(text=text_message, photo=photo)
    await state.set_state(UserEditorState.preview_message)

    if photo:
        await message.answer_photo(photo=photo, caption=text_message, parse_mode="HTML")
    else:
        await message.answer(text=text_message, parse_mode="HTML")

    await message.answer(
        menu_text("Клиент", "👀 Это предпросмотр сообщения. Отправить?"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="📤 Отправить", callback_data="send_user_message"),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_user_message"),
                ]
            ]
        ),
    )


@router.callback_query(
    F.data == "send_user_message",
    IsAdminFilter(),
    UserEditorState.preview_message,
)
async def handle_send_user_message(callback_query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tg_id = data.get("tg_id")
    text_message = data.get("text")
    photo = data.get("photo")

    try:
        if photo:
            await callback_query.bot.send_photo(
                chat_id=tg_id,
                photo=photo,
                caption=text_message,
                parse_mode="HTML",
            )
        else:
            await callback_query.bot.send_message(
                chat_id=tg_id,
                text=text_message,
                parse_mode="HTML",
            )
        try:
            import re

            from database import async_session_maker
            from database.web_notifications import notify_web

            clean = re.sub(r"<[^>]+>", "", text_message or "").strip()
            lines = clean.split("\n", 1)
            title = lines[0][:120]
            body = lines[1].strip()[:300] if len(lines) > 1 else ""
            async with async_session_maker() as session:
                await notify_web(session, tg_id=tg_id, type="message", title=title, message=body)
                await session.commit()
        except Exception as e:
            logger.warning("[UserManage] Ошибка web-уведомления для tg_id={}: {}", tg_id, e)

        await callback_query.message.edit_text(
            text=menu_text("Клиент", "✅ Сообщение отправлено.", markup=build_editor_kb(tg_id)),
            reply_markup=build_editor_kb(tg_id),
        )
    except Exception as e:
        await callback_query.message.edit_text(
            text=menu_text("Клиент", f"❌ Не удалось отправить сообщение: {e}", markup=build_editor_kb(tg_id)),
            reply_markup=build_editor_kb(tg_id),
        )
    await state.clear()


@router.callback_query(
    F.data == "cancel_user_message",
    IsAdminFilter(),
    UserEditorState.preview_message,
)
async def handle_cancel_user_message(callback_query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tg_id = data.get("tg_id")
    await callback_query.message.edit_text(
        text=menu_text("Клиент", "Отправка отменена.", markup=build_editor_kb(tg_id)),
        reply_markup=build_editor_kb(tg_id),
    )
    await state.clear()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_trial_restore"),
    IsAdminFilter(),
)
async def handle_trial_restore(
    callback_query: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id

    await update_trial(session, tg_id, 0)
    await callback_query.message.edit_text(
        text=menu_text("Клиент", "✅ Триал восстановлен.", markup=build_editor_kb(tg_id)),
        reply_markup=build_editor_kb(tg_id),
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "restore_trials"),
    IsAdminFilter(),
)
async def confirm_restore_trials(callback_query: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Подтвердить",
        callback_data=AdminPanelCallback(action="confirm_restore_trials").pack(),
    )
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text=(
            menu_text(
                "Клиент",
                "⚠️ Вернуть клиентам пробный период?",
                quote("Только тем, у кого нет подписок — ни активных, ни истёкших."),
                markup=builder.as_markup(),
            )
        ),
        reply_markup=builder.as_markup(),
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "confirm_restore_trials"),
    IsAdminFilter(),
)
async def restore_trials(callback_query: types.CallbackQuery, session: AsyncSession):
    stmt = (
        update(User)
        .where(
            User.trial == 1,
            ~exists(select(Key.user_id).where(Key.user_id == User.id)),
        )
        .values(trial=0)
    )
    result = await session.execute(stmt)

    builder = InlineKeyboardBuilder()
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text=menu_text(
            "Клиент",
            f"✅ Пробный период вернули {result.rowcount} клиентам без подписок.",
            markup=builder.as_markup(),
        ),
        reply_markup=builder.as_markup(),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_export_referrals"),
    IsAdminFilter(),
)
async def handle_users_export_referrals(
    callback_query: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    referrer_tg_id = callback_data.tg_id

    csv_file = await export_referrals_csv(referrer_tg_id, session)

    if csv_file is None:
        await callback_query.message.answer(menu_text("Клиент", "У клиента нет рефералов."))
        return

    await callback_query.message.answer_document(
        document=csv_file,
        caption=f"Список рефералов для пользователя {referrer_tg_id}.",
    )


async def process_user_search(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    tg_id: int,
    edit: bool = False,
    actor_tg_id: int | None = None,
) -> None:
    await state.clear()

    u = await resolve_user_optional(session, tg_id)
    if u is None:
        await message.answer(
            text=menu_text("Клиент", "❌ Клиент с таким ID не найден.", markup=build_admin_back_kb()),
            reply_markup=build_admin_back_kb(),
        )
        return
    uid = u.id
    real_tg_id = u.tg_id
    identity_email = None
    if u.identity_id:
        identity_email = await session.scalar(select(Identity.email).where(Identity.id == u.identity_id))

    stmt_user = select(User.username, User.balance, User.created_at, User.updated_at, User.trial).where(User.id == uid)
    result_user = await session.execute(stmt_user)
    user_data = result_user.first()

    if not user_data:
        await message.answer(
            text=menu_text("Клиент", "❌ Клиент с таким ID не найден.", markup=build_admin_back_kb()),
            reply_markup=build_admin_back_kb(),
        )
        return

    username, balance, created_at, updated_at, trial = user_data
    balance = int(balance or 0)
    created_at_str = created_at.replace(tzinfo=pytz.UTC).astimezone(MOSCOW_TZ).strftime("%d.%m.%y")
    updated_at_str = updated_at.replace(tzinfo=pytz.UTC).astimezone(MOSCOW_TZ).strftime("%d.%m.%y %H:%M")

    trial_status = "использован" if trial == 1 else "доступен"

    stmt_ref_count = select(func.count()).select_from(Referral).where(Referral.referrer_user_id == uid)
    result_ref = await session.execute(stmt_ref_count)
    referral_count = result_ref.scalar_one()

    stmt_ref_by = select(Referral.referrer_user_id).where(Referral.referred_user_id == uid).limit(1)
    result_ref_by = await session.execute(stmt_ref_by)
    referrer_uid = result_ref_by.scalar_one_or_none()

    referrer_text = None
    if referrer_uid:
        stmt_referrer = select(User.username, User.tg_id).where(User.id == referrer_uid)
        result_referrer = await session.execute(stmt_referrer)
        ref_row = result_referrer.first()
        ref_username = ref_row[0] if ref_row else None
        ref_tg = ref_row[1] if ref_row else None
        ref_label = int(ref_tg) if ref_tg is not None else int(referrer_uid)
        if ref_username:
            referrer_text = f"Пригласил: @{ref_username} ({ref_label})"
        else:
            referrer_text = f"Пригласил: {ref_label}"

    stmt = select(
        func.count(Payment.id),
        func.coalesce(func.sum(Payment.amount), 0),
    ).where(
        Payment.status == "success",
        Payment.user_id == uid,
        Payment.payment_system != "admin",
    )
    result = await session.execute(stmt)
    topups_amount, topups_sum = result.one_or_none() or (0, 0)

    stmt_keys = select(Key).where(Key.user_id == uid)
    result_keys = await session.execute(stmt_keys)
    key_records = result_keys.scalars().all()

    stmt_ban = select(ManualBan).where(ManualBan.user_id == uid).limit(1)
    result_ban = await session.execute(stmt_ban)
    ban_record = result_ban.scalar_one_or_none()

    ban_info = None
    ban_reason = None
    is_banned = ban_record is not None
    if ban_record:
        if ban_record.reason == "shadow":
            ban_info = "Теневой бан"
        elif ban_record.until:
            until_str = ban_record.until.replace(tzinfo=pytz.UTC).astimezone(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")
            ban_info = f"До {until_str}"
            if ban_record.reason:
                ban_reason = ban_record.reason
        else:
            ban_info = "Навсегда"
            if ban_record.reason:
                ban_reason = ban_record.reason

    from core.settings.web_config import get_site_url, is_web_enabled

    who = [
        f"TG ID: {real_tg_id if real_tg_id is not None else '—'}",
        f"Логин: @{username}" if username else "Логин: —",
        f"Почта: {identity_email if identity_email else '—'}",
    ]

    money = [f"Баланс: {balance} ₽"]
    if topups_amount:
        money.append(f"Пополнил: {topups_sum} ₽ ({topups_amount})")

    activity = [f"Пришёл: {created_at_str}", f"Активен: {updated_at_str}"]
    if referral_count:
        activity.append(f"Привёл: {referral_count}")
    if referrer_text:
        activity.append(referrer_text)
    activity.append(f"Пробный: {trial_status}")

    blocks = [section("👤 Данные", *who), section("💰 Деньги", *money), section("📈 Активность", *activity)]
    if ban_info:
        blocks.append(section("🚫 Блокировка", ban_info, f"Причина: {ban_reason}" if ban_reason else ""))
    if is_web_enabled() and get_site_url():
        blocks.append(section("🌐 Кабинет", f"<code>https://telegram.me/{USERNAME_BOT}?start=tab_keys</code>"))

    text = card(*blocks)

    effective_actor_tg_id = actor_tg_id or (message.from_user.id if message.from_user else None)
    admin_role = None
    if effective_actor_tg_id is not None:
        admin_role = await session.scalar(select(Admin.role).where(Admin.tg_id == effective_actor_tg_id))

    has_email = identity_email is not None and str(identity_email).strip() != ""
    has_tg = real_tg_id is not None
    kb = await build_user_edit_kb(
        tg_id,
        key_records,
        is_banned=is_banned,
        admin_role=admin_role,
        has_email=has_email,
        has_tg=has_tg,
    )

    screen = menu_text("Клиент", f"@{username}" if username else f"<code>{tg_id}</code>", text, markup=kb)

    if edit:
        try:
            await message.edit_text(text=screen, reply_markup=kb, disable_web_page_preview=True)
        except TelegramBadRequest:
            pass
    else:
        await message.answer(text=screen, reply_markup=kb, disable_web_page_preview=True)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_editor"),
    IsAdminFilter(),
)
async def handle_users_editor(
    callback: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
    state: FSMContext,
):
    await process_user_search(
        callback.message,
        state=state,
        session=session,
        tg_id=callback_data.tg_id,
        edit=callback_data.edit,
        actor_tg_id=callback.from_user.id,
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_site"),
    IsAdminFilter(),
)
async def handle_users_site(callback: CallbackQuery, callback_data: AdminUserEditorCallback):
    text = menu_text(
        "Ссылки на кабинет",
        "Выберите вкладку.",
        quote("Бот покажет ссылку для клиента — она откроет его кабинет сразу на этой вкладке."),
    )
    try:
        await callback.message.edit_text(
            text=text,
            reply_markup=build_user_site_tabs_kb(callback_data.tg_id),
            disable_web_page_preview=True,
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_site_tab"),
    IsAdminFilter(),
    flags={"popup": True},
)
async def handle_users_site_tab(callback: CallbackQuery, callback_data: AdminUserEditorCallback):
    tab = str(callback_data.data or "")
    label = SITE_TAB_LABELS.get(tab)
    if not label:
        await callback.answer("Неизвестная вкладка", show_alert=True)
        return
    text = menu_text(
        "Ссылка на кабинет",
        f"Вкладка: <b>{label}</b>",
        quote("Нажмите «Отправить» — клиент получит в чате кнопку, открывающую эту вкладку кабинета."),
    )
    try:
        await callback.message.edit_text(
            text=text,
            reply_markup=build_user_site_send_kb(callback_data.tg_id, tab),
            disable_web_page_preview=True,
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_site_send"),
    IsAdminFilter(),
    flags={"popup": True},
)
async def handle_users_site_send(callback: CallbackQuery, callback_data: AdminUserEditorCallback):
    tab = str(callback_data.data or "")
    label = SITE_TAB_LABELS.get(tab)
    if not label:
        await callback.answer("Неизвестная вкладка", show_alert=True)
        return

    from core.settings.web_config import get_site_url, is_web_enabled, is_web_open_in_browser

    if not is_web_enabled():
        await callback.answer("Веб-кабинет выключен", show_alert=True)
        return
    site_url = get_site_url()
    if not site_url:
        await callback.answer("Не задан адрес сайта", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    if is_web_open_in_browser():
        button = InlineKeyboardButton(text=f"🌐 {label}", url=f"{site_url}/dashboard?tab={tab}")
    else:
        button = InlineKeyboardButton(
            text=f"🌐 {label}",
            web_app=WebAppInfo(url=f"{site_url}/dashboard?tab={tab}&webapp=1"),
        )
    builder.row(button)

    from bot import bot

    try:
        await bot.send_message(
            callback_data.tg_id,
            "Откройте раздел в личном кабинете 👇",
            reply_markup=builder.as_markup(),
        )
    except Exception as e:
        logger.warning(f"[users_site_send] send to {callback_data.tg_id} failed: {e}")
        await callback.answer("Клиент не запускал бота", show_alert=True)
        return
    await callback.answer(f"✅ Отправлено клиенту: {label}", show_alert=True)


SUB_HISTORY_LIMIT = 20


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_sub_history"),
    IsAdminFilter(),
    flags={"popup": True},
)
async def handle_user_sub_history(
    callback: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    u = await resolve_user_optional(session, callback_data.tg_id)
    if u is None:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    history = await get_user_subscription_history(session, user_id=u.id, tg_id=u.tg_id)

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data=AdminUserEditorCallback(
                        action="users_editor", tg_id=callback_data.tg_id, edit=True
                    ).pack(),
                )
            ]
        ]
    )

    if not history:
        await callback.message.edit_text(
            menu_text(
                "История подписок",
                "Все подписки клиента, от свежих к старым.",
                quote("Подписок пока не было"),
                markup=back_kb,
            ),
            reply_markup=back_kb,
        )
        return

    tariff_ids = {g["tariff_id"] for g in history if g["tariff_id"] is not None}
    tariff_names: dict[int, str] = {}
    if tariff_ids:
        rows = (await session.execute(select(Tariff.id, Tariff.name).where(Tariff.id.in_(tariff_ids)))).all()
        tariff_names = {r.id: r.name for r in rows}

    client_ids = [g["client_id"] for g in history]
    active_expiry: dict[str, int] = {}
    if client_ids:
        rows = (
            await session.execute(select(Key.client_id, Key.expiry_time).where(Key.client_id.in_(client_ids)))
        ).all()
        active_expiry = {r.client_id: r.expiry_time for r in rows}

    active_count = sum(1 for g in history if g["client_id"] in active_expiry)
    shown = history[:SUB_HISTORY_LIMIT]

    blocks = [section("🧾 Итого", f"Всего: {len(history)}", f"Активных: {active_count}")]

    for i, g in enumerate(shown, 1):
        cid = g["client_id"]
        short = f"{cid[:8]}…" if cid and len(cid) > 8 else (cid or "—")
        tariff = tariff_names.get(g["tariff_id"]) or (f"тариф #{g['tariff_id']}" if g["tariff_id"] else "—")
        created_str = g["first_at"].replace(tzinfo=pytz.UTC).astimezone(MOSCOW_TZ).strftime("%d.%m.%y")

        if cid in active_expiry:
            status = "🟢 Активна"
            exp_ms = active_expiry[cid]
        else:
            exp_ms = g["max_expiry"]
            if g["last_event"] == "deleted":
                status = "⚪️ Удалена"
            elif g["last_event"] == "expired":
                status = "🔴 Истекла"
            else:
                status = "⚪️ Завершена"

        if exp_ms:
            exp_str = datetime.fromtimestamp(exp_ms / 1000, tz=timezone.utc).astimezone(MOSCOW_TZ).strftime("%d.%m.%y")
        else:
            exp_str = "—"

        blocks.append(
            section(
                f"{status} · {i}",
                f"Тариф: {tariff}",
                f"Начало: {created_str}",
                f"Конец: {exp_str}",
                f"Продлений: {g['renewals'] or 0}",
                f"ID: {short}",
            )
        )

    if len(history) > len(shown):
        blocks.append(quote(f"Показаны последние {len(shown)} из {len(history)}"))

    try:
        await callback.message.edit_text(
            menu_text("История подписок", card(*blocks), markup=back_kb), reply_markup=back_kb
        )
    except TelegramBadRequest:
        pass


async def _resolve_identity_for_user(session: AsyncSession, legacy_ref: int) -> Identity | None:
    u = await resolve_user_optional(session, legacy_ref)
    if u is None or not u.identity_id:
        return None
    return await session.scalar(select(Identity).where(Identity.id == u.identity_id))


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_unlink_email"),
    IsAdminFilter(),
    flags={"popup": True},
)
async def handle_unlink_email(
    callback: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
    state: FSMContext,
):
    from database.identities import detach_email

    identity = await _resolve_identity_for_user(session, callback_data.tg_id)
    if identity is None:
        await callback.answer("Веб-аккаунт не привязан", show_alert=True)
        return
    if identity.email is None:
        await callback.answer("Email уже не привязан", show_alert=True)
        return
    if identity.tg_id is None:
        await callback.answer("Нельзя отвязать email — это единственный способ входа", show_alert=True)
        return
    result = await detach_email(session, identity.id)
    if result is None:
        await callback.answer("Не удалось отвязать email", show_alert=True)
        return
    await callback.answer("Email отвязан", show_alert=False)
    await process_user_search(
        callback.message,
        state=state,
        session=session,
        tg_id=callback_data.tg_id,
        edit=True,
        actor_tg_id=callback.from_user.id,
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_unlink_tg"),
    IsAdminFilter(),
    flags={"popup": True},
)
async def handle_unlink_tg(
    callback: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
    state: FSMContext,
):
    from database.identities import detach_telegram

    identity = await _resolve_identity_for_user(session, callback_data.tg_id)
    if identity is None:
        await callback.answer("Веб-аккаунт не привязан", show_alert=True)
        return
    if identity.tg_id is None:
        await callback.answer("Telegram уже не привязан", show_alert=True)
        return
    if identity.email is None:
        await callback.answer("Нельзя отвязать TG — нет email для входа", show_alert=True)
        return
    result = await detach_telegram(session, identity.id)
    if result is None:
        await callback.answer("Не удалось отвязать Telegram", show_alert=True)
        return
    await callback.answer("Telegram отвязан", show_alert=False)
    await process_user_search(
        callback.message,
        state=state,
        session=session,
        tg_id=callback_data.tg_id,
        edit=True,
        actor_tg_id=callback.from_user.id,
    )
