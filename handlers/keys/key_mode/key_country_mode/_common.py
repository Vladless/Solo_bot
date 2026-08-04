import asyncio
import uuid

from datetime import datetime
from typing import Any

import pytz

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from py3xui import AsyncApi
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from bot import bot
from core.bootstrap import BUTTONS_CONFIG, MODES_CONFIG
from database import (
    add_user,
    check_server_name_by_cluster,
    check_user_exists,
    filter_cluster_by_subgroup,
    get_key_details,
    get_tariff_by_id,
    get_trial,
    update_balance,
    update_trial,
)
from database.access.resolution import notify_telegram_chat_id, resolve_user_optional
from database.models import Key, Server, ServerSpecialgroup
from handlers.keys.utils import build_key_callback, resolve_key
from handlers.utils import (
    ALLOWED_GROUP_CODES,
    build_support_button,
    edit_or_send_message,
    generate_random_email,
    get_least_loaded_cluster,
    is_full_remnawave_cluster,
)
from hooks.hook_buttons import insert_hook_buttons
from hooks.processors import (
    process_cluster_override,
    process_intercept_key_creation_message,
    process_key_creation_complete,
    process_remnawave_webapp_override,
)
from logger import logger
from panels._3xui import delete_client, get_xui_instance
from panels.remnawave import RemnawaveAPI, get_vless_link_for_remnawave_by_username
from services.errors import InsufficientFundsError
from services.operations import create_client_on_server
from services.operations.aggregated_links import make_aggregated_link
from services.tariffs.tariff_display import (
    build_key_created_message,
    get_effective_limits_for_key,
)
from settings.buttons import (
    BACK,
    CONNECT_DEVICE,
    MAIN_MENU,
    MY_SUB,
    ROUTER_BUTTON,
    SUPPORT,
    TV_BUTTON,
)
from settings.config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    REMNAWAVE_LOGIN,
    REMNAWAVE_PASSWORD,
    REMNAWAVE_WEBAPP,
    REMNAWAVE_WEBAPP_OPEN_IN_BROWSER,
    SUPPORT_CHAT_URL,
)
from settings.texts import SELECT_COUNTRY_MSG


router = Router()
moscow_tz = pytz.timezone("Europe/Moscow")
GB = 1024 * 1024 * 1024
