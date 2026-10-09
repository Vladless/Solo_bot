import json
import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database.broadcasts import (
    ALL_CHANNELS as ALL_CHANNELS,
    channels_to_str as channels_to_str,
    get_recipient_emails as get_recipient_emails,
    get_recipients as get_recipients,
    is_telegram_chat_id as is_telegram_chat_id,
    parse_channels as parse_channels,
)
from logger import logger


def parse_message_buttons(text: str) -> tuple[str, InlineKeyboardMarkup | None]:
    if "BUTTONS:" not in text:
        return text, None

    parts = text.split("BUTTONS:", 1)
    clean_text = parts[0].strip()
    buttons_text = parts[1].strip()

    if not buttons_text:
        return clean_text, None

    buttons = []
    button_lines = [line.strip() for line in buttons_text.split("\n") if line.strip()]

    for line in button_lines:
        try:
            cleaned_line = re.sub(r'<tg-emoji emoji-id="[^"]*">([^<]*)</tg-emoji>', r"\1", line)
            button_data = json.loads(cleaned_line)

            if not isinstance(button_data, dict) or "text" not in button_data:
                logger.warning(f"[Sender] Неверный формат кнопки: {line}")
                continue

            text_btn = button_data["text"]

            if "callback" in button_data:
                callback_data = button_data["callback"]
                if len(callback_data) > 64:
                    logger.warning(f"[Sender] Callback слишком длинный: {callback_data}")
                    continue
                button = InlineKeyboardButton(text=text_btn, callback_data=callback_data)
            elif "url" in button_data:
                url = button_data["url"]
                button = InlineKeyboardButton(text=text_btn, url=url)
            else:
                logger.warning(f"[Sender] Кнопка без действия: {line}")
                continue

            buttons.append([button])

        except json.JSONDecodeError as e:
            logger.warning(f"[Sender] Ошибка парсинга JSON кнопки: {line} - {e}")
            continue
        except Exception as e:
            logger.error(f"[Sender] Ошибка создания кнопки: {line} - {e}")
            continue

    if not buttons:
        return clean_text, None

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return clean_text, keyboard
