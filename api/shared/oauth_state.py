import base64
import hashlib
import hmac
import secrets
import time


STATE_TTL_SECONDS = 600


def sign_state(secret: str, payload: str) -> str:
    """Подпись состояния OAuth: своя у каждого провайдера за счёт секрета."""
    mac = hmac.new(str(secret).encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def make_state(secret: str, return_to: str) -> tuple[str, str]:
    """Состояние для редиректа: возвращает сам state и nonce для сверки."""
    nonce = secrets.token_urlsafe(16)
    payload = f"{nonce}.{int(time.time())}.{return_to}"
    raw = f"{payload}.{sign_state(secret, payload)}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("="), nonce


def verify_state(secret: str, state: str, fallback_return_to: str) -> tuple[str, str] | None:
    """Проверяет подпись и срок state, возвращает (куда вернуть, nonce)."""
    try:
        padded = state + "=" * (-len(state) % 4)
        raw = base64.urlsafe_b64decode(padded).decode()
    except Exception:
        return None
    parts = raw.rsplit(".", 1)
    if len(parts) != 2:
        return None
    payload, sig = parts
    if not hmac.compare_digest(sig, sign_state(secret, payload)):
        return None
    chunks = payload.split(".", 2)
    if len(chunks) != 3:
        return None
    nonce, ts, return_to = chunks
    try:
        if int(time.time()) - int(ts) > STATE_TTL_SECONDS:
            return None
    except Exception:
        return None
    return (return_to or fallback_return_to, nonce)
