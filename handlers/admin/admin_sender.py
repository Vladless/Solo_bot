from datetime import datetime
from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from filters.admin import IsAdminFilter
from keyboards.admin.panel_kb import AdminPanelCallback, build_admin_back_kb
from keyboards.admin.sender_kb import AdminSenderCallback, build_sender_kb
from logger import logger

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
    AdminSenderCallback.filter(),
    IsAdminFilter(),
)
async def handle_sender_callback(callback_query: CallbackQuery, callback_data: AdminSenderCallback, state: FSMContext):
    await callback_query.message.edit_text(
        text="✍️ Введите текст сообщения для рассылки:",
        reply_markup=build_admin_back_kb("sender"),
    )
    await state.update_data(type=callback_data.type)
    await state.set_state(AdminSender.waiting_for_message)


@router.message(
    AdminSender.waiting_for_message,
    IsAdminFilter(),
)
async def handle_message_input(message: Message, state: FSMContext, session: Any):
    text_message = message.text

    try:
        state_data = await state.get_data()
        send_to = state_data.get("type", "all")

        if send_to == "subscribed":
            tg_ids = await session.fetch(
                """
                    SELECT DISTINCT c.tg_id 
                    FROM connections c
                    JOIN keys k ON c.tg_id = k.tg_id
                    WHERE k.expiry_time > $1
                """,
                int(datetime.utcnow().timestamp() * 1000),
            )
        elif send_to == "unsubscribed":
            tg_ids = await session.fetch(
                """
                    SELECT c.tg_id 
                    FROM connections c
                    LEFT JOIN keys k ON c.tg_id = k.tg_id
                    GROUP BY c.tg_id
                    HAVING COUNT(k.tg_id) = 0 OR MAX(k.expiry_time) <= $1
                """,
                int(datetime.utcnow().timestamp() * 1000),
            )
        else:
            tg_ids = await session.fetch("SELECT DISTINCT tg_id FROM connections")

        total_users = len(tg_ids)
        success_count = 0

        for record in tg_ids:
            tg_id = record["tg_id"]
            try:
                await message.bot.send_message(chat_id=tg_id, text=text_message)
                success_count += 1
            except Exception as e:
                logger.error(e)

        text = (
            f"📤 Рассылка завершена!"
            f"\n\n👥 Всего пользователей: {total_users}"
            f"\n✅ Доставлено: {success_count}"
            f"\n❌ Не доставлено: {total_users - success_count}"
        )

        await message.answer(text=text, reply_markup=build_admin_back_kb("stats"))
    except Exception as e:
        logger.error(f"❗ Ошибка при подключении к базе данных: {e}")

    await state.clear()
