from aiogram import BaseMiddleware

from logger import logger


class SessionMiddleware(BaseMiddleware):
    def __init__(self, sessionmaker):
        self.sessionmaker = sessionmaker

    async def _rollback(self, session, context: str) -> None:
        """Attempt rollback so invalid transaction is cleared; log if rollback fails."""
        try:
            await session.rollback()
        except Exception as rollback_err:
            logger.warning(
                "Session rollback failed during %s — %s: %s",
                context,
                type(rollback_err).__name__,
                rollback_err,
                exc_info=True,
            )

    async def __call__(self, handler, event, data):
        if data.get("session"):
            return await handler(event, data)

        session = self.sessionmaker()
        data["session"] = session
        committed = False
        handler_name = getattr(handler, "__qualname__", getattr(handler, "__name__", str(handler)))
        event_type = type(event).__name__

        try:
            result = await handler(event, data)
            try:
                await session.commit()
                committed = True
                return result
            except Exception as commit_err:
                logger.warning(
                    "Session commit failed, rolling back (ошибка не пробрасывается) — handler=%s, event=%s, error=%s: %s",
                    handler_name,
                    event_type,
                    type(commit_err).__name__,
                    commit_err,
                    exc_info=True,
                )
                await self._rollback(session, "commit failure")
                return result
        except Exception as e:
            logger.warning(
                "Session rollback: ошибка при обработке — handler=%s, event=%s, error=%s: %s",
                handler_name,
                event_type,
                type(e).__name__,
                e,
                exc_info=True,
            )
            await self._rollback(session, "handler failure")
            raise
        finally:
            if not committed:
                try:
                    await session.rollback()
                except Exception:
                    pass
            try:
                await session.close()
            except Exception:
                pass
