import asyncio

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.executor import run_io, should_run_heavy_tasks_separately
from database import async_session_maker, save_blocked_user_ids
from database.models import Server
from database.scheduled_broadcasts import (
    cancel_scheduled_broadcast,
    create_scheduled_broadcast,
    get_scheduled_broadcast,
    list_scheduled_broadcasts,
    mark_scheduled_broadcast_failed,
    mark_scheduled_broadcast_sent,
    start_scheduled_broadcast,
    update_scheduled_broadcast,
)
from database.tracking_sources import get_all_tracking_sources
from filters.admin import IsAdminFilter
from logger import logger
from middlewares.session import release_session_early
from settings.config import API_TOKEN

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb
from .keyboard import (
    AdminSenderCallback,
    AdminSenderChannelCallback,
    ScheduledBroadcastCallback,
    build_broadcast_preview_kb,
    build_channel_kb,
    build_clusters_kb,
    build_scheduled_broadcast_detail_kb,
    build_scheduled_broadcasts_list_kb,
    build_sender_kb,
    build_sources_kb,
    channel_label,
)
from .scheduled_service import (
    execute_scheduled_broadcast,
    format_moscow_datetime,
    parse_moscow_datetime_input,
    prepare_broadcast_payload,
)
from .sender_service import BroadcastService, run_broadcast_in_thread
from .sender_states import AdminSender
from .sender_utils import (
    channels_to_str,
    get_recipients,
    is_telegram_chat_id,
    parse_channels,
    parse_message_buttons,
)


def _broadcast_progress_text(completed: int, total: int, sent: int, failed: int, pending: int = 0) -> str:
    """Формирует текст статус-бара рассылки."""
    if total <= 0:
        pct = 0
        bar_filled = 0
    else:
        pct = min(100, int(100 * completed / total))
        bar_filled = min(10, int(10 * completed / total))
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    base = f"📤 <b>Рассылка...</b>\n\n[{bar}] <b>{pct}%</b> ({completed}/{total})\n✅ {sent}   ❌ {failed}"
    if pending > 0:
        base += f"   🔄 {pending}"
    return base


def _compose_message_text() -> str:
    return (
        "✍️ Введите текст сообщения для рассылки\n\n"
        "Поддерживается только Telegram-форматирование — <b>жирный</b>, <i>курсив</i> и другие стили через редактор Telegram.\n\n"
        "Вы можете отправить:\n"
        "• Только <b>текст</b>\n"
        "• Только <b>картинку</b>\n"
        "• <b>Текст + картинку</b>\n"
        "• <b>Сообщение + кнопки</b> (см. формат ниже)\n\n"
        "<b>📋 Пример формата кнопок:</b>\n"
        "<code>Ваше сообщение</code>\n\n"
        "<code>BUTTONS:</code>\n"
        '<code>{"text": "👤 Личный кабинет", "callback": "profile"}</code>\n'
        '<code>{"text": "➕ Купить подписку", "callback": "buy"}</code>\n'
        '<code>{"text": "🎁 Забрать купон", "url": "https://telegram.me/cupons"}</code>\n'
        '<code>{"text": "📢 Канал", "url": "https://telegram.me/channel"}</code>'
    )


def _scheduled_status_label(status: str) -> str:
    mapping = {
        "scheduled": "Запланирована",
        "running": "Отправляется",
        "sent": "Отправлена",
        "cancelled": "Отменена",
        "failed": "Ошибка",
        "draft": "Черновик",
    }
    return mapping.get(status, status)


def _truncate_text(value: str, limit: int = 500) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _scheduled_broadcast_text(item) -> str:
    target = item.send_to
    if item.cluster_name:
        target = f"{target} ({item.cluster_name})"
    lines = [
        "🗓 <b>Запланированная рассылка</b>",
        "",
        f"🆔 <code>{item.id}</code>",
        f"📌 <b>Статус:</b> {_scheduled_status_label(item.status)}",
        f"🕒 <b>Время:</b> {format_moscow_datetime(item.scheduled_for) or '-'}",
        f"👥 <b>Аудитория:</b> {target}",
        f"📢 <b>Канал:</b> {channel_label(item.channel or 'both')}",
        f"🖼 <b>Фото:</b> {'Да' if item.photo else 'Нет'}",
        f"⌨️ <b>Кнопки:</b> {'Да' if item.keyboard_json else 'Нет'}",
        "",
        "<b>Текст:</b>",
        _truncate_text(item.text or "—"),
    ]
    if item.error_text:
        lines.extend(["", f"⚠️ <b>Ошибка:</b> {_truncate_text(item.error_text, 250)}"])
    if item.stats_json:
        stats = item.stats_json.get("stats") or {}
        lines.extend([
            "",
            f"✅ <b>Доставлено:</b> {stats.get('success_count', 0)}",
            f"❌ <b>Не доставлено:</b> {stats.get('failed_count', 0)}",
        ])
    return "\n".join(lines)


def _scheduled_broadcasts_list_text(items: list, page: int) -> str:
    if not items:
        return "🗓 <b>Запланированных рассылок пока нет.</b>"
    lines = ["🗓 <b>Запланированные рассылки</b>\n", f"Страница: <b>{page + 1}</b>\n"]
    for item in items:
        target = item.send_to if not item.cluster_name else f"{item.send_to}/{item.cluster_name}"
        lines.append(
            f"• <code>{item.id[:8]}</code> | {format_moscow_datetime(item.scheduled_for) or '-'} | "
            f"{_scheduled_status_label(item.status)} | {target}"
        )
    return "\n".join(lines)


def _broadcast_result_text(recipients: int, stats: dict) -> str:
    duration_minutes = int(stats["total_duration"] // 60)
    duration_seconds = int(stats["total_duration"] % 60)
    duration_str = (
        f"{duration_minutes} мин {duration_seconds} сек" if duration_minutes > 0 else f"{duration_seconds} сек"
    )
    email_line = ""
    if "email_sent" in stats:
        email_line = f"📧 <b>Писем отправлено:</b> {stats['email_sent']}"
        if stats.get("email_failed"):
            email_line += f" (ошибок {stats['email_failed']})"
        email_line += "\n"
    return (
        f"📤 <b>Рассылка завершена!</b>\n\n"
        f"👥 <b>Количество получателей:</b> {recipients}\n"
        f"✅ <b>Доставлено:</b> {stats['success_count']}\n"
        f"❌ <b>Не доставлено:</b> {stats['failed_count']}\n"
        f"🚫 <b>Заблокировавших бота:</b> {stats['blocked_users']}\n"
        f"{email_line}\n"
        f"⏱️ <b>Время выполнения:</b> {duration_str}\n"
        f"⚡ <b>Средняя скорость:</b> {stats['avg_speed']:.1f} сообщений/сек"
    )


from filters.admin import HasPermission
from filters.permissions import PERM_BROADCASTING


router = Router()
router.callback_query.filter(HasPermission(PERM_BROADCASTING))
router.message.filter(HasPermission(PERM_BROADCASTING))


@router.callback_query(
    AdminPanelCallback.filter(F.action == "sender"),
    IsAdminFilter(),
)
async def handle_sender(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback_query.message.edit_text(
            text="✍️ Выберите группу пользователей для рассылки:",
            reply_markup=build_sender_kb(),
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            logger.debug("[Sender] Сообщение не изменено")
        else:
            raise


@router.callback_query(
    AdminSenderCallback.filter(F.type == "cluster-select"),
    IsAdminFilter(),
)
async def handle_cluster_select(callback_query: CallbackQuery, session: AsyncSession, state: FSMContext):
    result = await session.execute(select(Server.cluster_name).distinct())
    clusters = result.mappings().all()
    data = await state.get_data()
    text = "✍️ Выберите кластер для рассылки сообщений:"
    if data.get("edit_action") == "audience":
        text = "✍️ Выберите новый кластер для запланированной рассылки:"

    await callback_query.message.answer(
        text,
        reply_markup=build_clusters_kb(clusters),
    )


@router.callback_query(
    AdminSenderCallback.filter(F.type == "source-select"),
    IsAdminFilter(),
)
async def handle_source_select(callback_query: CallbackQuery, session: AsyncSession, state: FSMContext):
    sources = await get_all_tracking_sources(session)
    if not sources:
        await callback_query.message.answer(
            "❌ Нет UTM-источников. Создайте источник трафика, чтобы делать рассылку по метке.",
            reply_markup=build_admin_back_kb("sender"),
        )
        return

    data = await state.get_data()
    text = "✍️ Выберите UTM-источник для рассылки:"
    if data.get("edit_action") == "audience":
        text = "✍️ Выберите новый UTM-источник для запланированной рассылки:"

    await callback_query.message.answer(
        text,
        reply_markup=build_sources_kb(sources),
    )


@router.callback_query(
    AdminSenderCallback.filter((F.type != "cluster-select") & (F.type != "source-select")),
    IsAdminFilter(),
)
async def handle_broadcast_type(
    callback_query: CallbackQuery,
    callback_data: AdminSenderCallback,
    state: FSMContext,
    session: AsyncSession,
):
    data = await state.get_data()
    if data.get("edit_action") == "audience":
        broadcast_id = data.get("edit_broadcast_id")
        page = int(data.get("edit_page", 0))
        item = await get_scheduled_broadcast(session, broadcast_id)
        if item is None:
            await state.clear()
            await callback_query.message.edit_text(
                "❌ Запланированная рассылка не найдена.",
                reply_markup=build_admin_back_kb("sender"),
            )
            return
        try:
            prepared = prepare_broadcast_payload(
                send_to=callback_data.type,
                text=item.text,
                photo=item.photo,
                cluster_name=callback_data.data,
                workers=item.workers,
                messages_per_second=item.messages_per_second,
                channel=item.channel,
            )
        except ValueError as exc:
            await callback_query.message.edit_text(str(exc), reply_markup=build_admin_back_kb("sender"))
            return
        updated = await update_scheduled_broadcast(
            session,
            broadcast_id,
            send_to=prepared["send_to"],
            cluster_name=prepared["cluster_name"],
            error_text=None,
        )
        await state.clear()
        if updated is None:
            await callback_query.message.edit_text(
                "❌ Эту рассылку уже нельзя изменить.",
                reply_markup=build_admin_back_kb("sender"),
            )
            return
        await callback_query.message.edit_text(
            _scheduled_broadcast_text(updated),
            reply_markup=build_scheduled_broadcast_detail_kb(updated, page=page),
        )
        return
    await state.update_data(type=callback_data.type, cluster_name=callback_data.data, channels=["bot", "site"])
    await state.set_state(AdminSender.waiting_for_channel)
    await callback_query.message.edit_text(
        text="📢 Куда отправить рассылку?\nОтметьте каналы и нажмите «Продолжить».",
        reply_markup=build_channel_kb(["bot", "site"]),
    )


@router.callback_query(
    AdminSenderChannelCallback.filter(),
    AdminSender.waiting_for_channel,
    IsAdminFilter(),
)
async def handle_channel_select(
    callback_query: CallbackQuery,
    callback_data: AdminSenderChannelCallback,
    state: FSMContext,
):
    token = callback_data.channel
    data = await state.get_data()
    channels = list(data.get("channels", ["bot", "site"]))

    if token in ("bot", "site", "email"):
        if token in channels:
            channels.remove(token)
        else:
            channels.append(token)
        await state.update_data(channels=channels)
        try:
            await callback_query.message.edit_reply_markup(reply_markup=build_channel_kb(channels))
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise
        await callback_query.answer()
        return

    if token == "continue":
        if not channels:
            await callback_query.answer("Выберите хотя бы один канал", show_alert=True)
            return
        await state.update_data(channel=channels_to_str(channels))
        await state.set_state(AdminSender.waiting_for_message)
        await callback_query.message.edit_text(
            text=_compose_message_text(),
            reply_markup=build_admin_back_kb("sender"),
        )
        await callback_query.answer()


@router.message(AdminSender.waiting_for_message, IsAdminFilter())
async def handle_message_input(message: Message, state: FSMContext, session: AsyncSession):
    original_text = message.html_text or message.text or message.caption or ""
    photo = message.photo[-1].file_id if message.photo else None

    clean_text, keyboard = parse_message_buttons(original_text)

    max_len = 1024 if photo else 4096
    if len(clean_text) > max_len:
        await message.answer(
            f"⚠️ Сообщение слишком длинное.\nМаксимум: <b>{max_len}</b> символов, сейчас: <b>{len(clean_text)}</b>.",
            reply_markup=build_admin_back_kb("sender"),
        )
        await state.clear()
        return

    data = await state.get_data()
    send_to = data.get("type", "all")
    cluster_name = data.get("cluster_name")
    channel = data.get("channel", "both")
    _, user_count = await get_recipients(session, send_to, cluster_name, channel=channel)

    if keyboard:
        try:
            keyboard_dict = keyboard.model_dump()
            InlineKeyboardMarkup.model_validate(keyboard_dict)
        except Exception as e:
            await message.answer(
                f"❌ <b>Ошибка в клавиатуре!</b>\n\n"
                f"Не удалось сохранить клавиатуру из указанных кнопок.\n"
                f"Ошибка: {str(e)}\n\n"
                f"Пожалуйста, проверьте формат кнопок и попробуйте снова.",
                reply_markup=build_admin_back_kb("sender"),
            )
            await state.clear()
            return

    await state.update_data(text=clean_text, photo=photo, keyboard=keyboard.model_dump() if keyboard else None)
    await state.set_state(AdminSender.preview)

    if photo:
        await message.answer_photo(photo=photo, caption=clean_text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer(text=clean_text, parse_mode="HTML", reply_markup=keyboard)

    await message.answer(
        f"👀 Это предпросмотр рассылки.\n"
        f"👥 Количество получателей: <b>{user_count}</b>\n"
        f"📢 Канал: <b>{channel_label(channel)}</b>\n\nОтправить?",
        reply_markup=build_broadcast_preview_kb(),
    )


@router.callback_query(F.data == "send_broadcast", IsAdminFilter())
async def handle_broadcast_confirm(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    text_message = data.get("text")
    photo = data.get("photo")
    keyboard_data = data.get("keyboard")
    send_to = data.get("type", "all")
    cluster_name = data.get("cluster_name")
    channel = data.get("channel", "both")

    keyboard = None
    if keyboard_data:
        try:
            keyboard = InlineKeyboardMarkup.model_validate(keyboard_data)
        except Exception as e:
            logger.error(f"[Sender] Ошибка восстановления клавиатуры: {e}")
            await callback_query.message.edit_text(
                f"❌ <b>Ошибка восстановления клавиатуры!</b>\n\n"
                f"Не удалось восстановить клавиатуру из сохраненных данных.\n"
                f"Ошибка: {str(e)}\n\n"
                f"Пожалуйста, создайте рассылку заново.",
                reply_markup=build_admin_back_kb("sender"),
            )
            await state.clear()
            return

    tg_ids, total_users = await get_recipients(
        session,
        send_to,
        cluster_name,
        channel=channel,
    )

    if not tg_ids:
        await callback_query.message.edit_text(
            "⚠️ Не найдено получателей для рассылки.",
            reply_markup=build_admin_back_kb("sender"),
        )
        await state.clear()
        return

    status_message = callback_query.message
    if "bot" in parse_channels(channel):
        total_users_for_bar = sum(1 for tid in tg_ids if is_telegram_chat_id(tid))
    else:
        total_users_for_bar = 0
    await status_message.edit_text(
        _broadcast_progress_text(0, total_users_for_bar, 0, 0),
    )

    bot = callback_query.bot
    state_keyboard_data = data.get("keyboard")

    await release_session_early(session)

    if should_run_heavy_tasks_separately():
        main_loop = asyncio.get_running_loop()

        async def _edit_progress(completed: int, total: int, sent: int, failed: int, pending: int) -> None:
            text = _broadcast_progress_text(completed, total, sent, failed, pending)
            try:
                await bot.edit_message_text(
                    chat_id=status_message.chat.id,
                    message_id=status_message.message_id,
                    text=text,
                )
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e).lower():
                    logger.debug(f"[Sender] Обновление прогресса: {e}")

        def progress_cb(completed: int, total: int, sent: int, failed: int, pending: int) -> None:
            main_loop.call_soon_threadsafe(
                lambda c=completed, t=total, s=sent, f=failed, p=pending: asyncio.ensure_future(
                    _edit_progress(c, t, s, f, p), loop=main_loop
                )
            )

        stats = await run_io(
            run_broadcast_in_thread,
            API_TOKEN,
            tg_ids,
            text_message,
            photo,
            state_keyboard_data,
            progress_cb,
            channel,
        )
    else:
        messages = []
        for tg_id in tg_ids:
            message_data = {"tg_id": tg_id, "text": text_message, "photo": photo, "keyboard": keyboard}
            messages.append(message_data)

        async def on_progress(completed: int, total: int, sent: int, failed: int, pending: int) -> None:
            text = _broadcast_progress_text(completed, total, sent, failed, pending)
            try:
                await bot.edit_message_text(
                    chat_id=status_message.chat.id,
                    message_id=status_message.message_id,
                    text=text,
                )
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e).lower():
                    logger.debug(f"[Sender] Обновление прогресса: {e}")

        broadcast_service = BroadcastService(bot=bot, session=None)
        stats = await broadcast_service.broadcast(
            messages,
            workers=5,
            on_progress=on_progress,
            progress_interval=2.0,
            progress_every=200,
            channel=channel,
        )

    blocked_ids = stats.get("blocked_user_ids")
    if blocked_ids:
        try:
            async with async_session_maker() as db_session:
                await save_blocked_user_ids(db_session, blocked_ids)
                await db_session.commit()
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении заблокированных пользователей: {e}")

    await callback_query.message.answer(
        text=_broadcast_result_text(total_users, stats),
        reply_markup=build_admin_back_kb("sender"),
    )
    await state.clear()


@router.callback_query(F.data == "schedule_broadcast", IsAdminFilter())
async def handle_schedule_broadcast(callback_query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("text"):
        await callback_query.message.edit_text(
            "⚠️ Не найден черновик рассылки. Создайте его заново.",
            reply_markup=build_admin_back_kb("sender"),
        )
        await state.clear()
        return
    await callback_query.message.edit_text(
        "🕒 Введите дату и время отправки по Москве в формате <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>.",
        reply_markup=build_admin_back_kb("sender"),
    )
    await state.set_state(AdminSender.waiting_for_schedule_datetime)


@router.message(AdminSender.waiting_for_schedule_datetime, IsAdminFilter())
async def handle_schedule_datetime_input(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    try:
        scheduled_for = parse_moscow_datetime_input(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    if scheduled_for <= datetime.now(timezone.utc):
        await message.answer("⚠️ Время отправки должно быть в будущем.")
        return
    created = await create_scheduled_broadcast(
        session,
        created_by_tg_id=message.from_user.id if message.from_user else None,
        send_to=data.get("type", "all"),
        channel=data.get("channel", "both"),
        cluster_name=data.get("cluster_name"),
        text=data.get("text", ""),
        photo=data.get("photo"),
        keyboard_json=data.get("keyboard"),
        scheduled_for=scheduled_for,
        workers=5,
        messages_per_second=25,
    )
    await state.clear()
    await message.answer(
        _scheduled_broadcast_text(created),
        reply_markup=build_scheduled_broadcast_detail_kb(created, page=0),
    )


@router.callback_query(ScheduledBroadcastCallback.filter(F.action == "list"), IsAdminFilter())
async def handle_scheduled_broadcasts_list(
    callback_query: CallbackQuery,
    callback_data: ScheduledBroadcastCallback,
    session: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    page = max(0, int(callback_data.page or 0))
    items = await list_scheduled_broadcasts(session, limit=5, offset=page * 5)
    await callback_query.message.edit_text(
        _scheduled_broadcasts_list_text(items, page),
        reply_markup=build_scheduled_broadcasts_list_kb(items, page=page),
    )


@router.callback_query(ScheduledBroadcastCallback.filter(F.action == "view"), IsAdminFilter())
async def handle_scheduled_broadcast_view(
    callback_query: CallbackQuery,
    callback_data: ScheduledBroadcastCallback,
    session: AsyncSession,
):
    item = await get_scheduled_broadcast(session, callback_data.broadcast_id)
    if item is None:
        await callback_query.message.edit_text(
            "❌ Запланированная рассылка не найдена.",
            reply_markup=build_admin_back_kb("sender"),
        )
        return
    await callback_query.message.edit_text(
        _scheduled_broadcast_text(item),
        reply_markup=build_scheduled_broadcast_detail_kb(item, page=callback_data.page),
    )


@router.callback_query(ScheduledBroadcastCallback.filter(F.action == "edit_message"), IsAdminFilter())
async def handle_scheduled_broadcast_edit_message(
    callback_query: CallbackQuery,
    callback_data: ScheduledBroadcastCallback,
    session: AsyncSession,
    state: FSMContext,
):
    item = await get_scheduled_broadcast(session, callback_data.broadcast_id)
    if item is None:
        await callback_query.message.edit_text(
            "❌ Запланированная рассылка не найдена.",
            reply_markup=build_admin_back_kb("sender"),
        )
        return
    await state.update_data(edit_broadcast_id=item.id, edit_page=callback_data.page)
    await state.set_state(AdminSender.waiting_for_edit_message)
    await callback_query.message.edit_text(
        "✏️ Отправьте новое сообщение для этой рассылки.",
        reply_markup=build_admin_back_kb("sender"),
    )


@router.message(AdminSender.waiting_for_edit_message, IsAdminFilter())
async def handle_scheduled_broadcast_edit_message_input(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    data = await state.get_data()
    broadcast_id = data.get("edit_broadcast_id")
    page = int(data.get("edit_page", 0))
    item = await get_scheduled_broadcast(session, broadcast_id)
    if item is None:
        await state.clear()
        await message.answer("❌ Запланированная рассылка не найдена.", reply_markup=build_admin_back_kb("sender"))
        return
    original_text = message.html_text or message.text or message.caption or ""
    photo = message.photo[-1].file_id if message.photo else item.photo
    try:
        prepared = prepare_broadcast_payload(
            send_to=item.send_to,
            text=original_text,
            photo=photo,
            cluster_name=item.cluster_name,
            workers=item.workers,
            messages_per_second=item.messages_per_second,
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return
    updated = await update_scheduled_broadcast(
        session,
        broadcast_id,
        text=prepared["text"],
        photo=prepared["photo"],
        keyboard_json=prepared["keyboard_json"],
        error_text=None,
    )
    await state.clear()
    if updated is None:
        await message.answer("❌ Эту рассылку уже нельзя изменить.", reply_markup=build_admin_back_kb("sender"))
        return
    await message.answer(
        _scheduled_broadcast_text(updated),
        reply_markup=build_scheduled_broadcast_detail_kb(updated, page=page),
    )


@router.callback_query(ScheduledBroadcastCallback.filter(F.action == "edit_time"), IsAdminFilter())
async def handle_scheduled_broadcast_edit_time(
    callback_query: CallbackQuery,
    callback_data: ScheduledBroadcastCallback,
    session: AsyncSession,
    state: FSMContext,
):
    item = await get_scheduled_broadcast(session, callback_data.broadcast_id)
    if item is None:
        await callback_query.message.edit_text(
            "❌ Запланированная рассылка не найдена.",
            reply_markup=build_admin_back_kb("sender"),
        )
        return
    await state.update_data(edit_broadcast_id=item.id, edit_page=callback_data.page)
    await state.set_state(AdminSender.waiting_for_edit_schedule_datetime)
    await callback_query.message.edit_text(
        "🕒 Введите новое время отправки по Москве в формате <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>.",
        reply_markup=build_admin_back_kb("sender"),
    )


@router.message(AdminSender.waiting_for_edit_schedule_datetime, IsAdminFilter())
async def handle_scheduled_broadcast_edit_time_input(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    data = await state.get_data()
    broadcast_id = data.get("edit_broadcast_id")
    page = int(data.get("edit_page", 0))
    try:
        scheduled_for = parse_moscow_datetime_input(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    if scheduled_for <= datetime.now(timezone.utc):
        await message.answer("⚠️ Время отправки должно быть в будущем.")
        return
    updated = await update_scheduled_broadcast(
        session,
        broadcast_id,
        scheduled_for=scheduled_for,
        status="scheduled",
        error_text=None,
    )
    await state.clear()
    if updated is None:
        await message.answer("❌ Эту рассылку уже нельзя изменить.", reply_markup=build_admin_back_kb("sender"))
        return
    await message.answer(
        _scheduled_broadcast_text(updated),
        reply_markup=build_scheduled_broadcast_detail_kb(updated, page=page),
    )


@router.callback_query(ScheduledBroadcastCallback.filter(F.action == "edit_audience"), IsAdminFilter())
async def handle_scheduled_broadcast_edit_audience(
    callback_query: CallbackQuery,
    callback_data: ScheduledBroadcastCallback,
    session: AsyncSession,
    state: FSMContext,
):
    item = await get_scheduled_broadcast(session, callback_data.broadcast_id)
    if item is None:
        await callback_query.message.edit_text(
            "❌ Запланированная рассылка не найдена.",
            reply_markup=build_admin_back_kb("sender"),
        )
        return
    await state.update_data(edit_broadcast_id=item.id, edit_page=callback_data.page, edit_action="audience")
    await callback_query.message.edit_text(
        "👥 Выберите новую аудиторию для рассылки:",
        reply_markup=build_sender_kb(include_scheduled=False),
    )


@router.callback_query(ScheduledBroadcastCallback.filter(F.action == "cancel"), IsAdminFilter())
async def handle_scheduled_broadcast_cancel(
    callback_query: CallbackQuery,
    callback_data: ScheduledBroadcastCallback,
    session: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    item = await cancel_scheduled_broadcast(session, callback_data.broadcast_id)
    if item is None:
        await callback_query.message.edit_text(
            "❌ Эту рассылку уже нельзя отменить.",
            reply_markup=build_admin_back_kb("sender"),
        )
        return
    await callback_query.message.edit_text(
        _scheduled_broadcast_text(item),
        reply_markup=build_scheduled_broadcast_detail_kb(item, page=callback_data.page),
    )


@router.callback_query(ScheduledBroadcastCallback.filter(F.action == "send_now"), IsAdminFilter())
async def handle_scheduled_broadcast_send_now(
    callback_query: CallbackQuery,
    callback_data: ScheduledBroadcastCallback,
    session: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    item = await start_scheduled_broadcast(session, callback_data.broadcast_id)
    if item is None:
        await callback_query.message.edit_text(
            "❌ Эту рассылку уже нельзя отправить сейчас.",
            reply_markup=build_admin_back_kb("sender"),
        )
        return
    await callback_query.message.edit_text("⏳ Запускаю рассылку...")
    await release_session_early(session)
    try:
        result = await execute_scheduled_broadcast(item, bot=callback_query.bot)
    except Exception as exc:
        failed_item = await mark_scheduled_broadcast_failed(session, callback_data.broadcast_id, str(exc))
        await callback_query.message.edit_text(
            _scheduled_broadcast_text(failed_item),
            reply_markup=build_scheduled_broadcast_detail_kb(failed_item, page=callback_data.page),
        )
        return
    if result.get("success"):
        updated = await mark_scheduled_broadcast_sent(session, callback_data.broadcast_id, result)
        await callback_query.message.edit_text(
            _scheduled_broadcast_text(updated),
            reply_markup=build_scheduled_broadcast_detail_kb(updated, page=callback_data.page),
        )
        stats = result.get("stats") or {
            "total_duration": 0,
            "success_count": 0,
            "failed_count": 0,
            "blocked_users": 0,
            "avg_speed": 0,
        }
        await callback_query.message.answer(
            _broadcast_result_text(result.get("recipients", 0), stats),
            reply_markup=build_admin_back_kb("sender"),
        )
        return
    failed_item = await mark_scheduled_broadcast_failed(
        session,
        callback_data.broadcast_id,
        result.get("message", "Broadcast failed"),
    )
    await callback_query.message.edit_text(
        _scheduled_broadcast_text(failed_item),
        reply_markup=build_scheduled_broadcast_detail_kb(failed_item, page=callback_data.page),
    )


@router.callback_query(F.data == "cancel_broadcast", IsAdminFilter())
async def handle_broadcast_cancel(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text(
        "🚫 Рассылка отменена.",
        reply_markup=build_admin_back_kb("sender"),
    )
    await state.clear()
