from __future__ import annotations

import re


SITE_LOG_TAG = "[Site]"

_VERBS = {
    "GET": "смотрит",
    "HEAD": "проверяет",
    "POST": "отправляет",
    "PUT": "сохраняет",
    "PATCH": "меняет",
    "DELETE": "удаляет",
    "OPTIONS": "спрашивает права на",
}

_STATUSES = {
    200: "ок",
    201: "создано",
    202: "принято",
    204: "ок",
    301: "переадресация",
    302: "переадресация",
    304: "без изменений",
    400: "неверный запрос",
    401: "вход не подтверждён",
    402: "не хватает средств",
    403: "нет доступа",
    404: "не найдено",
    409: "конфликт",
    410: "больше недоступно",
    413: "слишком большой файл",
    422: "не прошло проверку",
    429: "слишком часто",
    500: "ошибка сервера",
    502: "панель недоступна",
    503: "сервис недоступен",
    504: "панель не ответила",
}

_Rule = tuple[frozenset[str] | None, re.Pattern[str], str]

_RULES: list[_Rule] = []


def _rule(methods: str | None, pattern: str, phrase: str) -> None:
    """Правило: методы через пробел (или None — любой), путь, готовая фраза с {группами}."""
    _RULES.append((frozenset(methods.split()) if methods else None, re.compile(pattern), phrase))


def _crud(pattern: str, noun: str) -> None:
    """Стандартный набор глаголов для ресурса: смотрит, сохраняет, создаёт, удаляет."""
    _rule("GET HEAD", pattern, f"смотрит {noun}")
    _rule("PUT PATCH", pattern, f"сохраняет {noun}")
    _rule("POST", pattern, f"создаёт {noun}")
    _rule("DELETE", pattern, f"удаляет {noun}")


_rule("GET", r"^/api/web/pages/landing$", "открывает витрину")
_rule("GET", r"^/api/web/pages/dashboard$", "открывает кабинет")
_rule("GET", r"^/api/web/pages/login$", "открывает страницу входа")
_rule("GET", r"^/api/web/pages/checkout$", "открывает оплату")
_rule("GET", r"^/api/web/pages/(?P<slug>[^/]+)/theme$", "смотрит тему страницы «{slug}»")
_rule("PUT PATCH", r"^/api/web/pages/(?P<slug>[^/]+)/theme$", "сохраняет тему страницы «{slug}»")
_rule("GET", r"^/api/web/pages/(?P<slug>[^/]+)/variants$", "смотрит варианты страницы «{slug}»")
_rule("GET", r"^/api/web/pages/(?P<slug>[^/]+)$", "открывает страницу «{slug}»")
_rule("PUT PATCH", r"^/api/web/pages/(?P<slug>[^/]+)$", "сохраняет страницу «{slug}»")
_rule("DELETE", r"^/api/web/pages/(?P<slug>[^/]+)$", "удаляет страницу «{slug}»")
_rule("GET", r"^/api/web/pages$", "смотрит список страниц")
_rule("GET", r"^/api/web/design/snapshot$", "снимает слепок оформления")
_rule(None, r"^/api/web/uploads/", "берёт загруженный файл")

_rule("GET", r"^/api/web/packs/files/(?P<pack>[^/]+)/", "грузит файлы набора «{pack}»")
_rule("GET", r"^/api/web/packs/installed$", "смотрит установленные наборы")
_rule("GET", r"^/api/web/packs/available$", "смотрит доступные наборы")
_rule("GET", r"^/api/web/packs/entitlements$", "проверяет доступ к наборам")
_rule("GET", r"^/api/web/packs/(?P<pack>[^/]+)/design$", "смотрит оформление набора «{pack}»")
_rule("POST", r"^/api/web/packs/(?P<pack>[^/]+)/install$", "ставит набор «{pack}»")
_rule("GET", r"^/api/web/packs$", "смотрит каталог наборов")

_rule("GET", r"^/api/auth/me/payments", "смотрит свои платежи")
_rule("GET", r"^/api/auth/me$", "проверяет вход")
_rule("GET", r"^/api/auth/summary$", "смотрит сводку аккаунта")
_rule("GET", r"^/api/auth/sessions$", "смотрит свои устройства")
_rule("POST", r"^/api/auth/sessions/revoke-others$", "отключает другие устройства")
_rule("POST", r"^/api/auth/login$", "входит по паролю")
_rule("POST", r"^/api/auth/login-by-code$", "входит по коду из письма")
_rule("POST", r"^/api/auth/send-login-code$", "просит код для входа")
_rule("POST", r"^/api/auth/login-telegram-webapp$", "входит через Telegram-приложение")
_rule("POST", r"^/api/auth/login-telegram$", "входит через Telegram")
_rule("POST", r"^/api/auth/register$", "регистрируется")
_rule("POST", r"^/api/auth/logout$", "выходит из аккаунта")
_rule("POST", r"^/api/auth/set-password$", "ставит пароль")
_rule("POST", r"^/api/auth/change-password$", "меняет пароль")
_rule("POST", r"^/api/auth/request-password-reset$", "просит восстановление пароля")
_rule("POST", r"^/api/auth/confirm-password-reset$", "восстанавливает пароль")
_rule("POST", r"^/api/auth/send-verify-code$", "просит код подтверждения почты")
_rule("POST", r"^/api/auth/verify-email$", "подтверждает почту")
_rule(None, r"^/api/auth/(google|yandex)/", "входит через внешний аккаунт")
_rule(None, r"^/api/auth/", "работает с аккаунтом")

_rule("GET", r"^/api/keys/actions-config$", "смотрит доступные действия с подпиской")
_rule("GET", r"^/api/keys/(?P<key>[^/]+)/details$", "смотрит подписку")
_rule("GET", r"^/api/keys/(?P<key>[^/]+)/connection$", "смотрит данные подключения")
_rule("GET", r"^/api/keys/(?P<key>[^/]+)/devices$", "смотрит устройства подписки")
_rule("GET", r"^/api/keys/(?P<key>[^/]+)/traffic-history$", "смотрит историю трафика")
_rule("GET", r"^/api/keys/(?P<key>[^/]+)/addons-preview$", "считает докупку к подписке")
_rule("POST", r"^/api/keys/(?P<key>[^/]+)/renew$", "продлевает подписку")
_rule("POST", r"^/api/keys/(?P<key>[^/]+)/addons", "докупает к подписке")
_rule("POST", r"^/api/keys/(?P<key>[^/]+)/reset-devices$", "сбрасывает устройства подписки")
_rule("DELETE", r"^/api/keys/(?P<key>[^/]+)$", "удаляет подписку")
_rule("GET", r"^/api/keys$", "смотрит свои подписки")
_rule("POST", r"^/api/keys$", "создаёт подписку")

_rule("GET", r"^/api/tariffs/public$", "смотрит тарифы")
_rule("GET", r"^/api/tariffs/groups$", "смотрит группы тарифов")
_rule("GET", r"^/api/tariffs/config-price$", "считает цену опций тарифа")
_rule("POST", r"^/api/tariffs/purchase$", "покупает тариф")
_rule("GET", r"^/api/payment-links/", "проверяет платёж")
_rule("POST", r"^/api/payment-links", "создаёт ссылку на оплату")
_rule(None, r"^/api/payments/webhook", "принимает ответ кассы")
_rule(None, r"^/api/payments/", "работает с платежами")
_rule("GET", r"^/api/bonus/daily/me$", "смотрит ежедневный бонус")
_rule("POST", r"^/api/bonus/daily/claim$", "забирает ежедневный бонус")

_rule("GET", r"^/api/partners/payouts/me$", "смотрит свои выплаты")
_rule("GET", r"^/api/partners/payout-method/me$", "смотрит способ выплаты")
_rule("GET", r"^/api/partners/invited/me$", "смотрит приглашённых")
_rule("GET", r"^/api/partners/conditions$", "смотрит условия партнёрки")
_rule("GET", r"^/api/partners/qr$", "берёт QR партнёрской ссылки")
_rule("GET", r"^/api/partners/top$", "смотрит топ партнёров")
_rule("POST", r"^/api/partners/payouts", "запрашивает выплату")
_rule(None, r"^/api/partners/", "работает с партнёркой")
_rule("GET", r"^/api/referrals/conditions$", "смотрит условия рефералки")
_rule("GET", r"^/api/referrals/qr$", "берёт QR реферальной ссылки")
_rule("GET", r"^/api/referrals/top$", "смотрит топ рефералов")
_rule("GET", r"^/api/referrals/list$", "смотрит своих рефералов")
_rule(None, r"^/api/referrals/", "работает с рефералкой")
_rule("GET", r"^/api/gifts/my$", "смотрит свои подарки")
_rule("POST", r"^/api/gifts/redeem$", "активирует подарок")
_rule("POST", r"^/api/gifts$", "создаёт подарок")
_rule(None, r"^/api/gifts/", "работает с подарками")

_rule("GET", r"^/api/tickets/stream$", "слушает обновления обращений")
_rule("GET", r"^/api/tickets/(?P<id>[^/]+)$", "открывает обращение")
_rule("POST", r"^/api/tickets/(?P<id>[^/]+)/messages$", "пишет в обращение")
_rule("GET", r"^/api/tickets$", "смотрит свои обращения")
_rule("POST", r"^/api/tickets$", "создаёт обращение")
_rule("GET", r"^/api/notifications$", "смотрит уведомления")
_rule("POST", r"^/api/notifications/read-all$", "отмечает уведомления прочитанными")
_rule("POST", r"^/api/notifications/(?P<id>[^/]+)/read$", "читает уведомление")
_rule("DELETE", r"^/api/notifications/(?P<id>[^/]+)$", "удаляет уведомление")
_rule(None, r"^/api/push/", "работает с push-уведомлениями")

_rule("GET", r"^/api/flows/(?P<flow>[^/]+)$", "берёт сценарий «{flow}»")
_rule("POST", r"^/api/web/analytics/page-views$", "шлёт просмотр страницы")
_rule("POST", r"^/api/analytics/flow-events$", "шлёт событие сценария")
_rule("POST", r"^/api/web/error-reports$", "шлёт отчёт об ошибке")
_rule("GET", r"^/api/web/analytics/", "смотрит аналитику сайта")

_rule("GET", r"^/api/site-config$", "берёт настройки сайта")
_rule("GET", r"^/api/site/revision$", "проверяет версию настроек")
_rule("GET", r"^/api/site/init-state$", "проверяет первичную настройку")
_rule("GET", r"^/api/web/node-status$", "смотрит состояние серверов")
_rule("GET", r"^/api/telegram-widget-bot$", "берёт бота для виджета Telegram")
_rule("GET", r"^/api/meta/update-check$", "проверяет обновления")
_rule("GET", r"^/api/version$", "смотрит версию")
_rule("GET", r"^/api/health$", "проверяет доступность")
_rule("GET", r"^/api/admin/tickets/unanswered-count$", "считает неотвеченные обращения")
_rule("GET", r"^/api/admin/tickets/stream$", "слушает обновления обращений в админке")
_rule("GET", r"^/api/admin/tickets", "смотрит обращения в админке")
_rule(None, r"^/api/admin/", "работает в админке")


_rule("GET", r"^/api/web/logs$", "смотрит логи")
_rule("POST", r"^/api/log$", "шлёт логи браузера")
_rule("GET", r"^/api/settings/schema$", "смотрит схему настроек")
_rule(None, r"^/api/settings/configs/(?P<name>[^/]+)$", "меняет настройки «{name}»")
_rule(None, r"^/api/settings/", "работает с настройками")
_rule("GET", r"^/api/web/page-titles$", "смотрит названия страниц")
_rule("GET", r"^/api/web/maintenance$", "проверяет режим техработ")
_rule("POST", r"^/api/site/construction-mode$", "переключает режим техработ")
_rule("GET", r"^/api/web/node-status/admin$", "смотрит состояние серверов в админке")
_rule("GET", r"^/api/web/admin-audit$", "смотрит журнал действий админов")
_rule("GET", r"^/api/web/error-reports$", "смотрит отчёты об ошибках")
_rule("GET", r"^/api/web/notify-rules$", "смотрит правила уведомлений")
_rule("GET", r"^/api/web/imports/status$", "проверяет импорт оформления")
_rule("POST", r"^/api/web/import$", "импортирует оформление")
_rule("POST", r"^/api/web/upload$", "загружает файл")
_rule("POST", r"^/api/web/showcase$", "переключает режим витрины")
_rule("POST", r"^/api/web/install-default-design$", "ставит оформление по умолчанию")
_rule("POST", r"^/api/web/packs/install$", "ставит набор")
_rule("POST", r"^/api/web/design/rollback$", "откатывает оформление")
_rule("GET", r"^/api/web/pwa-icon", "берёт иконку приложения")
_rule("GET", r"^/api/web/custom-element-builds", "смотрит сборки своих элементов")
_rule("GET", r"^/api/account/summary$", "смотрит сводку клиента")
_rule("GET", r"^/api/management/dashboard$", "открывает сводку админки")
_rule(None, r"^/api/management/broadcast", "работает с рассылкой")
_rule(None, r"^/api/management/bulk", "работает с массовыми действиями")
_rule(None, r"^/api/management/", "работает в управлении")
_rule("GET", r"^/api/users/search$", "ищет клиентов")
_rule("GET", r"^/api/users/(?P<id>[^/]+)/card$", "открывает карточку клиента {id}")
_rule(None, r"^/api/users/", "работает с клиентами")
_rule("GET", r"^/api/keys/me$", "смотрит свои подписки")
_rule("GET", r"^/api/keys/(?P<key>[^/]+)/qr$", "берёт QR подписки")
_rule("GET", r"^/api/tariffs/stats$", "смотрит статистику тарифов")
_rule("POST", r"^/api/tariffs/trial$", "включает пробный период")
_rule("GET", r"^/api/tariffs$", "смотрит тарифы в админке")
_rule(None, r"^/api/tariffs/(?P<id>[^/]+)$", "меняет тариф {id}")
_rule("GET", r"^/api/coupons", "смотрит купоны")
_rule("GET", r"^/api/servers", "смотрит серверы")
_rule("GET", r"^/api/payments$", "смотрит платежи")
_rule("GET", r"^/api/tracking-sources", "смотрит источники трафика")
_rule("GET", r"^/api/analytics/(?P<what>[^/]+)$", "смотрит аналитику «{what}»")
_rule("GET", r"^/api/stats", "смотрит статистику")
_rule("GET", r"^/api/gifts$", "смотрит подарки в админке")
_rule("GET", r"^/api/(web/)?(app-info|app/info|me|profile)$", "берёт данные профиля")
_rule("GET", r"^/(favicon\.ico|robots\.txt|sitemap\.xml)$", "берёт служебный файл сайта")
_rule("GET", r"^/api/openapi\.json$", "смотрит описание API")
_rule("GET", r"^/$", "открывает корень сайта")


def describe_action(method: str, path: str) -> str:
    """Человеческое описание запроса: «открывает витрину», «продлевает подписку»."""
    upper = (method or "").upper()
    clean = (path or "").split("?", 1)[0] or "/"
    for methods, pattern, phrase in _RULES:
        if methods is not None and upper not in methods:
            continue
        match = pattern.search(clean)
        if match:
            return phrase.format(**match.groupdict()) if match.groupdict() else phrase
    return f"{_VERBS.get(upper, 'запрашивает')} {clean}"


def describe_status(status_code: int) -> str:
    """Итог запроса словами, с кодом для нестандартных ответов."""
    known = _STATUSES.get(status_code)
    if known:
        return known if status_code < 400 else f"{known} ({status_code})"
    if status_code < 400:
        return f"ответ {status_code}"
    return f"ошибка {status_code}"


def describe_actor(tg_id: int | None, identity_id: str | None) -> str:
    """Кто сделал запрос: Telegram-клиент, клиент сайта или гость."""
    if tg_id:
        return f"клиент Telegram {tg_id}"
    if identity_id:
        return f"клиент {str(identity_id)[:8]}"
    return "гость"


def describe_duration(duration_ms: int) -> str:
    """Длительность: миллисекунды до секунды, дальше секунды."""
    if duration_ms < 1000:
        return f"{duration_ms} мс"
    return f"{duration_ms / 1000:.1f} с".replace(".", ",")
