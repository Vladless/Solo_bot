import asyncio

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.bootstrap import MODES_CONFIG
from core.executor import spawn
from database import async_session_maker
from database.identities import get_or_create_identity_for_tg
from handlers.support_bot.media import AttachmentUnavailableError, collect_required_attachment
from services import tickets as svc
from services.tickets.telegram import (
    apply_telegram_edit,
    confirm_delete,
    find_source_message,
    register_source,
    request_delete,
    source_from_message,
)
from settings.texts import (
    SUPPORT_ATTACHMENT_UNAVAILABLE,
    SUPPORT_MESSAGE_DELETED_TEXT,
    SUPPORT_MESSAGE_DELETE_HINT,
    SUPPORT_MESSAGE_EDITED_TEXT,
    TRIAGE_ITEMS,
)


router = Router()
router.message.filter(F.chat.type == "private")

STATUS_LABEL = {
    svc.OPEN: "🟢 В работе",
    svc.ANSWERED: "💬 Есть ответ",
    svc.PENDING: "⏳ Ожидает",
    svc.CLOSED: "⚪️ Закрыт",
}
_CATEGORIES = {i["id"]: i["label"] for i in TRIAGE_ITEMS}


class IntakeSG(StatesGroup):
    describing = State()
    categorizing = State()


def _category_label(cat: str | None) -> str | None:
    if not cat:
        return None
    return _CATEGORIES.get(cat, cat)


def _fmt_dt(dt) -> str:
    try:
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return ""


def _shadow_mode() -> bool:
    return bool(MODES_CONFIG.get("SUPPORT_SHADOW_MODE", True))


async def _answer_ephemeral(message: Message, text: str, delay: float = 4.0) -> None:
    try:
        sent = await message.answer(text)
    except Exception:
        return

    async def _cleanup():
        await asyncio.sleep(delay)
        try:
            await sent.delete()
        except Exception:
            pass

    spawn(_cleanup())


async def _active_ticket(session, identity_id: str):
    tickets = await svc.list_for_identity(session, identity_id)
    for t in tickets:
        if t.status != svc.CLOSED:
            return t
    return None


async def _latest_closed_ticket(session, identity_id: str):
    tickets = await svc.list_for_identity(session, identity_id)
    for t in tickets:
        if t.status == svc.CLOSED:
            return t
    return None


def _cat_kb(has_tickets: bool = False) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for i in TRIAGE_ITEMS:
        kb.row(InlineKeyboardButton(text=i["label"], callback_data=f"sc:cat:{i['id']}"))
    kb.row(InlineKeyboardButton(text="✏️ Другое / не из списка", callback_data="sc:cat:_other"))
    if has_tickets:
        kb.row(InlineKeyboardButton(text="📋 Мои обращения", callback_data="sc:list"))
    return kb


_CATEGORY_PROMPT = "Выберите, что случилось — так мы поможем быстрее:"


@router.message(CommandStart(deep_link=True))
async def start_deeplink(message: Message, command: CommandObject, state: FSMContext) -> None:
    payload = (command.args or "").strip()
    category = payload if payload in _CATEGORIES else None
    await state.set_state(IntakeSG.describing)
    await state.update_data(category=category)
    label = _category_label(category)
    topic = f"📌 Тема: <b>{label}</b>\n\n" if label else ""
    await message.answer(
        "<b>Служба поддержки</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        f"{topic}"
        "Опишите проблему <b>одним сообщением</b> — оператор ответит вам прямо здесь.\n\n"
        "💡 Можно приложить скриншот или файл.\n"
        f"{SUPPORT_MESSAGE_DELETE_HINT}"
    )


@router.message(CommandStart())
async def start_plain(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with async_session_maker() as session:
        identity = await get_or_create_identity_for_tg(session, message.from_user.id)
        tickets = await svc.list_for_identity(session, identity.id)
        await session.commit()
    if _shadow_mode():
        kb = None
        if tickets:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="📋 Мои обращения", callback_data="sc:list"))
            kb = builder.as_markup()
        await message.answer(
            "<b>Служба поддержки</b>\n━━━━━━━━━━━━━━━\n\n"
            "Просто напишите ваш вопрос — оператор ответит прямо здесь.\n\n"
            "💡 Можно приложить скриншот или файл.\n"
            f"{SUPPORT_MESSAGE_DELETE_HINT}",
            reply_markup=kb,
        )
        return
    await state.set_state(IntakeSG.categorizing)
    await state.update_data(pending_body=None, pending_attachments=None, pending_source=None)
    await message.answer(
        f"<b>Служба поддержки</b>\n━━━━━━━━━━━━━━━\n\n{_CATEGORY_PROMPT}",
        reply_markup=_cat_kb(bool(tickets)).as_markup(),
    )


@router.callback_query(F.data == "sc:new")
async def cb_new(callback: CallbackQuery, state: FSMContext) -> None:
    if _shadow_mode():
        await callback.message.answer("Просто напишите ваш вопрос сообщением — оператор ответит здесь.")
        await callback.answer()
        return
    await state.set_state(IntakeSG.categorizing)
    await state.update_data(pending_body=None, pending_attachments=None, pending_source=None)
    await callback.message.answer(_CATEGORY_PROMPT, reply_markup=_cat_kb().as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("sc:cat:"))
async def cb_cat(callback: CallbackQuery, state: FSMContext) -> None:
    raw = callback.data.split(":", 2)[2]
    category = raw if raw in _CATEGORIES else None
    data = await state.get_data()
    pending_body = data.get("pending_body")
    pending_att = data.get("pending_attachments")
    pending_source = data.get("pending_source")
    await callback.answer()
    if pending_body or pending_att:
        await state.clear()
        await _create_from_intake(
            callback.message,
            callback.from_user.id,
            category,
            pending_body or "",
            pending_att,
            source=pending_source,
        )
        return
    await state.set_state(IntakeSG.describing)
    await state.update_data(category=category)
    label = _category_label(category)
    topic = f"📌 Тема: <b>{label}</b>\n\n" if label else ""
    await callback.message.answer(
        f"{topic}Опишите проблему <b>одним сообщением</b> — оператор ответит здесь.\n\n"
        "💡 Можно приложить скриншот или файл.\n"
        f"{SUPPORT_MESSAGE_DELETE_HINT}"
    )


@router.callback_query(F.data.startswith("sc:rate:"))
async def cb_rate(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, ticket_id, raw = callback.data.split(":", 3)
    rating = int(raw) if raw.isdigit() else 0
    async with async_session_maker() as session:
        identity = await get_or_create_identity_for_tg(session, callback.from_user.id)
        ticket = await svc.get_ticket(session, ticket_id)
        if ticket is None or ticket.identity_id != identity.id:
            await session.commit()
            await callback.answer("Обращение не найдено", show_alert=True)
            return
        updated = await svc.set_rating(session, ticket_id=ticket_id, rating=rating)
        await session.commit()
    if updated is None:
        await callback.answer("Не удалось сохранить оценку", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            f"Спасибо за оценку! {'⭐' * rating}\n\n"
            "Ваше мнение помогает нам стать лучше.\n"
            "Появится новый вопрос — просто напишите его сюда."
        )
    except Exception:
        pass
    await callback.answer("Спасибо!")


@router.callback_query(F.data == "sc:list")
async def cb_list(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with async_session_maker() as session:
        identity = await get_or_create_identity_for_tg(session, callback.from_user.id)
        tickets = await svc.list_for_identity(session, identity.id)
        await session.commit()
    if not tickets:
        await callback.answer("Обращений нет", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    for t in tickets[:15]:
        label = _category_label(t.category) or (t.subject or "Обращение")
        kb.row(
            InlineKeyboardButton(
                text=f"{STATUS_LABEL.get(t.status, t.status)} · {label}"[:60],
                callback_data=f"sc:open:{t.id}",
            )
        )
    await callback.message.answer("📋 <b>Ваши обращения</b>", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("sc:open:"))
async def cb_open(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    ticket_id = callback.data.split(":", 2)[2]
    async with async_session_maker() as session:
        identity = await get_or_create_identity_for_tg(session, callback.from_user.id)
        ticket = await svc.get_ticket(session, ticket_id)
        if ticket is None or ticket.identity_id != identity.id:
            await session.commit()
            await callback.answer("Обращение не найдено", show_alert=True)
            return
        thread = await svc.get_thread(session, ticket_id)
        await svc.mark_read(session, ticket_id=ticket_id, by="client")
        await session.commit()
    label = _category_label(ticket.category)
    head = [f"<b>Обращение</b> · {STATUS_LABEL.get(ticket.status, ticket.status)}"]
    if label:
        head.append(f"Тема: {label}")
    head.append("")
    lines = []
    visible = [m for m in thread if m.author != "note"]
    for m in visible[-12:]:
        tag = svc.support_persona() if m.author == "agent" else "Вы"
        body = SUPPORT_MESSAGE_DELETED_TEXT if m.deleted_at else escape((m.body or "").strip())
        edited = f" · {SUPPORT_MESSAGE_EDITED_TEXT}" if m.edited_at and not m.deleted_at else ""
        lines.append(f"<b>{escape(tag)}</b> <i>{_fmt_dt(m.created_at)}{edited}</i>\n{body}")
    text = "\n".join(head) + "\n\n".join(lines)
    if len(text) > 3900:
        text = text[-3900:]
    kb = InlineKeyboardBuilder()
    if ticket.status != svc.CLOSED and not _shadow_mode():
        kb.row(InlineKeyboardButton(text="✍️ Написать", callback_data="sc:new"))
    await callback.message.answer(text, reply_markup=kb.as_markup())
    await callback.answer()


async def _append_client_message(
    message: Message, ticket_id: str, *, collected: tuple[str, list[str] | None] | None = None
) -> None:
    source = source_from_message(message)
    async with async_session_maker() as session:
        if await find_source_message(session, source) is not None:
            return
    body, attachments = collected if collected is not None else await collect_required_attachment(message)
    if not body and not attachments:
        return
    ticket = None
    async with async_session_maker() as session:
        msg = await svc.add_message(
            session, ticket_id=ticket_id, author="client", body=body[:4000], attachments=attachments
        )
        if msg is not None:
            await register_source(session, msg, source)
        ticket = await svc.get_ticket(session, ticket_id)
        await session.commit()
    if msg is None or ticket is None:
        return


async def _create_from_intake(
    target: Message,
    user_id: int,
    category,
    body: str,
    attachments,
    source: dict | None = None,
) -> None:
    async with async_session_maker() as session:
        if source is not None and await find_source_message(session, source) is not None:
            return
        identity = await get_or_create_identity_for_tg(session, user_id)
        try:
            ticket = await svc.create_ticket(
                session,
                identity_id=identity.id,
                body=(body or "")[:4000],
                category=category,
                subject=_category_label(category),
                source="bot",
                attachments=attachments,
            )
        except svc.TicketRateLimited as e:
            await session.rollback()
            if e.reason == "too_many_open":
                await target.answer("У вас уже есть открытые обращения — дождитесь ответа по ним.")
            else:
                await target.answer("Вы только что создали обращение — подождите немного перед новым.")
            return
        if source is not None:
            thread = await svc.get_thread(session, ticket.id)
            if thread:
                await register_source(session, thread[0], source)
        ticket_id = ticket.id
        await svc.notify_agents_new_ticket(session, ticket=ticket)
        await session.commit()
    if _shadow_mode():
        await _answer_ephemeral(target, "✅ Передал в поддержку. Оператор ответит здесь.")
        return
    await target.answer(
        f"✅ Обращение принято (<code>{ticket_id.split('-')[0][:8]}</code>).\n"
        "Оператор ответит здесь. Можете дополнять сообщение — всё придёт в поддержку."
    )


@router.message(Command("delete"), F.chat.type == "private")
async def on_delete(message: Message) -> None:
    await request_delete(message, forum=False)


@router.callback_query(F.data.startswith("tm:delete:") | (F.data == "tm:cancel"), F.message.chat.type == "private")
async def on_delete_confirmation(callback: CallbackQuery) -> None:
    await confirm_delete(callback, forum=False)


@router.edited_message(F.chat.type == "private", F.text | F.caption | F.photo | F.document | F.animation)
async def on_client_edit(message: Message, state: FSMContext) -> None:
    if await state.get_state() == IntakeSG.categorizing.state:
        data = await state.get_data()
        pending = data.get("pending_source")
        source = source_from_message(message)
        if pending and all(pending.get(key) == source.get(key) for key in ("bot_id", "chat_id", "telegram_message_id")):
            try:
                body, attachments = await collect_required_attachment(message)
            except AttachmentUnavailableError:
                await message.answer(SUPPORT_ATTACHMENT_UNAVAILABLE)
                return
            if body or attachments:
                await state.update_data(
                    pending_body=body,
                    pending_attachments=attachments,
                    pending_source=source,
                )
            return
    await apply_telegram_edit(message, forum=False)


@router.message(IntakeSG.describing, F.text | F.caption | F.photo | F.document | F.animation)
async def on_intake(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    category = data.get("category")
    try:
        body, attachments = await collect_required_attachment(message)
    except AttachmentUnavailableError:
        await message.answer(SUPPORT_ATTACHMENT_UNAVAILABLE)
        return
    if not body and not attachments:
        await message.answer("Пустое сообщение. Опишите проблему текстом или пришлите скриншот.")
        return
    await state.clear()
    await _create_from_intake(
        message,
        message.from_user.id,
        category,
        body,
        attachments,
        source=source_from_message(message),
    )


async def _shadow_reopen_and_append(message: Message, ticket_id: str) -> bool:
    collected = await collect_required_attachment(message)
    async with async_session_maker() as session:
        ticket = await svc.set_status(session, ticket_id=ticket_id, status=svc.OPEN)
        await session.commit()
    if ticket is None:
        return False
    await _append_client_message(message, ticket_id, collected=collected)
    try:
        await svc.set_topic_state(ticket, closed=False)
    except Exception:
        pass
    return True


@router.message(F.text | F.caption | F.photo | F.document | F.animation)
async def on_free_text(message: Message, state: FSMContext) -> None:
    try:
        await _handle_free_text(message, state)
    except AttachmentUnavailableError:
        await message.answer(SUPPORT_ATTACHMENT_UNAVAILABLE)


async def _handle_free_text(message: Message, state: FSMContext) -> None:
    if (message.text or "").startswith("/"):
        return
    async with async_session_maker() as session:
        identity = await get_or_create_identity_for_tg(session, message.from_user.id)
        active = await _active_ticket(session, identity.id)
        closed = None if active is not None else await _latest_closed_ticket(session, identity.id)
        await session.commit()
        active_id = active.id if active is not None else None
        closed_id = closed.id if closed is not None else None
    if active_id is not None:
        await _append_client_message(message, active_id)
        await _answer_ephemeral(message, "✅ Передал в поддержку.")
        return
    if _shadow_mode():
        if closed_id is not None and await _shadow_reopen_and_append(message, closed_id):
            await _answer_ephemeral(message, "✅ Передал в поддержку.")
            return
        body, attachments = await collect_required_attachment(message)
        if not body and not attachments:
            await _answer_ephemeral(message, "Опишите проблему текстом или пришлите скриншот.")
            return
        await _create_from_intake(
            message,
            message.from_user.id,
            None,
            body,
            attachments,
            source=source_from_message(message),
        )
        return
    body, attachments = await collect_required_attachment(message)
    if not body and not attachments:
        await message.answer("Опишите проблему текстом или пришлите скриншот.")
        return
    await state.set_state(IntakeSG.categorizing)
    await state.update_data(
        pending_body=body,
        pending_attachments=attachments,
        pending_source=source_from_message(message),
    )
    await message.answer(
        f"Спасибо, приняли ваше сообщение. {_CATEGORY_PROMPT}",
        reply_markup=_cat_kb().as_markup(),
    )
