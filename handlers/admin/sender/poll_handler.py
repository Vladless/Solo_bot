import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database.polls import close_poll, create_poll, delete_poll, get_poll, get_poll_stats, list_polls
from filters.admin import IsAdminFilter
from logger import logger
from middlewares.session import release_session_early

from ..panel.keyboard import build_admin_back_kb
from .keyboard import (
    AdminPollCallback,
    build_poll_audience_kb,
    build_poll_delete_confirm_kb,
    build_poll_detail_kb,
    build_poll_preview_kb,
    build_polls_menu_kb,
)
from .poll_service import broadcast_poll
from .sender_states import AdminPoll
from .sender_utils import get_recipients


router = Router(name="admin_polls")

MAX_QUESTION = 300
MAX_OPTION = 100
MIN_OPTIONS = 2
MAX_OPTIONS = 12


def _esc(value) -> str:
    return html.escape(str(value or ""))


def _preview_text(question: str, options: list[str], is_anonymous: bool) -> str:
    lines = ["📊 <b>Предпросмотр опроса</b>", "", f"<b>{_esc(question)}</b>", ""]
    for idx, opt in enumerate(options, 1):
        lines.append(f"{idx}. {_esc(opt)}")
    lines.append("")
    if is_anonymous:
        lines.append(
            "⚠️ <b>Анонимный</b> опрос: Telegram не присылает голоса по отдельности, "
            "поэтому детальной статистики не будет — только число разосланных."
        )
    else:
        lines.append("Опрос <b>неанонимный</b> — голоса собираются в общую статистику.")
    return "\n".join(lines)


def _stats_text(poll, stats: dict) -> str:
    options = poll.options or []
    counts = stats["counts"]
    total_voters = stats["total_voters"]
    total_votes = stats["total_votes"]
    status = "🟢 Открыт" if poll.status == "open" else "🔒 Закрыт"
    denom = total_votes if poll.allows_multiple else total_voters
    lines = [
        "📊 <b>Статистика опроса</b>",
        "",
        f"<b>{_esc(poll.question)}</b>",
        "",
        f"Статус: {status}",
        f"Разослан: <b>{poll.sent_count}</b>",
        f"Проголосовало: <b>{total_voters}</b>",
        "",
    ]
    if poll.is_anonymous:
        lines.append("⚠️ Анонимный опрос — детальная статистика по голосам недоступна.")
        lines.append("")
    for idx, opt in enumerate(options):
        count = counts[idx] if idx < len(counts) else 0
        pct = int(round(100 * count / denom)) if denom else 0
        filled = max(0, min(10, int(round(pct / 10))))
        bar = "█" * filled + "░" * (10 - filled)
        lines.append(f"{_esc(opt)}\n[{bar}] {pct}% ({count})")
    return "\n".join(lines)


async def _render_menu(session: AsyncSession):
    polls = await list_polls(session, limit=10)
    text = (
        "📊 <b>Опросы</b>\n\n"
        "Нативные Telegram-опросы с рассылкой всем пользователям бота и сбором общей статистики.\n\n"
        f"Опросов в списке: <b>{len(polls)}</b>"
    )
    return text, build_polls_menu_kb(polls)


@router.callback_query(AdminPollCallback.filter(F.action == "menu"), IsAdminFilter())
async def open_polls_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    text, kb = await _render_menu(session)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(AdminPollCallback.filter(F.action == "create"), IsAdminFilter())
async def poll_create(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminPoll.waiting_for_question)
    await callback.message.edit_text(
        f"✍️ Введите <b>вопрос</b> опроса (до {MAX_QUESTION} символов):",
        reply_markup=build_admin_back_kb("sender"),
    )
    await callback.answer()


@router.message(AdminPoll.waiting_for_question, IsAdminFilter())
async def poll_question_input(message: Message, state: FSMContext) -> None:
    question = (message.text or "").strip()
    if not question:
        await message.answer("❌ Вопрос не может быть пустым. Введите текст вопроса.")
        return
    if len(question) > MAX_QUESTION:
        await message.answer(f"❌ Слишком длинный вопрос ({len(question)}/{MAX_QUESTION}). Сократите.")
        return
    await state.update_data(question=question)
    await state.set_state(AdminPoll.waiting_for_options)
    await message.answer(
        "📝 Теперь введите <b>варианты ответа</b> — по одному на строку.\n\n"
        f"От {MIN_OPTIONS} до {MAX_OPTIONS} вариантов, каждый до {MAX_OPTION} символов.\n\n"
        "Пример:\n<code>Да\nНет\nЗатрудняюсь ответить</code>"
    )


@router.message(AdminPoll.waiting_for_options, IsAdminFilter())
async def poll_options_input(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    options = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(options) < MIN_OPTIONS:
        await message.answer(f"❌ Нужно минимум {MIN_OPTIONS} варианта — по одному на строку.")
        return
    if len(options) > MAX_OPTIONS:
        await message.answer(f"❌ Максимум {MAX_OPTIONS} вариантов, сейчас {len(options)}.")
        return
    too_long = next((opt for opt in options if len(opt) > MAX_OPTION), None)
    if too_long is not None:
        await message.answer(f"❌ Вариант длиннее {MAX_OPTION} символов: «{too_long[:50]}…»")
        return
    await state.update_data(options=options, is_anonymous=False)
    await state.set_state(AdminPoll.preview)
    data = await state.get_data()
    await message.answer(
        _preview_text(data["question"], options, False),
        reply_markup=build_poll_preview_kb(False),
    )


@router.callback_query(AdminPollCallback.filter(F.action == "anon"), AdminPoll.preview, IsAdminFilter())
async def poll_toggle_anon(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    new_val = not data.get("is_anonymous", False)
    await state.update_data(is_anonymous=new_val)
    await callback.message.edit_text(
        _preview_text(data["question"], data["options"], new_val),
        reply_markup=build_poll_preview_kb(new_val),
    )
    await callback.answer()


@router.callback_query(AdminPollCallback.filter(F.action == "back_preview"), AdminPoll.preview, IsAdminFilter())
async def poll_back_preview(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    is_anon = data.get("is_anonymous", False)
    await callback.message.edit_text(
        _preview_text(data["question"], data["options"], is_anon),
        reply_markup=build_poll_preview_kb(is_anon),
    )
    await callback.answer()


@router.callback_query(AdminPollCallback.filter(F.action == "audience"), AdminPoll.preview, IsAdminFilter())
async def poll_audience(callback: CallbackQuery) -> None:
    await callback.message.edit_text("📤 Кому отправить опрос?", reply_markup=build_poll_audience_kb())
    await callback.answer()


@router.callback_query(AdminPollCallback.filter(F.action == "aud"), AdminPoll.preview, IsAdminFilter(), flags={"popup": True})
async def poll_send(
    callback: CallbackQuery, callback_data: AdminPollCallback, state: FSMContext, session: AsyncSession
) -> None:
    send_to = callback_data.value or "all"
    data = await state.get_data()
    question = data.get("question")
    options = data.get("options")
    is_anonymous = data.get("is_anonymous", False)
    if not question or not options:
        await callback.answer("Данные опроса потеряны, создайте заново.", show_alert=True)
        await state.clear()
        return

    poll = await create_poll(
        session,
        question=question,
        options=options,
        is_anonymous=is_anonymous,
        created_by_tg_id=callback.from_user.id,
    )
    poll_id = poll.id
    tg_ids, _ = await get_recipients(session, send_to, None, telegram_only=True)
    await session.commit()
    await state.clear()

    if not tg_ids:
        await callback.message.edit_text("⚠️ Нет получателей для рассылки.", reply_markup=build_admin_back_kb("sender"))
        await callback.answer()
        return

    await callback.message.edit_text(f"📤 Отправляю опрос… 0/{len(tg_ids)}")
    await callback.answer()
    await release_session_early(session)

    bot = callback.bot
    status_chat = callback.message.chat.id
    status_mid = callback.message.message_id

    async def on_progress(done: int, total: int, sent: int, failed: int) -> None:
        try:
            await bot.edit_message_text(
                chat_id=status_chat,
                message_id=status_mid,
                text=f"📤 Отправка опроса…\n\n{done}/{total}   ✅ {sent}   ❌ {failed}",
            )
        except Exception:
            pass

    try:
        result = await broadcast_poll(
            bot,
            poll_id=poll_id,
            question=question,
            options=options,
            allows_multiple=False,
            is_anonymous=is_anonymous,
            tg_ids=tg_ids,
            on_progress=on_progress,
        )
    except Exception as exc:
        logger.error("[Polls] Ошибка рассылки опроса: {}", exc)
        await bot.edit_message_text(
            chat_id=status_chat,
            message_id=status_mid,
            text=f"❌ Ошибка рассылки опроса: <code>{_esc(exc)}</code>",
            reply_markup=build_poll_detail_kb(poll_id, True),
        )
        return

    await bot.edit_message_text(
        chat_id=status_chat,
        message_id=status_mid,
        text=(
            "✅ <b>Опрос отправлен</b>\n\n"
            f"Доставлено: <b>{result['sent']}</b>\n"
            f"Не доставлено: <b>{result['failed']}</b>\n\n"
            "Статистика обновляется по мере голосования."
        ),
        reply_markup=build_poll_detail_kb(poll_id, True),
    )


@router.callback_query(AdminPollCallback.filter(F.action == "view"), IsAdminFilter(), flags={"popup": True})
async def poll_view(callback: CallbackQuery, callback_data: AdminPollCallback, session: AsyncSession) -> None:
    poll = await get_poll(session, callback_data.poll_id)
    if poll is None:
        await callback.answer("Опрос не найден", show_alert=True)
        return
    stats = await get_poll_stats(session, poll.id)
    await callback.message.edit_text(
        _stats_text(poll, stats),
        reply_markup=build_poll_detail_kb(poll.id, poll.status == "open"),
    )
    await callback.answer()


@router.callback_query(AdminPollCallback.filter(F.action == "close"), IsAdminFilter(), flags={"popup": True})
async def poll_close(callback: CallbackQuery, callback_data: AdminPollCallback, session: AsyncSession) -> None:
    poll = await get_poll(session, callback_data.poll_id)
    if poll is None:
        await callback.answer("Опрос не найден", show_alert=True)
        return
    await close_poll(session, poll.id)
    await session.flush()
    poll.status = "closed"
    stats = await get_poll_stats(session, poll.id)
    await callback.message.edit_text(_stats_text(poll, stats), reply_markup=build_poll_detail_kb(poll.id, False))
    await callback.answer("Опрос закрыт — новые голоса не учитываются")


@router.callback_query(AdminPollCallback.filter(F.action == "del_ask"), IsAdminFilter(), flags={"popup": True})
async def poll_delete_ask(callback: CallbackQuery, callback_data: AdminPollCallback, session: AsyncSession) -> None:
    poll = await get_poll(session, callback_data.poll_id)
    if poll is None:
        await callback.answer("Опрос не найден", show_alert=True)
        return
    question = (poll.question or "").strip()
    await callback.message.edit_text(
        "🗑 <b>Удалить опрос?</b>\n\n"
        f"<b>{_esc(question)}</b>\n\n"
        "Опрос и вся его статистика будут удалены безвозвратно. "
        "Уже отправленные пользователям сообщения останутся в их чатах.",
        reply_markup=build_poll_delete_confirm_kb(poll.id),
    )
    await callback.answer()


@router.callback_query(AdminPollCallback.filter(F.action == "del"), IsAdminFilter(), flags={"popup": True})
async def poll_delete(callback: CallbackQuery, callback_data: AdminPollCallback, session: AsyncSession) -> None:
    poll = await get_poll(session, callback_data.poll_id)
    if poll is None:
        await callback.answer("Опрос не найден", show_alert=True)
    else:
        await delete_poll(session, poll.id)
        await session.flush()
        await callback.answer("Опрос удалён")
    text, kb = await _render_menu(session)
    await callback.message.edit_text(text, reply_markup=kb)
