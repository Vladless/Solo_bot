from logger import logger


async def send_admin_alert(text: str) -> bool:
    """Отправляет уведомление администраторам и сообщает об успешной доставке хотя бы одному."""
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
