import random
import secrets

from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.texts import CAPTCHA_EMOJIS, CAPTCHA_PROMPT_MSG
from logger import logger

from .utils import edit_or_send_message


router = Router()


async def generate_captcha(message: Message, state: FSMContext):
    correct_emoji, correct_text = secrets.choice(list(CAPTCHA_EMOJIS.items()))
    wrong_emojis = random.sample([e for e in CAPTCHA_EMOJIS.keys() if e != correct_emoji], 3)

    all_emojis = [correct_emoji] + wrong_emojis
    random.shuffle(all_emojis)

    state_data = await state.get_data()

    if "user_data" not in state_data:
        from_user = message.from_user
        if not from_user:
            logger.warning("[CAPTCHA] ❗ from_user отсутствует — невозможно сохранить user_data")
            return None

        await state.update_data(
            user_data={
                "tg_id": from_user.id,
                "username": getattr(from_user, "username", None),
                "first_name": getattr(from_user, "first_name", None),
                "last_name": getattr(from_user, "last_name", None),
                "language_code": getattr(from_user, "language_code", None),
                "is_bot": getattr(from_user, "is_bot", False),
            }
        )

    update_data = {
        "correct_emoji": correct_emoji,
        "message_id": message.message_id,
        "chat_id": message.chat.id,
    }

    state_data = await state.get_data()
    if "original_text" not in state_data:
        update_data["original_text"] = message.text

    await state.update_data(**update_data)

    builder = InlineKeyboardBuilder()
    for emoji in all_emojis:
        builder.button(text=emoji, callback_data=f"captcha_{emoji}")
    builder.adjust(2, 2)

    return {
        "text": CAPTCHA_PROMPT_MSG.format(correct_text=correct_text),
        "markup": builder.as_markup(),
    }


@router.callback_query(F.data.startswith("captcha_"))
async def check_captcha(callback: CallbackQuery, state: FSMContext, session: Any, admin: bool):
    from handlers.start import process_start_logic

    selected_emoji = callback.data.split("captcha_")[1]
    state_data = await state.get_data()
    correct_emoji = state_data.get("correct_emoji")
    original_text = state_data.get("original_text")
    user_data = state_data.get("user_data")

    target_message = callback.message

    if selected_emoji == correct_emoji:
        logger.info(f"Пользователь {callback.from_user.id} успешно прошел капчу")
        logger.debug(f"[CAPTCHA] user_data передано в process_start_logic: {user_data}")
        await process_start_logic(
            message=target_message,
            state=state,
            session=session,
            admin=admin,
            text_to_process=original_text,
            user_data=user_data,
        )
    else:
        logger.warning(f"Пользователь {callback.from_user.id} неверно ответил на капчу")
        captcha = await generate_captcha(target_message, state)
        if captcha:
            await edit_or_send_message(
                target_message=target_message,
                text=captcha["text"],
                reply_markup=captcha["markup"],
            )
