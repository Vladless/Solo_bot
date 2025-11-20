from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Server
from filters.admin import IsAdminFilter
from logger import logger

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb
from .keyboard import AdminSenderCallback, build_clusters_kb, build_sender_kb
from .sender_states import AdminSender
from .sender_service import BroadcastService
from .sender_utils import get_recipients, parse_message_buttons


router = Router()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "sender"),
    IsAdminFilter(),
)
async def handle_sender(callback_query: CallbackQuery):
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
async def handle_cluster_select(callback_query: CallbackQuery, session: AsyncSession):
    result = await session.execute(select(Server.cluster_name).distinct())
    clusters = result.mappings().all()

    await callback_query.message.answer(
        "✍️ Выберите кластер для рассылки сообщений:",
        reply_markup=build_clusters_kb(clusters),
    )


@router.callback_query(
    AdminSenderCallback.filter(F.type != "cluster-select"),
    IsAdminFilter(),
)
async def handle_broadcast_type(
    callback_query: CallbackQuery,
    callback_data: AdminSenderCallback,
    state: FSMContext
):
    await callback_query.message.edit_text(
        text=(
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
            '<code>{"text": "🎁 Забрать купон", "url": "https://t.me/cupons"}</code>\n'
            '<code>{"text": "📢 Канал", "url": "https://t.me/channel"}</code>'
        ),
        reply_markup=build_admin_back_kb("sender"),
    )
    await state.update_data(type=callback_data.type, cluster_name=callback_data.data)
    await state.set_state(AdminSender.waiting_for_message)


@router.message(AdminSender.waiting_for_message, IsAdminFilter())
async def handle_message_input(message: Message, state: FSMContext, session: AsyncSession):
    original_text = message.html_text or message.text or message.caption or ""
    photo = message.photo[-1].file_id if message.photo else None

    clean_text, keyboard = parse_message_buttons(original_text)

    max_len = 1024 if photo else 4096
    if len(clean_text) > max_len:
        await message.answer(
            f"⚠️ Сообщение слишком длинное.\n"
            f"Максимум: <b>{max_len}</b> символов, сейчас: <b>{len(clean_text)}</b>.",
            reply_markup=build_admin_back_kb("sender"),
        )
        await state.clear()
        return

    data = await state.get_data()
    send_to = data.get("type", "all")
    cluster_name = data.get("cluster_name")
    _, user_count = await get_recipients(session, send_to, cluster_name)

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

    await state.update_data(
        text=clean_text,
        photo=photo,
        keyboard=keyboard.model_dump() if keyboard else None
    )
    await state.set_state(AdminSender.preview)

    if photo:
        await message.answer_photo(
            photo=photo,
            caption=clean_text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
    else:
        await message.answer(
            text=clean_text,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    await message.answer(
        f"👀 Это предпросмотр рассылки.\n"
        f"👥 Количество получателей: <b>{user_count}</b>\n\n"
        f"Отправить?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📤 Отправить",
                        callback_data="send_broadcast"
                    ),
                    InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data="cancel_broadcast"
                    ),
                ]
            ]
        ),
    )


@router.callback_query(F.data == "send_broadcast", IsAdminFilter())
async def handle_broadcast_confirm(
    callback_query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession
):
    data = await state.get_data()
    text_message = data.get("text")
    photo = data.get("photo")
    keyboard_data = data.get("keyboard")
    send_to = data.get("type", "all")
    cluster_name = data.get("cluster_name")

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

    tg_ids, total_users = await get_recipients(session, send_to, cluster_name)

    if not tg_ids:
        await callback_query.message.edit_text(
            "⚠️ Не найдено получателей для рассылки.",
            reply_markup=build_admin_back_kb("sender"),
        )
        await state.clear()
        return

    await callback_query.message.edit_text(
        f"📤 <b>Рассылка начата!</b>\n"
        f"👥 Количество получателей: {total_users}"
    )

    messages = []
    for tg_id in tg_ids:
        message_data = {
            "tg_id": tg_id,
            "text": text_message,
            "photo": photo,
            "keyboard": keyboard
        }
        messages.append(message_data)

    broadcast_service = BroadcastService(
        bot=callback_query.bot,
        session=session,
        messages_per_second=35
    )
    
    stats = await broadcast_service.broadcast(messages, workers=5)

    duration_minutes = int(stats["total_duration"] // 60)
    duration_seconds = int(stats["total_duration"] % 60)
    duration_str = (
        f"{duration_minutes} мин {duration_seconds} сек"
        if duration_minutes > 0
        else f"{duration_seconds} сек"
    )

    await callback_query.message.answer(
        text=(
            f"📤 <b>Рассылка завершена!</b>\n\n"
            f"👥 <b>Количество получателей:</b> {total_users}\n"
            f"✅ <b>Доставлено:</b> {stats['success_count']}\n"
            f"❌ <b>Не доставлено:</b> {stats['failed_count']}\n"
            f"🚫 <b>Заблокировавших бота:</b> {stats['blocked_users']}\n\n"
            f"⏱️ <b>Время выполнения:</b> {duration_str}\n"
            f"⚡ <b>Средняя скорость:</b> {stats['avg_speed']:.1f} сообщений/сек"
        ),
        reply_markup=build_admin_back_kb("sender"),
    )
    await state.clear()


@router.callback_query(F.data == "cancel_broadcast", IsAdminFilter())
async def handle_broadcast_cancel(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text(
        "🚫 Рассылка отменена.",
        reply_markup=build_admin_back_kb("sender"),
    )
    await state.clear()
