import asyncio
import json
import re
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Key, Payment, Server, User
from filters.admin import IsAdminFilter
from logger import logger
from .keyboard import AdminSenderCallback, build_clusters_kb, build_sender_kb
from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb

router = Router()


async def send_broadcast_batch(bot, messages, batch_size=15):
    results = []

    for i in range(0, len(messages), batch_size):
        batch = messages[i : i + batch_size]
        tasks = []

        for msg in batch:
            tg_id = msg["tg_id"]
            text = msg["text"]
            photo = msg.get("photo")
            keyboard = msg.get("keyboard")

            if photo:
                task = bot.send_photo(
                    chat_id=tg_id, photo=photo, caption=text, parse_mode="HTML", reply_markup=keyboard
                )
            else:
                task = bot.send_message(chat_id=tg_id, text=text, parse_mode="HTML", reply_markup=keyboard)
            tasks.append(task)

        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in batch_results:
            if isinstance(result, Exception):
                logger.error(f"❌ Ошибка отправки: {result}")
                results.append(False)
            else:
                results.append(True)

        if i + batch_size < len(messages):
            await asyncio.sleep(1.0)

    return results


class AdminSender(StatesGroup):
    waiting_for_message = State()
    preview = State()


def parse_message_buttons(text: str) -> tuple[str, InlineKeyboardMarkup | None]:
    if "BUTTONS:" not in text:
        return text, None

    parts = text.split("BUTTONS:", 1)
    clean_text = parts[0].strip()
    buttons_text = parts[1].strip()

    if not buttons_text:
        return clean_text, None

    buttons = []
    button_lines = [line.strip() for line in buttons_text.split("\n") if line.strip()]

    for line in button_lines:
        try:
            cleaned_line = re.sub(r'<tg-emoji emoji-id="[^"]*">([^<]*)</tg-emoji>', r"\1", line)

            button_data = json.loads(cleaned_line)

            if not isinstance(button_data, dict) or "text" not in button_data:
                logger.warning(f"Неверный формат кнопки: {line}")
                continue

            text_btn = button_data["text"]

            if "callback" in button_data:
                callback_data = button_data["callback"]
                if len(callback_data) > 64:
                    logger.warning(f"Callback слишком длинный: {callback_data}")
                    continue
                button = InlineKeyboardButton(text=text_btn, callback_data=callback_data)
            elif "url" in button_data:
                url = button_data["url"]
                button = InlineKeyboardButton(text=text_btn, url=url)
            else:
                logger.warning(f"Кнопка без действия: {line}")
                continue

            buttons.append([button])

        except json.JSONDecodeError as e:
            logger.warning(f"Ошибка парсинга JSON кнопки: {line} - {e}")
            continue
        except Exception as e:
            logger.error(f"Ошибка создания кнопки: {line} - {e}")
            continue

    if not buttons:
        return clean_text, None

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return clean_text, keyboard


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
            logger.debug("[Sender] Сообщение не изменено, Telegram отклонил редактирование")
        else:
            raise


@router.callback_query(
    AdminSenderCallback.filter(F.type != "cluster-select"),
    IsAdminFilter(),
)
async def handle_sender_callback_text(
    callback_query: CallbackQuery, callback_data: AdminSenderCallback, state: FSMContext
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


@router.callback_query(
    AdminSenderCallback.filter(F.type == "cluster-select"),
    IsAdminFilter(),
)
async def handle_sender_callback(callback_query: CallbackQuery, session: AsyncSession):
    result = await session.execute(select(Server.cluster_name).distinct())
    clusters = result.mappings().all()

    await callback_query.message.answer(
        "✍️ Выберите кластер для рассылки сообщений:",
        reply_markup=build_clusters_kb(clusters),
    )


@router.message(AdminSender.waiting_for_message, IsAdminFilter())
async def handle_message_input(message: Message, state: FSMContext):
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

    await state.update_data(text=clean_text, photo=photo, keyboard=keyboard.model_dump() if keyboard else None)
    await state.set_state(AdminSender.preview)

    if photo:
        await message.answer_photo(photo=photo, caption=clean_text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer(text=clean_text, parse_mode="HTML", reply_markup=keyboard)

    await message.answer(
        "👀 Это предпросмотр рассылки.\nОтправить?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="📤 Отправить", callback_data="send_message"),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_message"),
                ]
            ]
        ),
    )


@router.callback_query(F.data == "send_message", IsAdminFilter())
async def handle_send_confirm(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    text_message = data.get("text")
    photo = data.get("photo")
    keyboard_data = data.get("keyboard")
    send_to = data.get("type", "all")
    cluster_name = data.get("cluster_name")
    now_ms = int(datetime.utcnow().timestamp() * 1000)

    keyboard = None
    if keyboard_data:
        try:
            keyboard = InlineKeyboardMarkup.model_validate(keyboard_data)
        except Exception as e:
            logger.error(f"Ошибка восстановления клавиатуры: {e}")

    query = None
    if send_to == "subscribed":
        query = select(distinct(User.tg_id)).join(Key).where(Key.expiry_time > now_ms)
    elif send_to == "unsubscribed":
        subquery = (
            select(User.tg_id)
            .outerjoin(Key, User.tg_id == Key.tg_id)
            .group_by(User.tg_id)
            .having(func.count(Key.tg_id) == 0)
            .union_all(
                select(User.tg_id)
                .join(Key, User.tg_id == Key.tg_id)
                .group_by(User.tg_id)
                .having(func.max(Key.expiry_time) <= now_ms)
            )
        )
        query = select(distinct(subquery.c.tg_id))
    elif send_to == "untrial":
        subquery = select(Key.tg_id)
        query = select(distinct(User.tg_id)).where(~User.tg_id.in_(subquery) & User.trial.in_([0, -1]))
    elif send_to == "cluster":
        query = (
            select(distinct(User.tg_id))
            .join(Key, User.tg_id == Key.tg_id)
            .join(Server, Key.server_id == Server.cluster_name)
            .where(Server.cluster_name == cluster_name)
        )
    elif send_to == "hotleads":
        subquery = select(Key.tg_id)
        query = (
            select(distinct(User.tg_id))
            .join(Payment, User.tg_id == Payment.tg_id)
            .where(Payment.status == "success")
            .where(~User.tg_id.in_(subquery))
        )
    else:
        query = select(distinct(User.tg_id))

    result = await session.execute(query)
    tg_ids = [row[0] for row in result.all()]

    total_users = len(tg_ids)
    success_count = 0

    await callback_query.message.edit_text(f"📤 <b>Рассылка начата!</b>\n👥 Количество получателей: {total_users}")

    messages = []
    for tg_id in tg_ids:
        message_data = {"tg_id": tg_id, "text": text_message, "photo": photo, "keyboard": keyboard}
        messages.append(message_data)

    results = await send_broadcast_batch(bot=callback_query.bot, messages=messages, batch_size=15)

    success_count = sum(1 for result in results if result)

    await callback_query.message.answer(
        text=(
            f"📤 <b>Рассылка завершена!</b>\n\n"
            f"👥 <b>Количество получателей:</b> {total_users}\n"
            f"✅ <b>Доставлено:</b> {success_count}\n"
            f"❌ <b>Не доставлено:</b> {total_users - success_count}"
        ),
        reply_markup=build_admin_back_kb("sender"),
    )
    await state.clear()


@router.callback_query(F.data == "cancel_message", IsAdminFilter())
async def handle_send_cancel(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text(
        "🚫 Рассылка отменена.",
        reply_markup=build_admin_back_kb("sender"),
    )
    await state.clear()
