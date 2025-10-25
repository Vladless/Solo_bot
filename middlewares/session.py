from aiogram import BaseMiddleware


class SessionMiddleware(BaseMiddleware):
    def __init__(self, sessionmaker) -> None:
        super().__init__()
        self.sessionmaker = sessionmaker

    async def __call__(self, handler, event, data):
        if data.get("session"):
            return await handler(event, data)
        async with self.sessionmaker() as session:
            data["session"] = session
            return await handler(event, data)
