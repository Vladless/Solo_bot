import datetime
from datetime import datetime

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

from bot import bot
from config import BLOCK_DURATION, SERVER_COUNTRIES, TIMESTAMP_TTL
from database import get_key_details
from handlers.buttons import MAIN_MENU
from handlers.texts import TORRENT_BLOCKED_MSG, TORRENT_UNBLOCKED_MSG
from logger import logger

last_unblock_data = {}


def get_country_from_server(server: str) -> str:
    """
    Определяет страну сервера по его имени или домену.
    Ищет совпадение части домена в полных доменах.
    """
    server_part = server.split(".")[0]

    for full_domain, country in SERVER_COUNTRIES.items():
        if server_part in full_domain:
            return country
    return server


def handle_telegram_errors(func):
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except TelegramForbiddenError:
            tg_id = kwargs.get("tg_id") or args[1]
            logger.warning(f"🚫 Бот заблокирован пользователем {tg_id}.")
            return False
        except TelegramBadRequest:
            tg_id = kwargs.get("tg_id") or args[1]
            logger.warning(f"🚫 Чат не найден для пользователя {tg_id}.")
            return False
        except Exception as e:
            tg_id = kwargs.get("tg_id") or args[1]
            logger.error(f"❌ Ошибка отправки сообщения пользователю {tg_id}: {e}")
            return False

    return wrapper


@handle_telegram_errors
async def send_notification(
    tg_id: int, username: str, ip: str, server: str, action: str, timestamp: str
):
    country = get_country_from_server(server)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    if action == "block":
        message = TORRENT_BLOCKED_MSG.format(
            username=username, country=country, duration=BLOCK_DURATION
        )
    else:
        message = TORRENT_UNBLOCKED_MSG.format(username=username, country=country)

    await bot.send_message(
        chat_id=tg_id, text=message, parse_mode="HTML", reply_markup=builder.as_markup()
    )
    logger.info(
        f"Отправлено уведомление пользователю {tg_id} о {action} для подписки {username}"
    )
    return True


async def tblocker_webhook(request: web.Request):
    try:
        data = await request.json()
        logger.info(f"Получен запрос от tblocker: {data}")

        username = data.get("username")
        ip = data.get("ip")
        server = data.get("server")
        action = data.get("action")
        timestamp = data.get("timestamp")

        if not all([username, ip, server, action, timestamp]):
            logger.error("Неполные данные в вебхуке")
            return web.json_response({"error": "Missing required fields"}, status=400)

        global last_unblock_data
        current_time = datetime.now().timestamp()

        last_unblock_data = {
            k: v
            for k, v in last_unblock_data.items()
            if current_time - v["received_at"] <= TIMESTAMP_TTL
        }

        cache_key = f"{username}:{server}"
        if action == "unblock" and cache_key in last_unblock_data:
            if timestamp == last_unblock_data[cache_key]["timestamp"]:
                return web.json_response(
                    {"status": "ok", "message": "duplicate unblock skipped"}
                )

        if action == "unblock":
            last_unblock_data[cache_key] = {
                "timestamp": timestamp,
                "received_at": current_time,
            }

        sessionmaker = request.app["sessionmaker"]
        async with sessionmaker() as session:
            key_info = await get_key_details(session, username)

            if not key_info:
                logger.error(f"Ключ не найден для email {username}")
                return web.json_response({"error": "Key not found"}, status=404)

            success = await send_notification(
                tg_id=key_info["tg_id"],
                username=username,
                ip=ip,
                server=server,
                action=action,
                timestamp=timestamp,
            )

            if not success:
                logger.warning(
                    f"Не удалось отправить уведомление пользователю {key_info['tg_id']}"
                )

        return web.json_response({"status": "ok"})

    except Exception as e:
        logger.error(f"Ошибка при обработке вебхука: {str(e)}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)
