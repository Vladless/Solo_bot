from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import DOWNLOAD_ANDROID, DOWNLOAD_IOS, CONNECT_IOS, CONNECT_ANDROID, PUBLIC_LINK, RENEWAL_PLANS, \
    RENEWAL_PRICES, SUPPORT_CHAT_URL
from handlers.texts import DISCOUNTS


def build_view_keys_kb(records: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for record in records:
        key_name = record["email"]
        builder.button(
            text=f"🔑 {key_name}",
            callback_data=f"view_key|{key_name}",
        )

    builder.button(
        text="👤 Личный кабинет",
        callback_data="profile",
    )

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)


def build_view_no_keys_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="➕ Создать подписку",
        callback_data="create_key",
    )
    builder.button(
        text="👤 Личный кабинет",
        callback_data="profile",
    )

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)


def build_view_key_kb(key: str, key_name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="🍏 Скачать для iOS",
            url=DOWNLOAD_IOS,
        ),
        InlineKeyboardButton(
            text="🤖 Скачать для Android",
            url=DOWNLOAD_ANDROID,
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🍏 Подключить на iOS",
            url=f"{CONNECT_IOS}{key}",
        ),
        InlineKeyboardButton(
            text="🤖 Подключить на Android",
            url=f"{CONNECT_ANDROID}{key}",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="💻 Windows/Linux",
            callback_data=f"connect_pc|{key_name}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⏳ Продлить",
            callback_data=f"renew_key|{key_name}",
        ),
        InlineKeyboardButton(
            text="❌ Удалить",
            callback_data=f"delete_key|{key_name}",
        ),
    )

    if not key.startswith(PUBLIC_LINK):
        builder.row(
            InlineKeyboardButton(
                text="🔄 Обновить подписку",
                callback_data=f"update_subscription|{key_name}",
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="👤 Личный кабинет",
            callback_data="profile",
        )
    )

    return builder.as_markup()


def build_key_creation_kb(public_link: str, email: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="💬 Поддержка",
            url=SUPPORT_CHAT_URL,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🍏 Скачать для iOS",
            url=DOWNLOAD_IOS,
        ),
        InlineKeyboardButton(
            text="🤖 Скачать для Android",
            url=DOWNLOAD_ANDROID,
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🍏 Подключить на iOS",
            url=f"{CONNECT_IOS}{public_link}",
        ),
        InlineKeyboardButton(
            text="🤖 Подключить на Android",
            url=f"{CONNECT_ANDROID}{public_link}",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="💻 Windows/Linux",
            callback_data=f"connect_pc|{email}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="👤 Личный кабинет",
            callback_data="profile",
        )
    )

    return builder.as_markup()


def build_key_delete_kb(client_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="✅ Да, удалить",
        callback_data=f"confirm_delete|{client_id}",
    )
    builder.button(
        text="❌ Нет, отменить",
        callback_data="view_keys",
    )

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)


def build_renewal_plans_kb(client_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text=f'📅 1 месяц ({RENEWAL_PLANS["1"]["price"]} руб.)',
            callback_data=f"renew_plan|1|{client_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f'📅 3 месяца ({RENEWAL_PLANS["3"]["price"]} руб.) {DISCOUNTS["3"]}% скидка',
            callback_data=f"renew_plan|3|{client_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f'📅 6 месяцев ({RENEWAL_PLANS["6"]["price"]} руб.) {DISCOUNTS["6"]}% скидка',
            callback_data=f"renew_plan|6|{client_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f'📅 12 месяцев ({RENEWAL_PLANS["12"]["price"]} руб.) ({DISCOUNTS["12"]}% 🔥)',
            callback_data=f"renew_plan|12|{client_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text='🔙 Назад',
            callback_data="view_keys",
        )
    )

    return builder.as_markup()


def build_new_key_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="✅ Да, подключить новое устройство",
        callback_data="confirm_create_new_key",
    )
    builder.button(
        text="👤 Личный кабинет",
        callback_data="profile",
    )

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)


def build_plan_selected_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for index, (plan_id, price) in enumerate(RENEWAL_PRICES.items()):
        discount_text = ""

        if plan_id in DISCOUNTS:
            discount_percentage = DISCOUNTS[plan_id]
            discount_text = f" ({discount_percentage}% скидка)"

            if index == len(RENEWAL_PRICES) - 1:
                discount_text = f" ({discount_percentage}% 🔥)"

        builder.button(
            text=f"📅 {plan_id} мес. - {price}₽{discount_text}",
            callback_data=f"select_plan_{plan_id}",
        )

    builder.button(
        text="👤 Личный кабинет",
        callback_data="profile",
    )

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)


def build_top_up_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="💳 Пополнить баланс",
        callback_data="pay",
    )
    builder.button(
        text="👤 Личный кабинет",
        callback_data="profile",
    )

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)
