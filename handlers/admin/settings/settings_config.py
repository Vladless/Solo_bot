from typing import Final

BUTTON_TITLES: Final[dict[str, str]] = {
    "CHANNEL_BUTTON_ENABLE": "Канал",
    "DONATIONS_BUTTON_ENABLE": "Донаты",
    "BALANCE_BUTTON_ENABLE": "Баланс",
    "REFERRAL_QR_BUTTON_ENABLE": "QR реф.меню",
    "DELETE_KEY_BUTTON_ENABLE": "Удалить подп-ку",
    "INSTRUCTIONS_BUTTON_ENABLE": "Инструкции",
    "TOGGLE_CLIENT_BUTTON_ENABLE": "Заморозка подписки",
    "GIFT_BUTTON_ENABLE": "Подарки",
    "REFERRAL_BUTTON_ENABLE": "Реф.система",
    "TOP_REFERRAL_BUTTON_ENABLE": "Топ-5 рефералов",
    "QRCODE_BUTTON_ENABLE": "QR подписки",
    "HWID_RESET_BUTTON_ENABLE": "Сброс HWID",
}

NOTIFICATION_TITLES: Final[dict[str, str]] = {
    "RENEW_ENABLED": "Авто-продление",
    "EXPIRY_24H_ENABLED": "За 24 часа",
    "EXPIRY_10H_ENABLED": "За 10 часов",
    "DELETE_KEY_ENABLED": "Удалять просроченные",
    "RENEW_EXPIRED_ENABLED": "Продлевать просроченные",
    "HOT_LEADS_ENABLED": "Горячие лиды",
}

NOTIFICATION_TIME_FIELDS: Final[dict[str, str]] = {
    "BASE_NOTIFICATION_MINUTE": "Проверка (сек)",
    "INACTIVE_USER_ENABLED": "Неактивные (ч)",
    "EXPIRY_24H_BEFORE_HOURS": "До 24ч (ч)",
    "EXPIRY_10H_BEFORE_HOURS": "До 10ч (ч)",
    "DELETE_KEY_DELAY_HOURS": "Удаление (ч)",
    "EXTRA_DAYS_AFTER_EXPIRY": "Дни к пробнику",
    "INACTIVE_TRAFFIC_ENABLED": "Трафик неакт. (ч)",
    "HOT_LEADS_INTERVAL_HOURS": "Гор.лиды (ч)",
    "DISCOUNT_ACTIVE_HOURS": "Скидка (ч)",
}

PAYMENT_PROVIDER_TITLES: Final[dict[str, str]] = {
    "YOOKASSA": "YooKassa",
    "YOOMONEY": "YooMoney",
    "ROBOKASSA": "Robokassa",
    "KASSAI_CARDS": "KassaAI карты",
    "KASSAI_SBP": "KassaAI СБП",
    "TRIBUTE": "Tribute",
    "HELEKET": "Heleket",
    "CRYPTOBOT": "CryptoBot",
    "FREEKASSA": "FreeKassa",
    "STARS": "Telegram Stars",
}

MODES_TITLES: Final[dict[str, str]] = {
    "CAPTCHA_ENABLED": "Капча",
    "CHANNEL_CHECK_ENABLED": "Обязат. канал",
    "SHOW_START_MENU_ONLY_ONCE": "Старт один раз",
    "INLINE_MODE_ENABLED": "Инлайн-режим",
    "RANDOM_SUBSCRIPTIONS_ENABLED": "Случайные страны",
    "COUNTRY_SELECTION_ENABLED": "Режим стран",
    "REMNAWAVE_WEBAPP_ENABLED": "Remna WebApp",
    "HAPP_CRYPTOLINK_ENABLED": "Happ-ссылки",
    "LEGACY_LINKS_ENABLED": "Старые ссылки",
    "DIRECT_START_DISABLED": "Тихий режим",
    "TRIAL_TIME_DISABLED": "Отключить триал",
}

MONEY_FIELDS: Final[dict[str, str]] = {
    "FX_MARKUP": "Наценка FX (%)",
    "RUB_TO_USD": "Курс USD/RUB",
    "CASHBACK": "Кэшбэк (%)",
}
