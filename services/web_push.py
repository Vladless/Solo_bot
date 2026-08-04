import json

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


def push_enabled() -> bool:
    return _WEBPUSH_AVAILABLE and bool(VAPID_PRIVATE_KEY) and bool(VAPID_PUBLIC_KEY)


async def send_push_notification(
    subscription_info: dict,
    title: str,
    body: str,
    url: str = "/dashboard",
    tag: str = "solo-notification",
) -> str:
    """Отправить push одному подписчику. Возвращает 'ok' | 'dead' | 'error'.
    'dead' — эндпоинт удалён сервисом (404/410), подписку нужно удалить."""
    if not push_enabled():
        logger.debug("[WebPush] push отключён (нет VAPID ключей или pywebpush)")
        return "error"

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "tag": tag,
    })

    try:
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": f"mailto:{VAPID_CLAIMS_EMAIL}"},
        )
        logger.debug("[WebPush] уведомление отправлено: {}", title)
        return "ok"
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (404, 410):
            logger.info("[WebPush] подписка недействительна ({}), помечена на удаление", status)
            return "dead"
        logger.error("[WebPush] ошибка отправки: {}", e)
        return "error"
    except Exception as e:
        logger.error("[WebPush] неожиданная ошибка: {}", e)
        return "error"


async def send_push_to_many(
    subscriptions: list[dict],
    title: str,
    body: str,
    url: str = "/dashboard",
    tag: str = "solo-notification",
) -> tuple[int, list[str]]:
    """Отправить push нескольким подписчикам.
    Возвращает (кол-во успешных, список endpoint'ов недействительных подписок)."""
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
