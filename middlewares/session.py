from aiogram import BaseMiddleware


class SessionMiddleware(BaseMiddleware):
    def __init__(self, sessionmaker):
        self.sessionmaker = sessionmaker

    async def __call__(self, handler, event, data):
        if data.get("session"):
            return await handler(event, data)

        session = self.sessionmaker()
        data["session"] = session
        try:
            result = await handler(event, data)
            await session.commit()
            return result
        except Exception:
            try:
                await session.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                await session.close()
            except Exception:
                pass
