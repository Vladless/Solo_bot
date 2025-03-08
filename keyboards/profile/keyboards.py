from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import INLINE_MODE


def get_profile_keyboard(admin: bool = False) -> InlineKeyboardBuilder:
    """
    Создает клавиатуру для профиля пользователя.

    Args:
        admin: Флаг, указывающий, является ли пользователь администратором.

    Returns:
        InlineKeyboardBuilder: Построитель клавиатуры с кнопками профиля.
    """
    builder = InlineKeyboardBuilder()

    # Основные кнопки профиля
    builder.button(text="💰 Баланс", callback_data="balance")
    builder.button(text="🔑 Мои подписки", callback_data="my_subs")
    builder.button(text="💸 Пополнить баланс", callback_data="payment")
    builder.button(text="🎁 Подарки", callback_data="gifts")
    builder.button(text="👥 Пригласить друга", callback_data="invite")
    builder.button(text="📚 Инструкции", callback_data="instructions")
    builder.button(text="🎫 Активировать купон", callback_data="activate_coupon")

    # Кнопка админ-панели для администраторов
    if admin:
        builder.button(text="⚙️ Админ-панель", callback_data="admin_panel")

    # Кнопка главного меню
    builder.button(text="🏠 Главное меню", callback_data="main_menu")

    # Настройка расположения кнопок (2 кнопки в ряд)
    builder.adjust(2)

    return builder


def get_balance_keyboard() -> InlineKeyboardBuilder:
    """
    Создает клавиатуру для раздела баланса.

    Returns:
        InlineKeyboardBuilder: Построитель клавиатуры с кнопками для баланса.
    """
    builder = InlineKeyboardBuilder()

    builder.button(text="📊 История баланса", callback_data="balance_history")
    builder.button(text="👤 Личный кабинет", callback_data="profile")
    builder.adjust(1)

    return builder


def get_invite_keyboard(chat_id: int, referral_link: str) -> InlineKeyboardBuilder:
    """
    Создает клавиатуру для приглашения друзей.

    Args:
        chat_id: ID чата пользователя.
        referral_link: Реферальная ссылка.

    Returns:
        InlineKeyboardBuilder: Построитель клавиатуры с кнопками для приглашений.
    """
    builder = InlineKeyboardBuilder()

    if INLINE_MODE:
        builder.button(text="👥 Пригласить друга", switch_inline_query="invite")
    else:
        invite_text = f"\nПриглашаю тебя пользоваться действительно быстрым VPN вместе:\n\n{referral_link}"
        builder.button(text="👥 Пригласить друга", switch_inline_query=invite_text)

    builder.button(text="👤 Личный кабинет", callback_data="profile")
    builder.adjust(1)

    return builder
