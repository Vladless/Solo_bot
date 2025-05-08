from aiohttp.web_urldispatcher import UrlDispatcher

import bot


async def register_web_routes(router: UrlDispatcher) -> None:
    dp = bot.dp

    # todo: add your api routes here
