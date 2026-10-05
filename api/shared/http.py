from urllib.parse import urlsplit

from fastapi import Request


def resolve_public_base_url(request: Request) -> str:
    """Внешний адрес сайта для ссылок в ответах: origin, затем referer, затем заголовки прокси."""
    origin = str(request.headers.get("origin") or "").strip()
    if origin.startswith(("http://", "https://")):
        return origin.rstrip("/")
    referer = str(request.headers.get("referer") or request.headers.get("referrer") or "").strip()
    if referer.startswith(("http://", "https://")):
        parsed = urlsplit(referer)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    forwarded_host = str(request.headers.get("x-forwarded-host") or "").strip()
    host = forwarded_host or str(request.headers.get("host") or "").strip()
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    scheme = forwarded_proto if forwarded_proto in {"http", "https"} else request.url.scheme
    if host:
        return f"{scheme}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def resolve_default_web_payment_provider() -> str | None:
    """Касса по умолчанию для оплат с сайта: первая включённая из списка веб-провайдеров."""
    from core.bootstrap import PAYMENTS_CONFIG
    from services.payments.providers import get_web_link_provider_ids

    ids = get_web_link_provider_ids()
    for provider_id in ids:
        if bool(PAYMENTS_CONFIG.get(provider_id)):
            return provider_id
    return ids[0] if ids else None
