import re

from urllib.parse import urlsplit


def _numeric_tail(value: object) -> int | None:
    if not isinstance(value, str | int) or isinstance(value, bool):
        return None
    text = str(value)
    if not re.fullmatch(r"-?[0-9]+", text):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def saved_public_subscription_tail(link: str | None, email: str, public_link: str) -> int | None:
    """Извлекает числовой хвост доверенной ссылки подписки."""
    if not isinstance(link, str) or not email or any(char in email for char in "/?#"):
        return None
    try:
        base, saved = urlsplit(public_link), urlsplit(link)
    except (TypeError, ValueError):
        return None
    if base.scheme not in ("http", "https") or not base.netloc or base.query or base.fragment:
        return None
    if (saved.scheme, saved.netloc) != (base.scheme, base.netloc) or saved.query or saved.fragment:
        return None
    prefix = f"{base.path.rstrip('/')}/{email}/"
    if not saved.path.startswith(prefix):
        return None
    return _numeric_tail(saved.path[len(prefix) :])


def subscription_tail_matches(
    requested_tail: object,
    current_tg: int | None,
    saved_link: str | None,
    email: str,
    public_link: str,
) -> bool:
    """Проверяет хвост ссылки по текущему Telegram ID или сохранённой ссылке."""
    requested = _numeric_tail(requested_tail)
    if requested is None:
        return False
    current = _numeric_tail(current_tg)
    saved = saved_public_subscription_tail(saved_link, email, public_link)
    return (current is not None and requested == current) or (saved is not None and requested == saved)


def preserve_saved_public_link(
    candidate: str | None,
    saved_link: str | None,
    email: str,
    public_link: str,
) -> str | None:
    """Сохраняет ранее выданную публичную ссылку ключа."""
    if (
        saved_public_subscription_tail(candidate, email, public_link) is not None
        and saved_public_subscription_tail(saved_link, email, public_link) is not None
    ):
        return saved_link
    return candidate
