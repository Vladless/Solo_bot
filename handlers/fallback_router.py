from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.bootstrap import MODES_CONFIG
from handlers.support_triage import TriageCallback
from handlers.utils import build_support_button
from hooks.hooks import run_hooks
from logger import logger
from settings.buttons import HAVE_PROBLEM, MAIN_MENU, SUPPORT
from settings.texts import FALLBACK_MESSAGE


fallback_router = Router()

_START_WORDS = {"старт", "начать", "начало", "меню", "menu", "start", "привет", "здравствуйте", "хай", "hi", "hello"}
_BUY_WORDS = {"купить", "оформить", "подписаться", "подписка", "приобрести", "buy", "тариф", "тарифы"}


def _match_keyword_intent(text: str | None) -> str | None:
    cleaned = (text or "").strip().lower().lstrip("/").strip(" .!?,")
    if not cleaned:
        return None
    words = cleaned.split()
    candidates = {cleaned}
    if len(words) <= 3:
        candidates.add(words[0])
    if candidates & _START_WORDS:
        return "start"
    if candidates & _BUY_WORDS:
        return "buy"
    return None


@fallback_router.message(F.text)
async def handle_unhandled_messages(message: Message, session: Any, state: FSMContext = None, admin: bool = False):
    intent = _match_keyword_intent(message.text)
    if intent == "start":
        try:
            from handlers.start import start_entry

            await start_entry(message, state, session, admin)
            return
        except Exception as e:
            logger.error(f"[Fallback] start по ключевому слову не удался: {e}")
    elif intent == "buy":
        try:
            from handlers.keys.key_create import confirm_create_new_key

            await confirm_create_new_key(message, state, session)
            return
        except Exception as e:
            logger.error(f"[Fallback] buy по ключевому слову не удался: {e}")

    await run_hooks(
        "user_message",
        user_id=message.from_user.id,
        message_text=message.text,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        session=session,
        message=message,
    )

    keyboard = InlineKeyboardBuilder()
    if bool((MODES_CONFIG or {}).get("SUPPORT_TRIAGE_ENABLED", False)):
        keyboard.row(InlineKeyboardButton(text=HAVE_PROBLEM, callback_data=TriageCallback(action="root").pack()))
    else:
        support_btn = await build_support_button()
        if support_btn:
            keyboard.row(support_btn)
    keyboard.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
    await message.answer(
        FALLBACK_MESSAGE,
        reply_markup=keyboard.as_markup(),
    )
