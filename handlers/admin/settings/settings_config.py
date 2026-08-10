from typing import Final


BUTTON_TITLES: Final[dict[str, str]] = {
    "CHANNEL_BUTTON_ENABLE": "Канал",
    "DONATIONS_BUTTON_ENABLE": "Донаты",
    "BALANCE_BUTTON_ENABLE": "Баланс",
    "REFERRAL_QR_BUTTON_ENABLE": "QR реф.меню",
    "DELETE_KEY_BUTTON_ENABLE": "Удалить подп-ку",
    "INSTRUCTIONS_BUTTON_ENABLE": "Инструкции",
    "GIFT_BUTTON_ENABLE": "Подарки",
    "REFERRAL_BUTTON_ENABLE": "Реф.система",
    "TOP_REFERRAL_BUTTON_ENABLE": "Топ-5 рефералов",
    "QRCODE_BUTTON_ENABLE": "QR подписки",
    "HWID_RESET_BUTTON_ENABLE": "Сброс HWID",
    "ANDROID_TV_BUTTON_ENABLE": "Android TV",
    "COUPON_BUTTON_ENABLE": "Активировать купон",
}

NOTIFICATION_TITLES: Final[dict[str, str]] = {
    "RENEW_ENABLED": "Авто-продление",
    "EXPIRY_24H_ENABLED": "За 24 часа",
    "EXPIRY_10H_ENABLED": "За 10 часов",
    "DELETE_KEY_ENABLED": "Удалять просроченные",
    "RENEW_EXPIRED_ENABLED": "Продлевать просроченные",
    "HOT_LEADS_ENABLED": "Горячие лиды",
    "COLD_LEADS_ENABLED": "Холодные лиды",
    "RETURNING_ENABLED": "Возврат давно ушедших",
}

NOTIFICATION_TIME_FIELDS: Final[dict[str, str]] = {
    "BASE_NOTIFICATION_MINUTE": "Проверка (сек)",
    "INACTIVE_USER_ENABLED": "Неактивные (ч)",
    "EXPIRY_24H_BEFORE_HOURS": "До 24ч (ч)",
    "EXPIRY_10H_BEFORE_HOURS": "До 10ч (ч)",
    "DELETE_KEY_DELAY_MINUTES": "Удаление (мин)",
    "EXTRA_DAYS_AFTER_EXPIRY": "Дни к пробнику",
    "INACTIVE_TRAFFIC_ENABLED": "Трафик неакт. (ч)",
    "HOT_LEADS_INTERVAL_HOURS": "Гор.лиды (ч)",
    "COLD_LEADS_INTERVAL_HOURS": "Хол.лиды (ч)",
    "RETURNING_MIN_DAYS": "Давно ушли от (дн)",
    "RETURNING_MAX_DAYS": "Давно ушли до (дн)",
    "DISCOUNT_ACTIVE_HOURS": "Скидка (ч)",
    "RENEW_BUTTON_BEFORE_DAYS": "Кнопка продл. за (дн)",
    "HWID_DELETE_PENALTY": "HWID штраф",
    "HWID_DAILY_RECOVERY": "HWID восст/сут",
    "HWID_MIN_TRUST_TO_DELETE": "HWID порог",
}

PAYMENT_PROVIDER_TITLES: Final[dict[str, str]] = {
    "YOOKASSA": "YooKassa",
    "YOOMONEY": "YooMoney",
    "ROBOKASSA": "Robokassa",
    "KASSAI_CARDS": "KassaAI карты",
    "KASSAI_SBP": "KassaAI СБП",
    "WATA_RU": "WATA карты РФ / СБП",
    "WATA_INT": "WATA международные",
    "PARITYPAY_SBP": "ParityPay СБП",
    "PLATEGA_SBP": "Platega СБП",
    "PLATEGA_CARDS": "Platega карты РФ",
    "PLATEGA_INT": "Platega международные",
    "PLATEGA_CRYPTO": "Platega крипто",
    "OVERPAY_CARDS": "Overpay карты",
    "OVERPAY_SBP": "Overpay СБП",
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
    "REMNAWAVE_WEBAPP_OPEN_IN_BROWSER": "WebApp в браузере",
    "HAPP_CRYPTOLINK_ENABLED": "Happ-ссылки",
    "LEGACY_LINKS_ENABLED": "Старые ссылки",
    "DIRECT_START_DISABLED": "Тихий режим",
    "TRIAL_TIME_DISABLED": "Отключить триал",
    "WEB_TRIAL_DISABLED": "Отключить триал на сайте",
    "SUPPORT_TRIAGE_ENABLED": "Опросник поддержки",
    "SUPPORT_TICKETS_ENABLED": "Система тикетов",
    "SUPPORT_SHADOW_MODE": "Тикеты без кнопок",
    "PROTECT_CONTENT_ENABLED": "Защита контента",
    "TARIFF_OPTIONS_PAGINATION": "Слайдер опций",
    "HWID_DELETE_COOLDOWN_ENABLED": "Кулдаун HWID",
    "SINGLE_SUBSCRIPTION_MODE": "Одна подписка",
    "RENEWAL_CREDIT_AS_DAYS": "Перерасчет дни",
    "RENEWAL_SWITCH_KEEP_PERIOD": "Смена: сохранять срок",
    "GIFT_EXTEND_ENABLED": "Подарок продлить",
    "WEBAPP_ONLY_MODE": "Только веб-приложение",
}

MONEY_FIELDS: Final[dict[str, str]] = {
    "FX_MARKUP": "Наценка FX (%)",
    "RUB_TO_USD": "Курс USD/RUB",
    "CASHBACK": "Кэшбэк (%)",
}

TARIFFS_TITLES: Final[dict[str, str]] = {
    "ALLOW_DOWNGRADE": "Разрешить даунгрейд",
    "KEY_ADDONS_PACK_MODE": "Режим докупки опций",
    "KEY_ADDONS_PRICE_BASE_MODE": "База цены докупки",
    "KEY_ADDONS_RECALC_PRICE": "Перерасчёт цены докупки",
    "KEY_ADDONS_CARRY_ON_RENEWAL": "Переносить опции",
}

WEB_TITLES: Final[dict[str, str]] = {
    "WEB_ENABLED": "Сайт включён",
    "SITE_URL": "URL сайта",
    "SITE_MODE": "Режим сайта",
    "WEB_OPEN_IN_BROWSER": "Открывать в браузере",
    "WEB_NODE_STATUS_INTERVAL_MIN": "Статус серверов (мин)",
    "EMAIL_BINDING_ENABLED": "Привязка почты",
    "WEB_NOTIFY_PAYMENT_TITLE": "Уведомление об оплате — заголовок",
    "WEB_NOTIFY_PAYMENT_MESSAGE": "Уведомление об оплате — текст",
    "WEB_NOTIFY_KEY_CREATED_TITLE": "Подписка создана — заголовок",
    "WEB_NOTIFY_KEY_CREATED_MESSAGE": "Подписка создана — текст",
    "WEB_NOTIFY_KEY_EXPIRY_TITLE": "Подписка истекает — заголовок",
    "WEB_NOTIFY_KEY_EXPIRY_MESSAGE": "Подписка истекает — текст",
    "WEB_NOTIFY_GIFT_TITLE": "Подарок получен — заголовок",
    "WEB_NOTIFY_GIFT_MESSAGE": "Подарок получен — текст",
    "EMAIL_LOGIN_SUBJECT": "Письмо входа — тема",
    "EMAIL_LOGIN_BODY": "Письмо входа — текст",
    "EMAIL_RESET_SUBJECT": "Сброс пароля — тема",
    "EMAIL_RESET_BODY": "Сброс пароля — текст",
    "EMAIL_LINK_SUBJECT": "Привязка email — тема",
    "EMAIL_LINK_BODY": "Привязка email — текст",
}

REMNAWAVE_TITLES: Final[dict[str, str]] = {
    "NODE_HEALTH_ENABLED": "Мониторинг нод",
    "NODE_HEALTH_INTERVAL_MIN": "Интервал проверки (мин)",
    "HOST_AUTO_DISABLE_ON_NODE_DOWN": "Авто-отключение хостов",
    "HOST_ROTATION_ENABLED": "Ротация хостов",
    "HOST_ROTATION_INTERVAL_MIN": "Интервал ротации (мин)",
}

MANAGEMENT_TITLES: Final[dict[str, str]] = {
    "MAINTENANCE_ENABLED": "Режим обслуживания",
}
