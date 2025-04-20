from datetime import datetime
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from filters.admin import IsAdminFilter
from logger import logger

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb
from .keyboard import AdminSenderCallback, build_clusters_kb, build_sender_kb


router = Router()


class AdminSender(StatesGroup):
    waiting_for_message = State()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "sender"),
    IsAdminFilter(),
)
async def handle_sender(callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        text="✍️ Выберите группу пользователей для рассылки:",
        reply_markup=build_sender_kb(),
    )


@router.callback_query(
    AdminSenderCallback.filter(F.type != "cluster-select"),
    IsAdminFilter(),
)
async def handle_sender_callback_text(
    callback_query: CallbackQuery, callback_data: AdminSenderCallback, state: FSMContext
):
    await callback_query.message.edit_text(
        text="✍️ Введите текст сообщения для рассылки:",
        reply_markup=build_admin_back_kb("sender"),
    )
    await state.update_data(type=callback_data.type, cluster_name=callback_data.data)
    await state.set_state(AdminSender.waiting_for_message)


@router.callback_query(
    AdminSenderCallback.filter(F.type == "cluster-select"),
    IsAdminFilter(),
)
async def handle_sender_callback(callback_query: CallbackQuery, session: Any):
    clusters = await session.fetch("SELECT DISTINCT cluster_name FROM servers")
    await callback_query.message.answer(
        "✍️ Выберите кластер для рассылки сообщений:",
        reply_markup=build_clusters_kb(clusters),
    )


@router.message(AdminSender.waiting_for_message, IsAdminFilter())
async def handle_message_input(message: Message, state: FSMContext, session: Any):
    """
    Обрабатывает ввод сообщения для рассылки (поддержка текста + фото).
    """
    text_message = message.html_text if message.text else None
    photo = message.photo[-1].file_id if message.photo else None
    photo_url = message.caption if message.photo and message.caption and message.caption.startswith("http") else None

    if not text_message and message.caption:
        text_message = message.caption

    if not text_message and not photo and not photo_url:
        await message.answer("⚠ Ошибка! Отправьте текст или изображение для рассылки.")
        return

    state_data = await state.get_data()
    send_to = state_data.get("type", "all")

    now = int(datetime.utcnow().timestamp() * 1000)

    if send_to == "subscribed":
        tg_ids = await session.fetch(
            """
            SELECT DISTINCT u.tg_id
            FROM users u
            JOIN keys k ON u.tg_id = k.tg_id
            WHERE k.expiry_time > $1
            """,
            now,
        )
    elif send_to == "unsubscribed":
        tg_ids = await session.fetch(
            """
            SELECT u.tg_id
            FROM users u
            LEFT JOIN keys k ON u.tg_id = k.tg_id
            GROUP BY u.tg_id
            HAVING COUNT(k.tg_id) = 0 OR MAX(k.expiry_time) <= $1
            """,
            now,
        )
    elif send_to == "untrial":
        tg_ids = await session.fetch("SELECT DISTINCT tg_id FROM users WHERE tg_id NOT IN (SELECT tg_id FROM keys)")
    elif send_to == "cluster":
        cluster_name = state_data.get("cluster_name")
        tg_ids = await session.fetch(
            """
            SELECT DISTINCT u.tg_id
            FROM users u
            JOIN keys k ON u.tg_id = k.tg_id
            JOIN servers s ON k.server_id = s.cluster_name
            WHERE s.cluster_name = $1
            """,
            cluster_name,
        )
    elif send_to == "hotleads":
        tg_ids = await session.fetch(
            """
            SELECT DISTINCT u.tg_id
            FROM users u
            JOIN payments p ON u.tg_id = p.tg_id
            LEFT JOIN keys k ON u.tg_id = k.tg_id
            WHERE p.status = 'success'
            AND k.tg_id IS NULL
            """
        )
    else:
        tg_ids = await session.fetch("SELECT DISTINCT tg_id FROM users")

    total_users = len(tg_ids)
    success_count = 0

    text = f"📤 <b>Рассылка начата!</b>\n👥 Количество получателей: {total_users}"

    await message.answer(text=text)

    for record in tg_ids:
        tg_id = record["tg_id"]
        try:
            if photo or photo_url:
                await message.bot.send_photo(
                    chat_id=tg_id, photo=photo if photo else photo_url, caption=text_message, parse_mode="HTML"
                )
            else:
                await message.bot.send_message(chat_id=tg_id, text=text_message, parse_mode="HTML")

            success_count += 1
        except Exception as e:
            logger.error(f"❌ Ошибка отправки пользователю {tg_id}: {e}")

    text = (
        f"📤 <b>Рассылка завершена!</b>\n\n"
        f"👥 <b>Количество получателей:</b> {total_users}\n"
        f"✅ <b>Доставлено:</b> {success_count}\n"
        f"❌ <b>Не доставлено:</b> {total_users - success_count}"
    )

    await message.answer(text=text, reply_markup=build_admin_back_kb("sender"))
    await state.clear()
