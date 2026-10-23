from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import TelegramId, UserId, get_user_by_tg_id
from handlers.utils import edit_or_send_message
from hooks.hooks import register_hook
from settings import buttons, texts


router = Router(name="core_autopay_management")


@dataclass(frozen=True)
class AutopayManagementProvider:
    provider_id: str
    title: str
    enabled: Callable[[AsyncSession], Awaitable[bool]]
    has_data: Callable[[AsyncSession, UserId], Awaitable[bool]]
    render: Callable[[CallbackQuery, AsyncSession, UserId], Awaitable[None]]
    button_location: Callable[[], str]
    order: int = 0


_providers: dict[str, AutopayManagementProvider] = {}


def register_autopay_management(provider: AutopayManagementProvider) -> None:
    """Регистрирует управление автопродлением платёжной системы."""
    _providers[provider.provider_id] = provider


def _visible_at(provider: AutopayManagementProvider, location: str | None) -> bool:
    """Учитывает расположение кнопки выбранной кассы."""
    return location is None or provider.button_location() in {location, "both"}


async def management_providers(
    session: AsyncSession, user_id: UserId, location: str | None = None
) -> list[AutopayManagementProvider]:
    """Выбирает кассы клиента или доступные для первой настройки."""
    owned = []
    enabled = []
    for provider in sorted(_providers.values(), key=lambda item: (item.order, item.provider_id)):
        if not _visible_at(provider, location):
            continue
        if await provider.has_data(session, user_id):
            owned.append(provider)
        elif await provider.enabled(session):
            enabled.append(provider)
    return owned or enabled


def _location(callback: CallbackQuery) -> str | None:
    """Восстанавливает раздел, из которого открыто управление."""
    data = str(callback.data or "")
    if data == "autopay_manage":
        return None
    if data.startswith(("autopay_manage|", "autopay_provider|")):
        value = data.rsplit("|", 1)[-1]
        if value in {"profile", "pay_menu"}:
            return value
    markup = getattr(callback.message, "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", ()) or ():
        for button in row:
            value = str(button.callback_data or "")
            if value in {"autopay_manage|profile", "autopay_manage|pay_menu"}:
                return value.split("|", 1)[1]
            if value == "pay" and button.text == buttons.BACK:
                return "pay_menu"
            if value == "profile" and button.text == buttons.MAIN_MENU:
                return "profile"
    return None


def _menu_callback(location: str | None) -> str:
    """Возвращает адрес общего меню в исходном разделе."""
    return f"autopay_manage|{location}" if location else "autopay_manage"


async def management_back_button(
    callback: CallbackQuery, session: AsyncSession, user_id: UserId
) -> InlineKeyboardButton:
    """Возвращает к выбору кассы или в исходное меню."""
    location = _location(callback)
    if len(await management_providers(session, user_id, location)) > 1:
        return InlineKeyboardButton(text=buttons.BACK, callback_data=_menu_callback(location))
    if location == "pay_menu":
        return InlineKeyboardButton(text=buttons.BACK, callback_data="pay")
    return InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile")


async def _actor(callback: CallbackQuery, session: AsyncSession) -> UserId | None:
    """Определяет владельца исключительно по отправителю Telegram."""
    user = await get_user_by_tg_id(session, TelegramId(callback.from_user.id))
    if user is None:
        keyboard = InlineKeyboardBuilder()
        keyboard.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))
        await edit_or_send_message(
            callback.message, texts.AUTOPAY_MANAGEMENT_USER_NOT_FOUND, reply_markup=keyboard.as_markup()
        )
        await callback.answer()
        return None
    return UserId(user.id)


async def _render_choices(
    callback: CallbackQuery,
    providers: list[AutopayManagementProvider],
    location: str | None,
) -> None:
    """Показывает выбор кассы без отдельных кнопок в кабинете."""
    keyboard = InlineKeyboardBuilder()
    for provider in providers:
        suffix = f"|{location}" if location else ""
        keyboard.row(
            InlineKeyboardButton(
                text=provider.title, callback_data=f"autopay_provider|{provider.provider_id}{suffix}"
            )
        )
    keyboard.row(
        InlineKeyboardButton(text=buttons.BACK, callback_data="pay" if location == "pay_menu" else "profile")
    )
    await edit_or_send_message(
        callback.message,
        texts.AUTOPAY_MANAGEMENT_CHOOSE_PROVIDER if providers else texts.AUTOPAY_MANAGEMENT_UNAVAILABLE,
        reply_markup=keyboard.as_markup(),
    )


@router.callback_query(F.data == "autopay_manage")
@router.callback_query(F.data.in_({"autopay_manage|profile", "autopay_manage|pay_menu"}))
async def manage(callback: CallbackQuery, session: AsyncSession) -> None:
    """Открывает единственную кассу либо предлагает выбор."""
    user_id = await _actor(callback, session)
    if user_id is None:
        return
    location = _location(callback)
    providers = await management_providers(session, user_id, location)
    if len(providers) == 1:
        await providers[0].render(callback, session, user_id)
    else:
        await _render_choices(callback, providers, location)
    await callback.answer()


@router.callback_query(F.data.startswith("autopay_provider|"))
async def choose_provider(callback: CallbackQuery, session: AsyncSession) -> None:
    """Проверяет доступность кассы текущего клиента перед открытием."""
    user_id = await _actor(callback, session)
    if user_id is None:
        return
    location = _location(callback)
    provider_id = callback.data.split("|", 2)[1]
    providers = await management_providers(session, user_id, location)
    provider = next((item for item in providers if item.provider_id == provider_id), None)
    if provider is None:
        await _render_choices(callback, providers, location)
    else:
        await provider.render(callback, session, user_id)
    await callback.answer()


async def _management_button(chat_id: int, session: AsyncSession, location: str):
    """Добавляет одну кнопку для доступных клиенту касс."""
    user = await get_user_by_tg_id(session, TelegramId(chat_id))
    if user is None or not await management_providers(session, UserId(user.id), location):
        return None
    return InlineKeyboardButton(text=buttons.AUTOPAY_MANAGE, callback_data=_menu_callback(location))


async def profile_menu_hook(chat_id: int, session: AsyncSession, **kwargs: Any):
    """Добавляет единую кнопку автопродления в кабинет."""
    button = await _management_button(chat_id, session, "profile")
    return [{"after": "gifts", "button": button}] if button is not None else []


async def pay_menu_hook(chat_id: int, session: AsyncSession, **kwargs: Any):
    """Добавляет единую кнопку автопродления в меню оплаты."""
    button = await _management_button(chat_id, session, "pay_menu")
    return [{"button": button}] if button is not None else []


register_hook("profile_menu", profile_menu_hook)
register_hook("pay_menu_buttons", pay_menu_hook)
