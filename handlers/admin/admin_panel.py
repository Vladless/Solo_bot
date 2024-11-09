import subprocess
from datetime import datetime

import asyncpg
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from backup import backup_database
from bot import bot
from config import ADMIN_ID, DATABASE_URL
from handlers.commands import send_message_to_all_clients
from middlewares.admin import admin_only

router = Router()


class UserEditorState(StatesGroup):
    waiting_for_tg_id = State()
    displaying_user_info = State()


@router.message(Command("admin"))
@admin_only()
async def handle_admin_command(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Статистика пользователей", callback_data="user_stats"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Редактор пользователей", callback_data="user_editor"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отправить сообщение всем клиентам",
                    callback_data="send_to_alls",
                )
            ],
            [InlineKeyboardButton(text="Создать бэкап", callback_data="backups")],
            [
                InlineKeyboardButton(
                    text="Перезапустить бота", callback_data="restart_bot"
                )
            ],
        ]
    )
    await bot.send_message(
        message.chat.id, "Панель администратора", reply_markup=keyboard
    )


@router.callback_query(lambda c: c.data == "user_stats")
@admin_only()
async def user_stats_menu(callback_query: CallbackQuery):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM connections")
        total_keys = await conn.fetchval("SELECT COUNT(*) FROM keys")
        total_referrals = await conn.fetchval("SELECT COUNT(*) FROM referrals")

        active_keys = await conn.fetchval(
            "SELECT COUNT(*) FROM keys WHERE expiry_time > $1",
            int(datetime.utcnow().timestamp() * 1000),
        )
        expired_keys = total_keys - active_keys

        stats_message = (
            f"🔹 <b>Общая статистика пользователей:</b>\n"
            f"• Всего пользователей: <b>{total_users}</b>\n"
            f"• Всего ключей: <b>{total_keys}</b>\n"
            f"• Всего рефералов: <b>{total_referrals}</b>\n"
            f"• Активные ключи: <b>{active_keys}</b>\n"
            f"• Истекшие ключи: <b>{expired_keys}</b>"
        )

        back_button = InlineKeyboardButton(
            text="Назад", callback_data="back_to_admin_menu"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button]])

        await callback_query.message.edit_text(
            stats_message, reply_markup=keyboard, parse_mode="HTML"
        )
    finally:
        await conn.close()

    await callback_query.answer()


@router.callback_query(lambda c: c.data == "send_to_alls")
@admin_only()
async def handle_send_to_all(callback_query: CallbackQuery, state: FSMContext):
    await send_message_to_all_clients(callback_query.message, state, from_panel=True)
    await callback_query.answer()


@router.callback_query(lambda c: c.data == "backups")
@admin_only()
async def handle_backup(message: Message):
    await message.answer("Запускаю бэкап базы данных...")
    await backup_database()
    await message.answer("Бэкап завершен и отправлен админу.")


@router.callback_query(lambda c: c.data == "restart_bot")
@admin_only()
async def handle_restart(callback_query: CallbackQuery):
    if callback_query.from_user.id == ADMIN_ID:
        try:
            result = subprocess.run(
                ["sudo", "systemctl", "restart", "bot.service"],
                check=True,
                capture_output=True,
                text=True,
            )
            await callback_query.message.answer("Бот успешно перезапущен.")
        except subprocess.CalledProcessError as e:
            await callback_query.message.answer(
                f"Бот будет перезапущен через 30 секунд {e.stderr}"
            )
    else:
        await callback_query.answer(
            "У вас нет доступа к этой команде.", show_alert=True
        )


@router.callback_query(lambda c: c.data == "user_editor")
@admin_only()
async def user_editor_menu(callback_query: CallbackQuery):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Поиск по имени ключа", callback_data="search_by_key_name"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Поиск по tg_id", callback_data="search_by_tg_id"
                )
            ],
            [
                InlineKeyboardButton(text="Назад", callback_data="back_to_admin_menu")
            ],  # Back button
        ]
    )
    await callback_query.message.edit_text(
        "Выберите метод поиска:", reply_markup=keyboard
    )


@router.callback_query(lambda c: c.data == "back_to_admin_menu")
@admin_only()
async def back_to_admin_menu(callback_query: CallbackQuery):
    try:
        await callback_query.message.delete()
    except Exception:
        pass

    tg_id = callback_query.from_user.id
    if tg_id == ADMIN_ID:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Статистика пользователей", callback_data="user_stats"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Редактор пользователей", callback_data="user_editor"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Отправить сообщение всем клиентам",
                        callback_data="send_to_alls",
                    )
                ],
                [InlineKeyboardButton(text="Создать бэкап", callback_data="backups")],
                [
                    InlineKeyboardButton(
                        text="Перезапустить бота", callback_data="restart_bot"
                    )
                ],
            ]
        )
        await bot.send_message(tg_id, "Панель администратора", reply_markup=keyboard)
    else:
        await bot.send_message(tg_id, "У вас нет доступа к этой команде.")


async def handle_error(tg_id, callback_query, message):
    await bot.edit_message_text(
        message, chat_id=tg_id, message_id=callback_query.message.message_id
    )
