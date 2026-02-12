from aiohttp import web


def telegram_webhook_guard_middleware(webhook_path: str, secret_token: str):
    webhook_path = (webhook_path or "").strip()
    if not webhook_path.startswith("/"):
        webhook_path = "/" + webhook_path

    if webhook_path != "/" and webhook_path.endswith("/"):
        webhook_path = webhook_path.rstrip("/")

    @web.middleware
    async def middleware(request: web.Request, handler):
        request_path = request.rel_url.path
        if request_path != "/" and request_path.endswith("/"):
            request_path = request_path.rstrip("/")

        if request_path != webhook_path:
            return await handler(request)

        if request.method != "POST":
            return web.Response(status=204)

        header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if header != secret_token:
            return web.Response(status=204)

        return await handler(request)

    return middleware
