import asyncio
import time
import uuid

from datetime import datetime, timedelta, timezone

import pytz

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MODES_CONFIG
from database import (
    check_server_name_by_cluster,
    delete_key,
    delete_user_data,
    get_active_tariffs_by_group_code,
    get_key_by_email,
    get_key_details,
    get_keys,
    get_server_names,
    get_servers,
    get_tariff_by_id,
    get_tariffs_for_cluster,
    mark_key_as_frozen,
    mark_key_as_unfrozen,
    save_admin_key_config,
    update_key_expiry,
    update_key_subscription_links,
)
from database.models import Key
from filters.admin import IsAdminFilter
from handlers.utils import generate_random_email, handle_error
from hooks.hook_buttons import insert_hook_buttons
from hooks.processors import process_admin_key_edit_menu
from logger import logger
from middlewares.session import release_session_early
from panels.remnawave import RemnawaveAPI
from services.operations import (
    create_key_on_cluster,
    delete_key_from_cluster,
    get_user_traffic,
    renew_key_in_cluster,
    reset_traffic_in_cluster,
    toggle_client_on_cluster,
    update_subscription,
)
from services.users_utils import resolve_admin_key
from settings.buttons import BACK
from settings.config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, REMNAWAVE_TOKEN_LOGIN_ENABLED, USE_COUNTRY_SELECTION

from ...panel.keyboard import AdminPanelCallback, build_admin_back_btn, build_admin_back_kb
from ..keyboard import (
    AdminUserEditorCallback,
    AdminUserKeyEditorCallback,
    build_cluster_selection_kb,
    build_editor_kb,
    build_key_delete_kb,
    build_key_edit_kb,
    build_reissue_menu_kb,
    build_user_delete_kb,
    build_users_key_expiry_kb,
    build_users_key_show_kb,
)
from ..users_states import RenewTariffState, UserEditorState


MOSCOW_TZ = pytz.timezone("Europe/Moscow")

router = Router()


async def resolve_callback_key(
    session: AsyncSession,
    tg_id: int,
    key_ref: str | int | None,
) -> Key | None:
    return await resolve_admin_key(session, tg_id, key_ref)
