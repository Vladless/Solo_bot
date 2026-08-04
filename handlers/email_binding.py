import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.settings.web_config import is_email_binding_enabled
from database.identities import get_identity_by_email, get_or_create_identity_for_tg
from handlers.utils import edit_or_send_message
from logger import logger
from settings.buttons import BACK


router = Router(name="email_binding")

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class EmailBindingState(StatesGroup):
    waiting_for_email = State()


@router.callback_query(F.data == "bind_email", flags={"popup": True})
async def prompt_email(callback: CallbackQuery, state: FSMContext, session) -> None:
    if not is_email_binding_enabled():
        await callback.answer("Привязка почты отключена", show_alert=True)
        return

    identity = await get_or_create_identity_for_tg(session, callback.from_user.id)
    if identity.email:
        await callback.answer("Почта уже привязана", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data="profile"))

    await edit_or_send_message(
        target_message=callback.message,
        text=(
            "📧 <b>Привязка почты</b>\n\n"
            "Укажите email — он понадобится для входа на сайт, "
            "если возникнут проблемы с Telegram.\n\n"
            "Отправьте адрес сообщением."
        ),
        reply_markup=builder.as_markup(),
    )
    await state.set_state(EmailBindingState.waiting_for_email)
    await callback.answer()


@router.message(EmailBindingState.waiting_for_email)
async def receive_email(message: Message, state: FSMContext, session) -> None:
    raw = (message.text or "").strip().lower()
    if not EMAIL_RE.match(raw) or len(raw) > 255:
        await message.answer("❌ Неверный формат email. Попробуйте ещё раз.")
        return

    existing = await get_identity_by_email(session, raw)
    if existing and existing.tg_id and existing.tg_id != message.from_user.id:
        await message.answer("❌ Этот email уже занят другим пользователем.")
        return

    identity = await get_or_create_identity_for_tg(session, message.from_user.id)
    if identity.email:
        await state.clear()
        await message.answer("ℹ️ Почта уже была привязана.")
        return

    if existing and existing.id != identity.id:
        logger.warning(
            "email_binding: identity collision tg_id=%s wants email=%s already on identity_id=%s",
            message.from_user.id,
            raw,
            existing.id,
        )
        await message.answer("❌ Этот email уже занят. Используйте другой.")
        return

    identity.email = raw
    await session.flush()
    await state.clear()

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 В кабинет", callback_data="profile"))
    await message.answer(
        f"✅ Почта <code>{raw}</code> привязана.",
        reply_markup=builder.as_markup(),
    )
