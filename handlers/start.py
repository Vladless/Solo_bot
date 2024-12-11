import os
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from database import add_connection, add_referral, check_connection_exists, get_trial, use_trial
from handlers.keys.trial_key import create_trial_key
from handlers.texts import INSTRUCTIONS_TRIAL, WELCOME_TEXT, get_about_vpn
from keyboards.start_kb import build_start_kb, build_connect_kb, build_about_kb

router = Router()


@router.callback_query(F.data == "start")
async def handle_start_callback_query(callback_query: CallbackQuery, state: FSMContext, session: Any, admin: bool):
    await start_command(callback_query.message, state, session, admin)


@router.message(Command("start"))
async def start_command(message: Message, state: FSMContext, session: Any, admin: bool):
    await state.clear()
    if message.text:
        try:
            referrer_tg_id = int(message.text.split("referral_")[1])
            await add_referral(message.chat.id, referrer_tg_id, session)
        except (ValueError, IndexError):
            pass
        connection_exists = await check_connection_exists(message.chat.id)
        if not connection_exists:
            await add_connection(tg_id=message.chat.id, session=session)

    # Get data
    trial_status = await get_trial(message.chat.id, session)
    image_path = os.path.join("img", "pic.jpg")

    # Build keyboard
    kb = build_start_kb(trial_status, admin)

    # Answer message
    if os.path.isfile(image_path):
        with open(image_path, "rb") as image_from_buffer:
            await message.answer_photo(
                photo=BufferedInputFile(image_from_buffer.read(), filename="pic.jpg"),
                caption=WELCOME_TEXT,
                reply_markup=kb,
            )
    else:
        await message.answer(
            text=WELCOME_TEXT,
            reply_markup=kb,
        )


@router.callback_query(F.data == "connect_vpn")
async def handle_connect_vpn(callback_query: CallbackQuery, session: Any):
    user_id = callback_query.message.chat.id

    # Get trial key info
    trial_key_info = await create_trial_key(user_id, session)

    if "error" in trial_key_info:
        await callback_query.message.answer(trial_key_info["error"])
    else:
        await use_trial(user_id, session)

        # Prepare text
        text = (
            f"🔑 <b>Ваш персональный ключ доступа:</b>\n"
            f"<code>{trial_key_info['key']}</code>\n\n"
            f"📋 <b>Быстрая инструкция по подключению:</b>\n{INSTRUCTIONS_TRIAL}"
        )

        # Build keyboard
        kb = build_connect_kb(trial_key_info)

        # Answer message
        await callback_query.message.answer(
            text=text,
            reply_markup=kb,
        )


@router.callback_query(F.data == "about_vpn")
async def handle_about_vpn(callback_query: CallbackQuery):
    # Answer message
    await callback_query.message.answer(
        text=get_about_vpn("3.2.21-Release"),
        reply_markup=build_about_kb(),
    )
