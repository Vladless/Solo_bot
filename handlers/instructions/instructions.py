import os
from typing import Any

from aiogram import F, Router, types
from aiogram.types import BufferedInputFile

from handlers.texts import INSTRUCTION_PC, INSTRUCTIONS, KEY_MESSAGE
from keyboards.instructions.instructions_kb import build_instructions_kb, build_connect_pc_kb

router = Router()


@router.callback_query(F.data == "instructions")
async def send_instructions(callback_query: types.CallbackQuery):
    image_path = os.path.join("img", "instructions.jpg")
    if not os.path.isfile(image_path):
        await callback_query.message.answer("Файл изображения не найден.")
        return

    # Build keyboard
    kb = build_instructions_kb()

    with open(image_path, "rb") as image_from_buffer:
        await callback_query.message.answer_photo(
            photo=BufferedInputFile(image_from_buffer.read(), filename="instructions.jpg"),
            caption=INSTRUCTIONS,
            reply_markup=kb,
        )


@router.callback_query(F.data.startswith("connect_pc|"))
async def process_connect_pc(callback_query: types.CallbackQuery, session: Any):
    tg_id = callback_query.message.chat.id
    key_name = callback_query.data.split("|")[1]

    record = await session.fetchrow(
        """
        SELECT k.key
        FROM keys k
        WHERE k.tg_id = $1 AND k.email = $2
        """,
        tg_id,
        key_name,
    )

    if not record:
        await callback_query.message.answer("❌ <b>Ключ не найден. Проверьте имя ключа.</b> 🔍")
        return

    key = record["key"]
    key_message = KEY_MESSAGE.format(key)
    instruction_message = f"{key_message}{INSTRUCTION_PC}"

    # Build keyboard
    kb = build_connect_pc_kb(key)

    await callback_query.message.answer(
        text=instruction_message,
        reply_markup=kb,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
