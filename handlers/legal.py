import os

from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.legal_config import LEGAL_CONFIG, is_legal_enabled, legal_doc_url
from database.models import User
from handlers.utils import edit_or_send_message
from logger import logger
from settings.buttons import LEGAL_ACCEPT, LEGAL_OFFER, LEGAL_PRIVACY, LEGAL_TERMS


router = Router(name="legal")

DOCS: tuple[tuple[str, str], ...] = (
    ("LEGAL_PRIVACY_URL", LEGAL_PRIVACY),
    ("LEGAL_TERMS_URL", LEGAL_TERMS),
    ("LEGAL_OFFER_URL", LEGAL_OFFER),
)


def legal_doc_buttons() -> list[InlineKeyboardButton]:
    """Кнопки документов: заполненные ссылки в том порядке, в каком их задал админ."""
    if not is_legal_enabled():
        return []
    buttons = []
    for key, label in DOCS:
        url = legal_doc_url(key)
        if url:
            buttons.append(InlineKeyboardButton(text=label, url=url))
    return buttons


def legal_intro_text() -> str:
    return str(LEGAL_CONFIG.get("LEGAL_INTRO_TEXT") or "").strip()


async def legal_accepted(session: AsyncSession, tg_id: int) -> bool:
    stmt = select(User.legal_accepted_at).where(User.tg_id == tg_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return row is not None


async def show_legal_gate(message: Message) -> None:
    kb = InlineKeyboardBuilder()
    for button in legal_doc_buttons():
        kb.row(button)
    kb.row(InlineKeyboardButton(text=LEGAL_ACCEPT, callback_data="legal_accept"))
    await edit_or_send_message(
        message,
        legal_intro_text(),
        reply_markup=kb.as_markup(),
        media_path=os.path.join("img", "pic.jpg"),
    )


async def legal_gate_passed(message: Message, session: AsyncSession, tg_id: int) -> bool:
    """False — клиенту показан экран согласия, дальше по цепочке идти нельзя."""
    if not is_legal_enabled():
        return True
    try:
        if await legal_accepted(session, tg_id):
            return True
    except Exception as exc:
        logger.error(f"[Legal] Не удалось прочитать согласие для {tg_id}: {exc}")
        return True
    await show_legal_gate(message)
    return False


@router.callback_query(F.data == "legal_accept")
async def accept_legal(callback: CallbackQuery, state: FSMContext, session: AsyncSession, admin: bool) -> None:
    tg_id = callback.from_user.id
    await session.execute(update(User).where(User.tg_id == tg_id).values(legal_accepted_at=datetime.utcnow()))
    await session.commit()

    from handlers.start import process_start_logic

    await callback.answer()
    data = await state.get_data()
    await process_start_logic(
        callback.message,
        state,
        session,
        admin,
        data.get("original_text"),
        data.get("user_data"),
    )
