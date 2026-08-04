from __future__ import annotations

from collections.abc import Iterable


def _get_bot_webhook_path() -> str:
    """Путь вебхука бота из конфига (для исключения из шага «успешная оплата»)."""
    try:
        from settings.config import WEBHOOK_PATH

        return ((WEBHOOK_PATH or "").strip().lower()) or ""
    except ImportError:
        return ""


def _is_bot_webhook_path(path: str) -> bool:
    """True только если path — именно вебхук бота (точное совпадение сегмента пути), не касса."""
    bot_path = _get_bot_webhook_path()
    if not bot_path:
        return False
    p = (path or "").strip().lower()
    path_segment = p.split(" ", 1)[1] if " " in p else p
    return path_segment == bot_path or path_segment.rstrip("/") == bot_path.rstrip("/")


AUDIT_STEP_LABELS: dict[str, str] = {
    "start": "Старт",
    "start_coupon": "Старт: купон",
    "start_gift": "Старт: подарок",
    "start_referral": "Старт: рефералка",
    "start_utm": "Старт: UTM",
    "profile": "Профиль",
    "about": "О VPN",
    "instructions": "Инструкции",
    "balance": "Баланс / история оплат",
    "view_keys": "Мои ключи",
    "buy_entry": "Оформление: вход",
    "tariff_config": "Оформление: выбор тарифа/конфига",
    "key_create": "Подписка оформлена (ключ создан)",
    "pay_start": "Оплата: вход / создание ссылки",
    "pay": "Успешная оплата (пополнение)",
    "key_view": "Ключ (карточка)",
    "connect": "Подключение: экран / инструкции / QR",
    "key_manage": "Управление подпиской",
    "renew": "Продление",
    "addons": "Аддоны",
    "referral": "Рефералы",
    "coupons": "Купоны",
    "register": "Регистрация (API)",
    "login": "Вход (API)",
    "api_other": "API прочее",
    "admin": "Админ-панель",
    "other": "Прочее",
}


DEFAULT_FUNNEL_STEPS = (
    "start",
    "profile",
    "view_keys",
    "buy_entry",
    "tariff_config",
    "key_create",
    "pay_start",
    "pay",
    "key_view",
    "connect",
)


_CALLBACK_EXACT: dict[str, str] = {
    "start": "start",
    "profile": "profile",
    "about_vpn": "about",
    "instructions": "instructions",
    "view_keys": "view_keys",
    "create_key": "buy_entry",
    "buy": "buy_entry",
    "pay": "pay_start",
    "balance": "balance",
    "balance_history": "balance",
    "activate_coupon": "coupons",
    "cancel_coupon_activation": "coupons",
    "exit_coupon_input": "coupons",
    "invite": "referral",
    "top_referrals": "referral",
    "check_subscription": "start",
    "back_to_tariff_group_list": "tariff_config",
    "back_to_subgroup_tariffs": "tariff_config",
    "cancel_and_back_to_view_keys": "view_keys",
    "fastflow_coupon": "coupons",
    "fastflow_coupon_back": "coupons",
    "fastflow_back": "pay_start",
    "pay_kassai": "pay_start",
    "pay_kassai_cards": "pay_start",
    "pay_kassai_sbp": "pay_start",
    "pay_heleket_crypto": "pay_start",
    "pay_freekassa": "pay_start",
    "pay_robokassa": "pay_start",
}


_CALLBACK_PREFIX: list[tuple[str, str]] = [
    ("view_key|", "key_view"),
    ("view_keys|", "view_keys"),
    ("show_referral_qr|", "referral"),
    ("tariff_subgroup_user|", "tariff_config"),
    ("select_tariff_plan|", "tariff_config"),
    ("cfg_user_devices|", "tariff_config"),
    ("cfg_user_traffic|", "tariff_config"),
    ("rename_key|", "key_manage"),
    ("reset_hwid|", "key_manage"),
    ("delete_key|", "key_manage"),
    ("confirm_delete|", "key_manage"),
    ("update_subscription|", "key_manage"),
    ("change_location|", "key_manage"),
    ("select_country|", "key_manage"),
    ("connect_device|", "connect"),
    ("connect_router|", "connect"),
    ("connect_tv|", "connect"),
    ("connect_pc|", "connect"),
    ("connect_ios|", "connect"),
    ("connect_android|", "connect"),
    ("show_qr|", "connect"),
    ("continue_tv|", "connect"),
    ("pay_currency|", "pay_start"),
    ("choose_payment_currency|", "pay_start"),
    ("cfg_user_confirm|", "key_create"),
    ("choose_payment_provider|", "pay_start"),
    ("kassai_method|", "pay_start"),
    ("kassai_cards_amount|", "pay_start"),
    ("kassai_sbp_amount|", "pay_start"),
    ("kassai_custom_amount|", "pay_start"),
    ("heleket_method|", "pay_start"),
    ("heleket_crypto_amount|", "pay_start"),
    ("heleket_custom_amount|", "pay_start"),
    ("freekassa_amount|", "pay_start"),
    ("robokassa_", "pay_start"),
    ("cfg_renew", "renew"),
    ("key_addons", "addons"),
    ("extend_key", "coupons"),
]


_CALLBACK_CONTAINS: list[tuple[str, str]] = [
    ("connect_", "connect"),
    ("renew", "renew"),
    ("addon", "addons"),
    ("referral", "referral"),
    ("invite", "referral"),
    ("coupon", "coupons"),
    ("users_audit", "admin"),
    ("users_editor", "admin"),
    ("search_user", "admin"),
    ("admin_panel", "admin"),
]


_MESSAGE_STEP_BY_COMMAND: dict[str, str] = {
    "/start": "start",
    "start": "start",
    "/buy": "buy_entry",
    "buy": "buy_entry",
    "/subs": "view_keys",
    "subs": "view_keys",
    "/profile": "profile",
    "profile": "profile",
    "/invite": "referral",
    "invite": "referral",
    "/instructions": "instructions",
    "instructions": "instructions",
    "/activate_coupon": "coupons",
    "activate_coupon": "coupons",
}

_START_PAYLOAD_MAX_LEN = 256
_START_PAYLOAD_MAX_PARTS = 20


_IGNORED_CALLBACK_EXACT: set[str] = {
    " ",
    "back",
    "cancel",
    "back_to_pay",
    "back_to_currency",
    "back_to_tariff_group_list",
    "back_to_subgroup_tariffs",
    "cancel_and_back_to_view_keys",
    "cancel_coupon_activation",
    "exit_coupon_input",
    "fastflow_back",
    "fastflow_coupon_back",
    "cfg_back_menu",
    "cfg_cancel_input",
    "cancel_broadcast",
}


_IGNORED_CALLBACK_PREFIX: tuple[str, ...] = (
    "back:",
    "back_to_",
    "cancel_",
    "gifts_page|",
)

_IGNORED_CALLBACK_EXACT_RULES: dict[str, str] = dict.fromkeys(_IGNORED_CALLBACK_EXACT, "ignore")
_IGNORED_CALLBACK_PREFIX_RULES: tuple[tuple[str, str], ...] = tuple(
    (prefix, "ignore") for prefix in _IGNORED_CALLBACK_PREFIX
)


_HANDLER_CONTAINS: list[tuple[str, str] | tuple[str, str, str]] = [
    ("process_start", "start"),
    ("start_entry", "start"),
    ("show_start_menu", "start"),
    ("process_callback_view_profile", "profile"),
    ("handle_about_vpn", "about"),
    ("send_instructions", "instructions"),
    ("process_callback_or_message_view_keys", "view_keys"),
    ("key_view", "key_view", "key_create"),
    ("handle_user_config_confirm", "key_create"),
    ("finalize_config_and_purchase", "key_create"),
    ("proceed_purchase_with_values", "tariff_config"),
    ("key_create", "buy_entry"),
    ("handle_key_creation", "buy_entry"),
    ("complete_key_renewal", "renew"),
    ("handle_connect_device", "connect"),
    ("process_connect_", "connect"),
    ("process_callback_connect", "connect"),
    ("process_continue_tv", "connect"),
    ("show_qr_code", "connect"),
    ("balance_history", "balance"),
    ("balance_handler", "balance"),
    ("pay", "pay_start"),
    ("choose_payment_provider", "pay_start"),
    ("kassai_", "pay_start"),
    ("heleket_", "pay_start"),
    ("freekassa_", "pay_start"),
    ("robokassa_", "pay_start"),
    ("rename_key", "key_manage"),
    ("reset_hwid", "key_manage"),
    ("delete_key", "key_manage"),
    ("change_location", "key_manage"),
    ("select_country", "key_manage"),
    ("renew", "renew"),
    ("addon", "addons"),
    ("referral", "referral"),
    ("refferal", "referral"),
    ("coupon", "coupons"),
    ("admin_panel", "admin"),
    ("users_audit", "admin"),
    ("users_editor", "admin"),
    ("search_user", "admin"),
    ("auth/register", "register"),
    ("auth/login", "login"),
    ("auth/send-login", "login"),
    ("auth/login-by-code", "login"),
    ("auth/login-telegram", "login"),
    ("auth/set-password", "login"),
    ("auth/change-password", "login"),
    ("auth/request-password-reset", "login"),
    ("auth/confirm-password-reset", "login"),
    ("auth/summary", "login"),
    ("site-config", "api_other"),
    ("tariffs/purchase", "pay_start"),
    ("tariffs/config-price", "tariff_config"),
    ("gifts/redeem", "key_create"),
    ("referrals/apply", "referral"),
]


_API_CONTAINS: list[tuple[str, str]] = [
    ("auth/register", "register"),
    ("auth/login", "login"),
    ("auth/send-login", "login"),
    ("auth/login-by-code", "login"),
    ("auth/login-telegram", "login"),
    ("auth/set-password", "login"),
    ("auth/change-password", "login"),
    ("auth/request-password-reset", "login"),
    ("auth/confirm-password-reset", "login"),
    ("auth/summary", "login"),
    ("site-config", "api_other"),
    ("tariffs/purchase", "pay_start"),
    ("tariffs/config-price", "tariff_config"),
    ("gifts/redeem", "key_create"),
    ("referrals/apply", "referral"),
    ("/keys/create", "key_create"),
]


def _match_step_rules(
    value: str,
    *,
    exact: dict[str, str] | None = None,
    prefixes: Iterable[tuple[str, str]] | None = None,
    contains: Iterable[tuple[str, str] | tuple[str, str, str]] | None = None,
) -> str | None:
    """Универсальный matcher шага по exact/prefix/contains правилам."""
    normalized = (value or "").lower().strip()
    if not normalized:
        return None
    if exact:
        step = exact.get(normalized)
        if step:
            return step
    if prefixes:
        for prefix, step in prefixes:
            if normalized.startswith(prefix):
                return step
    if contains:
        for item in contains:
            substr, step = item[0], item[1]
            exclude = item[2] if len(item) > 2 else None
            if substr in normalized and (exclude is None or exclude not in normalized):
                return step
    return None


def _is_ignored_analytics_event(path: str) -> bool:
    """Исключает чисто навигационные события из аналитики шагов и воронки."""
    p = (path or "").lower().strip()
    if not p.startswith("callback:"):
        return False
    callback_data = p.split(":", 1)[-1]
    return (
        _match_step_rules(
            callback_data,
            exact=_IGNORED_CALLBACK_EXACT_RULES,
            prefixes=_IGNORED_CALLBACK_PREFIX_RULES,
        )
        is not None
    )


def _message_command_step(path: str) -> str | None:
    """Определяет шаг только по точной команде/первому токену сообщения."""
    steps = _message_command_steps(path)
    return steps[0] if steps else None


def _message_command_steps(path: str) -> list[str]:
    """Определяет один или несколько шагов из текстового сообщения."""
    p = (path or "").strip()
    if not p.lower().startswith("message:"):
        return []
    text = (p.split(":", 1)[-1] or "").strip().lower()
    if not text:
        return []
    parts = text.split(None, 1)
    token = parts[0]
    if token.startswith("/"):
        token = token.split("@", 1)[0]
    if token in ("/start", "start"):
        steps = ["start"]
        payload = parts[1].strip() if len(parts) > 1 else ""
        if payload:
            if len(payload) > _START_PAYLOAD_MAX_LEN:
                payload = payload[:_START_PAYLOAD_MAX_LEN]
            payload_parts = payload.split("-")
            if len(payload_parts) > _START_PAYLOAD_MAX_PARTS:
                payload_parts = payload_parts[:_START_PAYLOAD_MAX_PARTS]
            for part in payload_parts:
                part = part.strip()
                if not part:
                    continue
                if "coupons" in part:
                    steps.append("start_coupon")
                    continue
                if "gift" in part:
                    steps.append("start_gift")
                    continue
                if "referral" in part:
                    steps.append("start_referral")
                    continue
                if "utm" in part:
                    steps.append("start_utm")
                    continue
            normalized_payload = "-".join(payload_parts).strip().lower()
            if normalized_payload in _MESSAGE_STEP_BY_COMMAND:
                steps.append(_MESSAGE_STEP_BY_COMMAND[normalized_payload])
            return list(dict.fromkeys(steps))
        return steps
    step = _MESSAGE_STEP_BY_COMMAND.get(token)
    return [step] if step else []


def _callback_step(path: str) -> str | None:
    callback_data = path.split(":", 1)[-1]
    callback_key = callback_data.split("|")[0]
    return _match_step_rules(
        callback_key,
        exact=_CALLBACK_EXACT,
    ) or _match_step_rules(
        callback_data,
        prefixes=_CALLBACK_PREFIX,
        contains=_CALLBACK_CONTAINS,
    )


def _api_step(path: str) -> str:
    step = _match_step_rules(path, contains=_API_CONTAINS)
    if step:
        return step
    if "payment-links" in path or ("payment" in path and "webhook" not in path):
        return "pay_start"
    return "api_other"


def _handler_step(path: str) -> str | None:
    return _match_step_rules(path, contains=_HANDLER_CONTAINS)


def _funnel_step_counts(path: str, result: str, step: str) -> bool:
    """Решает, считать ли событие достижением шага воронки."""
    if result != "success":
        return False
    p = (path or "").lower()
    if step == "pay_start":
        return True
    if step == "pay":
        return p.startswith("payment_success:")
    if step == "key_create":
        return "cfg_user_confirm" in p or "/keys/create" in p
    return True


def _normalize_path_to_step(path: str) -> str:
    """Сводит path_or_handler к шагу по правилам из маппингов."""
    steps = _normalize_path_to_steps(path)
    return steps[0] if steps else "other"


def _normalize_path_to_steps(path: str) -> list[str]:
    """Сводит path_or_handler к одному или нескольким шагам по правилам аудита."""
    if not path:
        return ["other"]
    p = path.lower().strip()
    if p.startswith("payment_success:"):
        return ["pay"]
    if p.startswith("callback:"):
        return [_callback_step(p) or "other"]
    message_steps = _message_command_steps(path)
    if message_steps:
        return message_steps
    if p.startswith("message:"):
        return ["other"]
    if p.startswith(("post ", "get ")):
        return [_api_step(p)]
    return [_handler_step(p) or "other"]
