import re
import secrets

from html import escape as html_escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.settings.web_config import is_email_binding_enabled, is_email_binding_verification_enabled
from database.identities import (
    attach_email,
    get_identity_by_email,
    get_identity_by_tg_id,
    get_or_create_identity_for_tg,
)
from handlers.email_binding_context import EmailBindingState, clear_email_binding_context
from handlers.profile import process_callback_view_profile
from handlers.utils import edit_or_send_message
from services.email_binding import (
    confirm_email_binding_code,
    discard_email_binding_code,
    email_binding_codes_configured,
    send_email_binding_code,
)
from services.errors import ServiceError
from settings.buttons import BACK
from utils.web_email_codes import normalize_email


router = Router(name="email_binding")

EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")


def _email_keyboard():
    """Создаёт кнопку отмены привязки."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data="bind_email_back"))
    return builder.as_markup()


def _code_keyboard():
    """Создаёт действия на шаге подтверждения почты."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📨 Отправить код ещё раз", callback_data="bind_email_resend"))
    builder.row(InlineKeyboardButton(text="✏️ Изменить адрес", callback_data="bind_email_change"))
    builder.row(InlineKeyboardButton(text=BACK, callback_data="bind_email_back"))
    return builder.as_markup()


def _profile_keyboard():
    """Создаёт переход в кабинет."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 В кабинет", callback_data="profile"))
    return builder.as_markup()


async def _discard_pending(state: FSMContext, request_id: str | None = None) -> None:
    """Отменяет код и очищает состояние привязки."""
    await clear_email_binding_context(state, request_id)


async def _request_current(state: FSMContext, request_id: str, expected_state: str) -> bool:
    """Проверяет, что запрос привязки ещё активен."""
    if await state.get_state() != expected_state:
        return False
    return (await state.get_data()).get("email_binding_request_id") == request_id


async def _binding_allowed(target: Message | CallbackQuery, state: FSMContext, *, verification: bool = False) -> bool:
    """Проверяет настройки перед очередным шагом привязки."""
    if not is_email_binding_enabled():
        text = "Привязка почты отключена"
    elif verification and not is_email_binding_verification_enabled():
        text = "Подтверждение кодом выключено. Начните привязку заново."
    else:
        return True
    await _discard_pending(state)
    if isinstance(target, CallbackQuery):
        await target.answer(text, show_alert=True)
    else:
        await target.answer(text, reply_markup=_profile_keyboard())
    return False


async def _send_pending_code(target: Message | CallbackQuery, state: FSMContext) -> None:
    """Отправляет код и сохраняет только действующий шаг диалога."""
    data = await state.get_data()
    request_id = data.get("email_binding_request_id")
    identity_id = data.get("email_binding_identity_id")
    email = data.get("email_binding_email")
    if not request_id or not identity_id or not email:
        return
    if not await _request_current(state, request_id, EmailBindingState.waiting_for_code.state):
        return
    await state.update_data(email_binding_sending=True)
    error = ""
    try:
        await send_email_binding_code(str(identity_id), str(email), request_id)
    except ServiceError as exc:
        error = exc.message
    if not await _request_current(state, request_id, EmailBindingState.waiting_for_code.state):
        await discard_email_binding_code(str(identity_id), str(email), request_id)
        return
    if not await _binding_allowed(target, state, verification=True):
        return
    await state.update_data(email_binding_sending=False)
    message = target.message if isinstance(target, CallbackQuery) else target
    text = (
        f"❌ {error}"
        if error
        else (
            "📧 <b>Подтверждение почты</b>\n\n"
            f"Код отправлен на <code>{html_escape(email)}</code>.\n"
            "Введите 6 цифр из письма. Почта будет привязана только после подтверждения."
        )
    )
    if isinstance(target, CallbackQuery):
        await edit_or_send_message(target_message=message, text=text, reply_markup=_code_keyboard())
    else:
        await message.answer(text, reply_markup=_code_keyboard())


async def _pending_identity(target: Message | CallbackQuery, state: FSMContext, session):
    """Проверяет владельца сохранённого запроса привязки."""
    data = await state.get_data()
    request_id = data.get("email_binding_request_id")
    if not request_id or not await _request_current(state, request_id, EmailBindingState.waiting_for_code.state):
        return None
    identity = await get_identity_by_tg_id(session, target.from_user.id)
    if not await _request_current(state, request_id, EmailBindingState.waiting_for_code.state):
        return None
    if (
        identity is None
        or identity.id != data.get("email_binding_identity_id")
        or identity.email
        or not data.get("email_binding_email")
    ):
        await _discard_pending(state, request_id)
        text = "Шаг привязки устарел. Откройте привязку почты заново."
        if isinstance(target, CallbackQuery):
            await target.answer(text, show_alert=True)
        else:
            await target.answer(text, reply_markup=_profile_keyboard())
        return None
    return identity


@router.callback_query(F.data == "bind_email", flags={"popup": True})
async def prompt_email(callback: CallbackQuery, state: FSMContext, session) -> None:
    """Открывает ввод адреса почты."""
    if not await _binding_allowed(callback, state):
        return
    await _discard_pending(state)
    await state.clear()
    request_id = secrets.token_urlsafe(12)
    await state.set_state(EmailBindingState.waiting_for_email)
    await state.update_data(email_binding_request_id=request_id)
    identity = await get_or_create_identity_for_tg(session, callback.from_user.id)
    if not await _request_current(state, request_id, EmailBindingState.waiting_for_email.state):
        await callback.answer()
        return
    if identity.email:
        await _discard_pending(state, request_id)
        await callback.answer("Почта уже привязана", show_alert=True)
        return
    await edit_or_send_message(
        target_message=callback.message,
        text=(
            "📧 <b>Привязка почты</b>\n\n"
            "Укажите email — он понадобится для входа на сайт, "
            "если возникнут проблемы с Telegram.\n\n"
            "Отправьте адрес сообщением."
        ),
        reply_markup=_email_keyboard(),
    )
    await callback.answer()


@router.callback_query(StateFilter(EmailBindingState), F.data == "bind_email_back")
async def cancel_email_binding(callback: CallbackQuery, state: FSMContext, session, admin: bool = False) -> None:
    """Отменяет привязку и возвращает в кабинет."""
    await _discard_pending(state)
    await process_callback_view_profile(callback, state, admin, session)


@router.callback_query(EmailBindingState.waiting_for_code, F.data == "bind_email_change")
async def change_email(callback: CallbackQuery, state: FSMContext, session) -> None:
    """Отменяет прежний код и предлагает другой адрес."""
    await prompt_email(callback, state, session)


@router.callback_query(F.data == "bind_email_resend")
async def resend_email_code(callback: CallbackQuery, state: FSMContext, session) -> None:
    """Повторно отправляет код на выбранный адрес."""
    if not await _binding_allowed(callback, state, verification=True):
        return
    if await state.get_state() != EmailBindingState.waiting_for_code.state:
        await callback.answer("Откройте привязку почты заново", show_alert=True)
        return
    identity = await _pending_identity(callback, state, session)
    if identity is None:
        return
    data = await state.get_data()
    if data.get("email_binding_sending"):
        await callback.answer("Письмо отправляется. Подождите.")
        return
    await session.commit()
    await callback.answer()
    await _send_pending_code(callback, state)


@router.message(EmailBindingState.waiting_for_email)
async def receive_email(message: Message, state: FSMContext, session) -> None:
    """Проверяет адрес и запускает выбранный способ привязки."""
    if await state.get_state() != EmailBindingState.waiting_for_email.state:
        return
    request_id = (await state.get_data()).get("email_binding_request_id")
    if not request_id:
        request_id = secrets.token_urlsafe(12)
        await state.update_data(email_binding_request_id=request_id)
    if not await _binding_allowed(message, state):
        return
    email = normalize_email(message.text or "")
    if EMAIL_RE.fullmatch(email) is None or len(email) > 255:
        await message.answer("❌ Неверный формат email. Попробуйте ещё раз.", reply_markup=_email_keyboard())
        return
    if not await _request_current(state, request_id, EmailBindingState.waiting_for_email.state):
        return
    request_id = secrets.token_urlsafe(12)
    await state.update_data(email_binding_request_id=request_id)
    identity = await get_or_create_identity_for_tg(session, message.from_user.id)
    if not await _request_current(state, request_id, EmailBindingState.waiting_for_email.state):
        return
    if identity.email:
        await _discard_pending(state, request_id)
        await message.answer("ℹ️ Почта уже была привязана.", reply_markup=_profile_keyboard())
        return
    existing = await get_identity_by_email(session, email)
    if not await _request_current(state, request_id, EmailBindingState.waiting_for_email.state):
        return
    if existing and existing.id != identity.id:
        await message.answer("❌ Этот email уже занят. Используйте другой.", reply_markup=_email_keyboard())
        return
    verification_required = is_email_binding_verification_enabled() and email_binding_codes_configured()
    if not await _request_current(state, request_id, EmailBindingState.waiting_for_email.state):
        return
    if not await _binding_allowed(message, state):
        return
    if verification_required:
        identity_id = identity.id
        await session.commit()
        if not await _request_current(state, request_id, EmailBindingState.waiting_for_email.state):
            return
        await state.update_data(
            email_binding_identity_id=identity_id,
            email_binding_email=email,
            email_binding_sending=True,
        )
        await state.set_state(EmailBindingState.waiting_for_code)
        await _send_pending_code(message, state)
        return
    result = await attach_email(session, identity.id, email, allow_merge=False, only_if_unbound=True)
    if not await _request_current(state, request_id, EmailBindingState.waiting_for_email.state):
        await session.rollback()
        return
    if not await _binding_allowed(message, state):
        await session.rollback()
        return
    if result is None:
        await message.answer(
            "❌ Этот email уже занят или почта уже привязана. Начните заново.", reply_markup=_email_keyboard()
        )
        return
    await session.commit()
    await _discard_pending(state, request_id)
    await message.answer(f"✅ Почта <code>{html_escape(email)}</code> привязана.", reply_markup=_profile_keyboard())


@router.message(EmailBindingState.waiting_for_code)
async def receive_email_code(message: Message, state: FSMContext, session) -> None:
    """Подтверждает код и сохраняет проверенную почту."""
    if not await _binding_allowed(message, state, verification=True):
        return
    identity = await _pending_identity(message, state, session)
    if identity is None:
        return
    data = await state.get_data()
    email = data.get("email_binding_email")
    request_id = data.get("email_binding_request_id")
    if not email or not request_id:
        return
    if not await _request_current(state, request_id, EmailBindingState.waiting_for_code.state):
        return
    if data.get("email_binding_sending"):
        await message.answer("Письмо отправляется. Подождите.")
        return
    try:
        confirmed = await confirm_email_binding_code(identity.id, email, request_id, (message.text or "").strip())
    except ServiceError as exc:
        if not await _request_current(state, request_id, EmailBindingState.waiting_for_code.state):
            return
        await message.answer(f"❌ {exc.message}", reply_markup=_code_keyboard())
        return
    if not await _request_current(state, request_id, EmailBindingState.waiting_for_code.state):
        return
    if not await _binding_allowed(message, state, verification=True):
        return
    if not confirmed:
        await message.answer(
            "❌ Неверный код или срок действия истёк. Введите код из письма или запросите новый.",
            reply_markup=_code_keyboard(),
        )
        return
    result = await attach_email(session, identity.id, email, verified=True, allow_merge=False, only_if_unbound=True)
    if not await _request_current(state, request_id, EmailBindingState.waiting_for_code.state):
        await session.rollback()
        return
    if not await _binding_allowed(message, state, verification=True):
        await session.rollback()
        return
    if result is None:
        await _discard_pending(state, request_id)
        await message.answer(
            "❌ Этот email уже занят или почта уже привязана. Начните заново.", reply_markup=_profile_keyboard()
        )
        return
    await session.commit()
    await _discard_pending(state, request_id)
    await message.answer(
        f"✅ Почта <code>{html_escape(email)}</code> подтверждена и привязана.", reply_markup=_profile_keyboard()
    )


@router.callback_query(F.data.in_({"bind_email_back", "bind_email_change"}))
async def stale_email_binding_button(callback: CallbackQuery) -> None:
    """Сообщает об устаревшей кнопке без изменения состояния."""
    await callback.answer("Шаг привязки устарел", show_alert=True)
