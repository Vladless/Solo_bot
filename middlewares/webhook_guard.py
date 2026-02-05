from aiohttp import web


def telegram_webhook_guard_middleware(webhook_path: str, secret_token: str):
    @web.middleware
    async def middleware(request: web.Request, handler):
        if request.path != webhook_path:
            return web.Response(status=204)

        if request.method != "POST":
            return web.Response(status=204)

        header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if header != secret_token:
            return web.Response(status=204)

        return await handler(request)

    return middleware
