from aiogram import BaseMiddleware

from logger import logger


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
        except Exception as e:
            handler_name = getattr(handler, "__qualname__", getattr(handler, "__name__", str(handler)))
            event_type = type(event).__name__
            logger.warning(
                "Session rollback: ошибка при обработке — handler=%s, event=%s, error=%s: %s",
                handler_name,
                event_type,
                type(e).__name__,
                e,
                exc_info=True,
            )
            try:
                await session.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                await session.rollback()
            except Exception:
                pass
            try:
                await session.close()
            except Exception:
                pass
