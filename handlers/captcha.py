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

    await state.update_data(
        correct_emoji=correct_emoji,
        message_id=message.message_id,
        chat_id=message.chat.id,
        original_text=message.text,
    )

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
    from handlers.start import start_command

    selected_emoji = callback.data.split("captcha_")[1]
    state_data = await state.get_data()
    correct_emoji = state_data.get("correct_emoji")
    message_id = state_data.get("message_id")
    chat_id = state_data.get("chat_id")

    if not message_id or not chat_id:
        target_message = callback.message
    else:
        try:
            target_message = await callback.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=callback.message.text,
            )
        except Exception:
            target_message = callback.message

    if selected_emoji == correct_emoji:
        logger.info(f"Пользователь {callback.message.chat.id} успешно прошел капчу")
        await start_command(target_message, state, session, admin, captcha=False)
    else:
        logger.warning(f"Пользователь {callback.message.chat.id} неверно ответил на капчу")
        captcha = await generate_captcha(target_message, state)
        await edit_or_send_message(
            target_message=target_message,
            text=captcha["text"],
            reply_markup=captcha["markup"],
        )
