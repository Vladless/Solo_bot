import json

from urllib.parse import urlsplit

from requests import Session

from core.executor import run_io
from logger import logger


try:
    from pywebpush import WebPushException, webpush

    _WEBPUSH_AVAILABLE = True
except ImportError:
    _WEBPUSH_AVAILABLE = False

try:
    from settings.config import VAPID_CLAIMS_EMAIL, VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY
except ImportError:
    VAPID_PRIVATE_KEY = ""
    VAPID_PUBLIC_KEY = ""
    VAPID_CLAIMS_EMAIL = ""


PUSH_HTTP_TIMEOUT_SECONDS = 10.0
_PUSH_HOSTS = frozenset({"fcm.googleapis.com", "updates.push.services.mozilla.com"})
_PUSH_HOST_SUFFIXES = (".push.apple.com", ".notify.windows.com")


def validate_push_endpoint(endpoint: str) -> str:
    """Разрешает HTTPS-адреса доверенных браузерных push-сервисов."""
    invalid = ValueError("Недопустимый адрес сервиса push-уведомлений")
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > 4096:
        raise invalid
    if any(ord(char) <= 32 or ord(char) == 127 for char in endpoint):
        raise invalid
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise invalid from exc
    host = parsed.hostname or ""
    valid_host = host in _PUSH_HOSTS or host.endswith(_PUSH_HOST_SUFFIXES)
    if (
        parsed.scheme != "https"
        or not valid_host
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise invalid
    return endpoint


class _PushSession(Session):
    def post(self, url, **kwargs):
        """Запрещает перенаправления за пределы выбранного push-сервиса."""
        validate_push_endpoint(url)
        kwargs["allow_redirects"] = False
        return super().post(url, **kwargs)


def _send_push(subscription_info: dict, payload: str):
    """Отправляет ограниченный по времени запрос в рабочем потоке."""
    with _PushSession() as session:
        return webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": f"mailto:{VAPID_CLAIMS_EMAIL}"},
            timeout=PUSH_HTTP_TIMEOUT_SECONDS,
            requests_session=session,
        )


def push_enabled() -> bool:
    return _WEBPUSH_AVAILABLE and bool(VAPID_PRIVATE_KEY) and bool(VAPID_PUBLIC_KEY)


async def send_push_notification(
    subscription_info: dict,
    title: str,
    body: str,
    url: str = "/dashboard",
    tag: str = "solo-notification",
) -> str:
    """Отправляет браузерное уведомление и возвращает результат доставки."""
    if not push_enabled():
        logger.debug("[WebPush] push отключён (нет VAPID ключей или pywebpush)")
        return "error"

    try:
        validate_push_endpoint(subscription_info.get("endpoint"))
    except (AttributeError, ValueError):
        logger.warning("[WebPush] Небезопасный адрес подписки отклонён")
        return "dead"

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "tag": tag,
    })

    try:
        await run_io(_send_push, subscription_info, payload)
        logger.debug("[WebPush] уведомление отправлено: {}", title)
        return "ok"
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (404, 410):
            logger.info("[WebPush] подписка недействительна ({}), помечена на удаление", status)
            return "dead"
        logger.error("[WebPush] ошибка отправки: {} (HTTP {})", type(e).__name__, status)
        return "error"
    except Exception as e:
        logger.error("[WebPush] неожиданная ошибка: {}", type(e).__name__)
        return "error"


async def send_push_to_many(
    subscriptions: list[dict],
    title: str,
    body: str,
    url: str = "/dashboard",
    tag: str = "solo-notification",
) -> tuple[int, list[str]]:
    """Отправляет уведомления и возвращает число доставок и адреса недействительных подписок."""
    sent = 0
    dead: list[str] = []
    for sub in subscriptions:
        result = await send_push_notification(sub, title, body, url, tag)
        if result == "ok":
            sent += 1
        elif result == "dead":
            endpoint = sub.get("endpoint") if isinstance(sub, dict) else None
            if endpoint:
                dead.append(str(endpoint))
    return sent, dead
