from logger import logger


async def send_admin_alert(text: str) -> bool:
    """Шлёт текстовое уведомление всем админам через основной бот.
    Best-effort: не бросает исключений, возвращает True если хоть кому-то ушло."""
    try:
        from settings.config import ADMIN_ID
    except Exception:
        return False
    if not ADMIN_ID:
        return False
    try:
        from bot import bot
    except Exception:
        logger.debug("[AdminAlert] bot недоступен")
        return False
    sent = False
    for admin_id in ADMIN_ID:
        try:
            await bot.send_message(admin_id, text, parse_mode=None)
            sent = True
        except Exception as exc:
            logger.warning("[AdminAlert] не удалось отправить {}: {}", admin_id, exc)
    return sent
