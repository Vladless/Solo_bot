import asyncio

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.bootstrap import MODES_CONFIG
from database import async_session_maker
from database.identities import get_or_create_identity_for_tg
from handlers.support_bot.media import collect_attachment
from services import tickets as svc
from settings.texts import TRIAGE_ITEMS


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

    asyncio.create_task(_cleanup())


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


def _menu_kb(has_tickets: bool) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✉️ Новое обращение", callback_data="sc:new"))
    if has_tickets:
        kb.row(InlineKeyboardButton(text="📋 Мои обращения", callback_data="sc:list"))
    return kb


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
        "💡 Можно приложить скриншот или файл."
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
            "💡 Можно приложить скриншот или файл.",
            reply_markup=kb,
        )
        return
    await state.set_state(IntakeSG.categorizing)
    await state.update_data(pending_body=None, pending_attachments=None)
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
    await state.update_data(pending_body=None, pending_attachments=None)
    await callback.message.answer(_CATEGORY_PROMPT, reply_markup=_cat_kb().as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("sc:cat:"))
async def cb_cat(callback: CallbackQuery, state: FSMContext) -> None:
    raw = callback.data.split(":", 2)[2]
    category = raw if raw in _CATEGORIES else None
    data = await state.get_data()
    pending_body = data.get("pending_body")
    pending_att = data.get("pending_attachments")
    await callback.answer()
    if pending_body or pending_att:
        await state.clear()
        await _create_from_intake(callback.message, callback.from_user.id, category, pending_body or "", pending_att)
        return
    await state.set_state(IntakeSG.describing)
    await state.update_data(category=category)
    label = _category_label(category)
    topic = f"📌 Тема: <b>{label}</b>\n\n" if label else ""
    await callback.message.answer(
        f"{topic}Опишите проблему <b>одним сообщением</b> — оператор ответит здесь.\n\n"
        "💡 Можно приложить скриншот или файл."
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
        lines.append(f"<b>{tag}</b> <i>{_fmt_dt(m.created_at)}</i>\n{(m.body or '').strip()}")
    text = "\n".join(head) + "\n\n".join(lines)
    if len(text) > 3900:
        text = text[-3900:]
    kb = InlineKeyboardBuilder()
    if ticket.status != svc.CLOSED and not _shadow_mode():
        kb.row(InlineKeyboardButton(text="✍️ Написать", callback_data="sc:new"))
    await callback.message.answer(text, reply_markup=kb.as_markup())
    await callback.answer()


async def _append_client_message(message: Message, ticket_id: str) -> None:
    body, attachments = await collect_attachment(message)
    if not body and not attachments:
        return
    assigned = None
    ticket = None
    async with async_session_maker() as session:
        msg = await svc.add_message(
            session, ticket_id=ticket_id, author="client", body=body[:4000], attachments=attachments
        )
        ticket = await svc.get_ticket(session, ticket_id)
        assigned = ticket.assigned_agent_tg_id if ticket is not None else None
        await session.commit()
    if msg is None or ticket is None:
        return
    if svc.forum_enabled():
        await svc.post_client_message(ticket, body, attachments)
        return
    if assigned:
        short = ticket_id.split("-")[0][:8]
        try:
            await message.bot.send_message(assigned, f"🆕 Новое сообщение по обращению <code>{short}</code>")
            if attachments:
                await message.copy_to(assigned)
        except Exception:
            pass


async def _create_from_intake(target: Message, user_id: int, category, body: str, attachments) -> None:
    async with async_session_maker() as session:
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
        await session.commit()
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


@router.message(IntakeSG.describing, F.text | F.caption | F.photo | F.document)
async def on_intake(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    category = data.get("category")
    await state.clear()
    body, attachments = await collect_attachment(message)
    if not body and not attachments:
        await message.answer("Пустое сообщение. Опишите проблему текстом или пришлите скриншот.")
        return
    await _create_from_intake(message, message.from_user.id, category, body, attachments)


async def _shadow_reopen_and_append(message: Message, ticket_id: str) -> bool:
    async with async_session_maker() as session:
        ticket = await svc.set_status(session, ticket_id=ticket_id, status=svc.OPEN)
        await session.commit()
    if ticket is None:
        return False
    await _append_client_message(message, ticket_id)
    try:
        await svc.set_topic_state(ticket, closed=False)
    except Exception:
        pass
    return True


@router.message(F.text | F.caption | F.photo | F.document)
async def on_free_text(message: Message, state: FSMContext) -> None:
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
        body, attachments = await collect_attachment(message)
        if not body and not attachments:
            await _answer_ephemeral(message, "Опишите проблему текстом или пришлите скриншот.")
            return
        await _create_from_intake(message, message.from_user.id, None, body, attachments)
        return
    body, attachments = await collect_attachment(message)
    if not body and not attachments:
        await message.answer("Опишите проблему текстом или пришлите скриншот.")
        return
    await state.set_state(IntakeSG.categorizing)
    await state.update_data(pending_body=body, pending_attachments=attachments)
    await message.answer(
        f"Спасибо, приняли ваше сообщение. {_CATEGORY_PROMPT}",
        reply_markup=_cat_kb().as_markup(),
    )
