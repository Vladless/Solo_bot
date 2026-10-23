import re

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.autopay_locations import AUTOPAY_MANAGEMENT_LOCATIONS
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
    if location is None:
        return True
    configured = AUTOPAY_MANAGEMENT_LOCATIONS.get(provider.button_location(), frozenset())
    requested = AUTOPAY_MANAGEMENT_LOCATIONS.get(location, frozenset({location}))
    return bool(configured & requested)


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


def _callback_context(data: str) -> tuple[str | None, str | None]:
    """Читает раздел и ссылку возврата из общей кнопки управления."""
    parts = data.split("|")
    index = {"autopay_manage": 1, "autopay_provider": 2}.get(parts[0])
    if index is None or len(parts) <= index:
        return None, None
    location = parts[index]
    if location not in {"profile", "pay_menu", "subscription", "profile_subscription"}:
        return None, None
    key_ref = "|".join(parts[index + 1 :]) if location == "subscription" and len(parts) > index + 1 else None
    return location, key_ref


def _location(callback: CallbackQuery) -> str | None:
    """Восстанавливает раздел, из которого открыто управление."""
    data = str(callback.data or "")
    if data == "autopay_manage":
        return None
    location, _ = _callback_context(data)
    if location is not None:
        return location
    markup = getattr(callback.message, "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", ()) or ():
        for button in row:
            value = str(button.callback_data or "")
            location, _ = _callback_context(value)
            if location is not None:
                return location
            if value == "view_keys" or value.startswith("view_key|"):
                return "subscription"
            if value == "pay" and button.text == buttons.BACK:
                return "pay_menu"
            if value == "profile" and button.text == buttons.BACK:
                return "profile_subscription"
            if value == "profile" and button.text == buttons.MAIN_MENU:
                return "profile"
    return None


def _key_ref(callback: CallbackQuery) -> str | None:
    """Сохраняет возврат к исходной подписке между действиями кассы."""
    location, key_ref = _callback_context(str(callback.data or ""))
    if location == "subscription":
        return key_ref
    markup = getattr(callback.message, "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", ()) or ():
        for button in row:
            value = str(button.callback_data or "")
            location, key_ref = _callback_context(value)
            if location == "subscription":
                return key_ref
            if value.startswith("view_key|"):
                return value.split("|", 1)[1]
    return None


def _menu_callback(location: str | None, key_ref: str | None = None) -> str:
    """Возвращает адрес общего меню в исходном разделе."""
    if location == "subscription" and key_ref:
        return f"autopay_manage|subscription|{key_ref}"
    return f"autopay_manage|{location}" if location else "autopay_manage"


def management_return_callback(callback: CallbackQuery, fallback: str) -> str:
    """Передаёт раздел и подписку экрану подтверждения действия."""
    location = _location(callback)
    return _menu_callback(location, _key_ref(callback)) if location else fallback


def management_checkout_callback(callback: CallbackQuery) -> str:
    """Сохраняет возврат из привязки способа оплаты к подписке."""
    if str(callback.data or "") == "pay" or str(callback.data or "").startswith("choose_payment_"):
        return "pay"
    if _location(callback) in {"subscription", "profile_subscription"}:
        return management_return_callback(callback, "pay")
    return "pay"


async def remember_management_checkout(
    callback: CallbackQuery,
    state: FSMContext | None,
    user_id: UserId,
    provider_id: str,
    *,
    reset: bool = False,
) -> str:
    """Сохраняет возврат из привязки отдельно от данных оплаты."""
    target = management_checkout_callback(callback)
    if state is not None:
        if reset:
            await state.clear()
        await state.update_data(
            autopay_management_navigation={
                "user_id": int(user_id),
                "provider_id": provider_id,
                "return_callback": target,
            }
        )
    return target


async def management_checkout_return(
    state: FSMContext | None, user_id: UserId, provider_id: str, fallback: str = "pay"
) -> str:
    """Читает возврат только для текущего владельца и кассы."""
    data = await state.get_data() if state is not None else None
    navigation = data.get("autopay_management_navigation") if isinstance(data, dict) else None
    if not isinstance(navigation, dict) or navigation.get("user_id") != int(user_id):
        return fallback
    if navigation.get("provider_id") != provider_id:
        return fallback
    target = str(navigation.get("return_callback") or "")
    location, key_ref = _callback_context(target)
    if target.startswith("autopay_manage|") and location in {"subscription", "profile_subscription"}:
        if key_ref is None or re.fullmatch(r"[0-9a-f]{12}", key_ref):
            return target
    return fallback


def _origin_callback(callback: CallbackQuery, location: str | None) -> str:
    """Возвращает пользователя к подписке, списку или исходному меню."""
    if location == "subscription":
        key_ref = _key_ref(callback)
        return f"view_key|{key_ref}" if key_ref else "view_keys"
    return "pay" if location == "pay_menu" else "profile"


async def _validate_subscription_context(callback: CallbackQuery, session: AsyncSession, user_id: UserId) -> bool:
    """Проверяет принадлежность подписки из адреса возврата."""
    if _location(callback) != "subscription":
        return True
    key_ref = _key_ref(callback)
    if key_ref is None:
        return True
    if re.fullmatch(r"[0-9a-f]{12}", key_ref):
        from handlers.keys.utils import resolve_key

        if await resolve_key(session, user_id, key_ref) is not None:
            return True
    await callback.answer(texts.AUTOPAY_MANAGEMENT_UNAVAILABLE, show_alert=True)
    return False


def merge_management_buttons(
    markup: InlineKeyboardMarkup, location: str = "profile_subscription"
) -> InlineKeyboardMarkup:
    """Оставляет одну кнопку в объединённом профиле с подпиской."""
    rows = []
    added = False
    for row in markup.inline_keyboard:
        result = []
        for button in row:
            if str(button.callback_data or "").split("|", 1)[0] == "autopay_manage":
                if added:
                    continue
                button = InlineKeyboardButton(text=buttons.AUTOPAY_MANAGE, callback_data=_menu_callback(location))
                added = True
            result.append(button)
        if result:
            rows.append(result)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def management_back_button(
    callback: CallbackQuery, session: AsyncSession, user_id: UserId
) -> InlineKeyboardButton:
    """Возвращает к выбору кассы или в исходное меню."""
    location = _location(callback)
    if len(await management_providers(session, user_id, location)) > 1:
        return InlineKeyboardButton(text=buttons.BACK, callback_data=_menu_callback(location, _key_ref(callback)))
    if location in {"pay_menu", "subscription", "profile_subscription"}:
        return InlineKeyboardButton(text=buttons.BACK, callback_data=_origin_callback(callback, location))
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
        suffix = _menu_callback(location, _key_ref(callback)).removeprefix("autopay_manage")
        keyboard.row(
            InlineKeyboardButton(text=provider.title, callback_data=f"autopay_provider|{provider.provider_id}{suffix}")
        )
    keyboard.row(InlineKeyboardButton(text=buttons.BACK, callback_data=_origin_callback(callback, location)))
    await edit_or_send_message(
        callback.message,
        texts.AUTOPAY_MANAGEMENT_CHOOSE_PROVIDER if providers else texts.AUTOPAY_MANAGEMENT_UNAVAILABLE,
        reply_markup=keyboard.as_markup(),
    )


@router.callback_query(F.data == "autopay_manage")
@router.callback_query(
    F.data.in_({"autopay_manage|profile", "autopay_manage|pay_menu", "autopay_manage|profile_subscription"})
)
@router.callback_query(F.data == "autopay_manage|subscription")
@router.callback_query(F.data.startswith("autopay_manage|subscription|"))
async def manage(callback: CallbackQuery, session: AsyncSession, state: FSMContext | None = None) -> None:
    """Открывает единственную кассу либо предлагает выбор."""
    user_id = await _actor(callback, session)
    if user_id is None or not await _validate_subscription_context(callback, session, user_id):
        return
    if state is not None:
        await state.clear()
    location = _location(callback)
    providers = await management_providers(session, user_id, location)
    if len(providers) == 1:
        await providers[0].render(callback, session, user_id)
    else:
        await _render_choices(callback, providers, location)
    await callback.answer()


@router.callback_query(F.data.startswith("autopay_provider|"))
async def choose_provider(callback: CallbackQuery, session: AsyncSession, state: FSMContext | None = None) -> None:
    """Проверяет доступность кассы текущего клиента перед открытием."""
    user_id = await _actor(callback, session)
    if user_id is None or not await _validate_subscription_context(callback, session, user_id):
        return
    if state is not None:
        await state.clear()
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


async def subscription_management_button(
    session: AsyncSession, user_id: UserId, client_id: str | None = None, email: str | None = None
) -> InlineKeyboardButton | None:
    """Добавляет общее управление автопродлением в меню подписки."""
    if not await management_providers(session, user_id, "subscription"):
        return None
    from handlers.keys.utils import build_key_ref

    key_ref = build_key_ref(client_id, email) if client_id else None
    return InlineKeyboardButton(text=buttons.AUTOPAY_MANAGE, callback_data=_menu_callback("subscription", key_ref))


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
