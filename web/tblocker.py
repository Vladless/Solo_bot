import datetime

from datetime import datetime

import asyncpg

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

from bot import bot
from config import BLOCK_DURATION, DATABASE_URL, SERVER_COUNTRIES, TIMESTAMP_TTL
from database import get_key_details
from handlers.buttons import MAIN_MENU
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
async def send_notification(tg_id: int, username: str, ip: str, server: str, action: str, timestamp: str):
    country = get_country_from_server(server)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    if action == "block":
        message = (
            f"⚠️ <b>Замечено использование торрентов</b> ⚠️\n\n"
            f"<b>Уважаемый пользователь, мы обнаружили использование торрент-трафика в вашей подписке.</b>\n\n"
            f"📋 <b>Детали:</b>"
            f"<blockquote>"
            f"• Подписка: <code>{username}</code>\n"
            f"• Сервер: {country}\n"
            f"• Время блокировки страны: <b>{BLOCK_DURATION} минут</b>"
            f"</blockquote>\n\n"
            f"❗️ <b>Важно:</b>\n"
            f"• Возможно, вы забыли отключить торрент-клиент и сейчас качаете или раздаете торренты через VPN\n"
            f"• Загрузка и раздача через торренты запрещена согласно правилам использования сервиса\n"
            f"• Пожалуйста, полностью выключите торрент-клиент\n\n"
            f"⏳ <b>После истечения времени блокировки доступ будет автоматически восстановлен.</b>\n\n"
            f"⚠️ <b>Внимание:</b> При повторном использовании торрентов, блокировка будет применена снова."
        )
    else:
        message = (
            f"✅ <b>Доступ восстановлен</b>\n\n"
            f"<b>Уважаемый пользователь, временные ограничения для вашей подписки сняты.</b>\n\n"
            f"📋 <b>Детали:</b>"
            f"<blockquote>"
            f"• Подписка: <code>{username}</code>\n"
            f"• Сервер: {country}"
            f"</blockquote>\n\n"
            f"💬 <b>Напоминание:</b>\n"
            f"• Пожалуйста, воздержитесь от использования торрентов\n"
            f"• Убедитесь, что торрент-клиент полностью выключен"
        )

    await bot.send_message(chat_id=tg_id, text=message, parse_mode="HTML", reply_markup=builder.as_markup())
    logger.info(f"Отправлено уведомление пользователю {tg_id} о {action} для подписки {username}")
    return True


async def tblocker_webhook(request):
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
            k: v for k, v in last_unblock_data.items() if current_time - v["received_at"] <= TIMESTAMP_TTL
        }

        cache_key = f"{username}:{server}"
        if action == "unblock" and cache_key in last_unblock_data:
            if timestamp == last_unblock_data[cache_key]["timestamp"]:
                return web.json_response({"status": "ok", "message": "duplicate unblock skipped"})

        if action == "unblock":
            last_unblock_data[cache_key] = {"timestamp": timestamp, "received_at": current_time}

        conn = await asyncpg.connect(DATABASE_URL)
        try:
            key_info = await get_key_details(username, conn)

            if not key_info:
                logger.error(f"Ключ не найден для email {username}")
                return web.json_response({"error": "Key not found"}, status=404)

            success = await send_notification(key_info["tg_id"], username, ip, server, action, timestamp)

            if not success:
                logger.warning(f"Не удалось отправить уведомление пользователю {key_info['tg_id']}")

            return web.json_response({"status": "ok"})
        finally:
            await conn.close()

    except Exception as e:
        logger.error(f"Ошибка при обработке вебхука: {str(e)}")
        return web.json_response({"error": str(e)}, status=500)
