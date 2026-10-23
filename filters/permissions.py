from __future__ import annotations


PERM_USERS = "users"
PERM_KEYS = "keys"
PERM_BULK = "bulk"
PERM_TARIFFS = "tariffs"
PERM_CLUSTERS = "clusters"
PERM_BROADCASTING = "broadcasting"
PERM_COUPONS = "coupons"
PERM_GIFTS = "gifts"
PERM_STATS = "stats"
PERM_ADS = "ads"
PERM_MODULES = "modules"
PERM_SETTINGS = "settings"
PERM_MANAGEMENT = "management"
PERM_ADMINS = "admins"
PERM_EMOJI = "emoji"


PERMISSION_LABELS: dict[str, str] = {
    PERM_USERS: "👤 Поиск",
    PERM_BULK: "📦 Массовые действия",
    PERM_TARIFFS: "💸 Тарифы",
    PERM_CLUSTERS: "🖥️ Серверы",
    PERM_BROADCASTING: "📢 Рассылки",
    PERM_COUPONS: "🎟️ Купоны",
    PERM_GIFTS: "🎁 Подарки",
    PERM_STATS: "📊 Статистика",
    PERM_ADS: "📈 UTM / Аналитика",
    PERM_MODULES: "🧩 Модули",
    PERM_SETTINGS: "⚙️ Настройки",
    PERM_MANAGEMENT: "🤖 Управление ботом",
    PERM_ADMINS: "👑 Управление админами",
    PERM_EMOJI: "😀 Эмоджи",
}

ALL_PERMISSIONS: tuple[str, ...] = tuple(PERMISSION_LABELS.keys())

PERM_KEY_VIEW = "key_view"
PERM_KEY_CREATE = "key_create"
PERM_KEY_REISSUE = "key_reissue"
PERM_KEY_DELETE = "key_delete"
PERM_KEY_EXPIRY = "key_expiry"
PERM_KEY_TARIFF = "key_tariff"
PERM_KEY_DEVICES = "key_devices"
PERM_KEY_TRAFFIC = "key_traffic"
PERM_KEY_LOCATION = "key_location"
PERM_KEY_FREEZE = "key_freeze"
PERM_USER_BALANCE = "user_balance"
PERM_USER_BAN = "user_ban"
GRANULAR_ACTIONS_CONFIGURED = "granular_actions_configured"

ACTION_PERMISSION_LABELS: dict[str, str] = {
    PERM_KEY_VIEW: "🔎 Просмотр ключей и ссылок",
    PERM_KEY_CREATE: "➕ Создание подписок",
    PERM_KEY_REISSUE: "🔄 Перевыпуск ключей",
    PERM_KEY_DELETE: "❌ Удаление ключей и пользователей",
    PERM_KEY_EXPIRY: "⏳ Изменение срока",
    PERM_KEY_TARIFF: "📦 Изменение тарифа",
    PERM_KEY_DEVICES: "💻 Управление устройствами",
    PERM_KEY_TRAFFIC: "📊 Управление трафиком",
    PERM_KEY_LOCATION: "🌍 Изменение локации",
    PERM_KEY_FREEZE: "⏯ Заморозка подписок",
    PERM_USER_BALANCE: "💸 Изменение баланса",
    PERM_USER_BAN: "🚫 Блокировка пользователей",
}
ALL_ACTION_PERMISSIONS = tuple(ACTION_PERMISSION_LABELS)
STORED_PERMISSIONS = (*ALL_PERMISSIONS, *ALL_ACTION_PERMISSIONS, GRANULAR_ACTIONS_CONFIGURED)


_LEGACY_PERMISSION_ALIASES: dict[str, str] = {
    PERM_KEYS: PERM_USERS,
}


def normalize_permissions(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        item = _LEGACY_PERMISSION_ALIASES.get(item, item)
        if item in STORED_PERMISSIONS and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def action_permissions(raw) -> frozenset[str]:
    """Сохраняет прежний доступ до настройки детальных прав."""
    permissions = set(normalize_permissions(raw))
    if GRANULAR_ACTIONS_CONFIGURED not in permissions:
        return frozenset(ALL_ACTION_PERMISSIONS)
    return frozenset(permissions.intersection(ALL_ACTION_PERMISSIONS))
