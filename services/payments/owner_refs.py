import re

from database.access.resolution import TelegramId, UserId


def payment_owner_token(user_ref: int) -> str:
    """Кодирует ссылку на владельца платёжного счёта."""
    if isinstance(user_ref, UserId):
        if user_ref <= 0:
            raise ValueError("Billing user ID must be positive")
        return f"u{int(user_ref)}"
    return str(int(user_ref))


def parse_payment_owner(value: object) -> int:
    """Определяет тип и номер владельца по платёжному токену."""
    if isinstance(value, UserId | TelegramId):
        return value
    token = str(value).strip()
    if re.fullmatch(r"u[1-9][0-9]*", token):
        return UserId(token[1:])
    if re.fullmatch(r"-?[0-9]+", token):
        return TelegramId(token)
    raise ValueError("Invalid payment owner token")


def payment_owner_from_label(label: str) -> int:
    match = re.match(r"^(u[1-9][0-9]*|-?[0-9]+)-", label)
    if match is None:
        raise ValueError("Invalid payment label")
    return parse_payment_owner(match[1])
