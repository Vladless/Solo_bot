import asyncio
import html

from datetime import datetime, timezone

import pytz

from aiogram.types import CallbackQuery, InlineKeyboardButton, Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import BUTTONS_CONFIG, MODES_CONFIG
from database import get_key_details, get_keys, get_vless_enabled_batch
from database.models import Key
from handlers.keys.view.screens import (
    _format_device_block,
    build_key_view_text,
    build_single_subscription_text,
)
from handlers.keys.utils import build_key_callback, build_key_ref, key_owned_by_user, resolve_key
from handlers.menu_layout import KEY_MENU, arrange_menu, split_hook_buttons
from handlers.utils import (
    edit_or_send_message,
    fill_text,
    format_days,
    format_hours,
    format_minutes,
    get_russian_month,
    is_full_remnawave_cluster,
    render_screen,
    safe_answer_callback,
)
from hooks.hook_buttons import insert_hook_buttons
from hooks.processors import (
    process_remnawave_webapp_override,
    process_view_key_menu,
)
from logger import logger
from panels.remnawave_runtime import (
    get_remnawave_profile,
    with_remnawave_api,
)
from services.tariffs.tariff_display import GB, get_key_tariff_addons_state, get_key_tariff_display
from settings.buttons import (
    ADDONS_BUTTON_DEVICES,
    ADDONS_BUTTON_DEVICES_TRAFFIC,
    ADDONS_BUTTON_TRAFFIC,
    ALIAS,
    BACK,
    CHANGE_LOCATION,
    CONNECT_DEVICE,
    DELETE,
    MAIN_MENU,
    MY_DEVICES,
    QR,
    RENEW_KEY,
    ROUTER_BUTTON,
    TV_BUTTON,
    UNBIND_DEVICE,
)
from settings.config import (
    ENABLE_DELETE_KEY_BUTTON,
    HAPP_CRYPTOLINK,
    HWID_RESET_BUTTON,
    QRCODE,
    REMNAWAVE_WEBAPP,
    REMNAWAVE_WEBAPP_OPEN_IN_BROWSER,
    USE_COUNTRY_SELECTION,
)
from settings.texts import (
    DAYS_LEFT_MESSAGE,
    FROZEN_SUBSCRIPTION_MSG,
    KEYS_FOOTER,
    KEYS_LIST_ROW,
    KEYS_LIST_TEXT,
    MY_DEVICES_EMPTY_TEXT,
    MY_DEVICES_HINT,
    MY_DEVICES_TEXT,
    NO_SUBSCRIPTIONS_MSG,
)


moscow_tz = pytz.timezone("Europe/Moscow")

DEVICES_PER_PAGE = 3


async def build_keys_response(records: list[Key] | None, session: AsyncSession, page: int = 0):
    builder = InlineKeyboardBuilder()

    page_size = 5
    records = sorted(
        records or [],
        key=lambda r: r.created_at or 0,
    )

    total = len(records)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))

    if total:
        key_rows: list[str] = []

        start = page * page_size
        end = start + page_size
        page_records = records[start:end]

        tariff_ids = []
        for record in page_records:
            tid = getattr(record, "tariff_id", None)
            if tid is not None:
                try:
                    tariff_ids.append(int(tid))
                except (TypeError, ValueError):
                    pass
        vless_by_tariff = await get_vless_enabled_batch(session, tariff_ids) if tariff_ids else {}

        for record in page_records:
            alias = record.alias
            email = record.email
            client_id = record.client_id
            expiry_time = record.expiry_time

            key_display = html.escape(alias.strip() if alias else email)

            if expiry_time:
                expiry_date_full = datetime.fromtimestamp(expiry_time / 1000, tz=moscow_tz)
                formatted_date_full = expiry_date_full.strftime("до %d.%m.%y")
            else:
                formatted_date_full = "без срока"

            tid = getattr(record, "tariff_id", None)
            is_vless = vless_by_tariff.get(int(tid), False) if tid is not None else False

            icon = "📶" if is_vless else "🔑"

            key_button = InlineKeyboardButton(
                text=f"{icon} {key_display}",
                callback_data=build_key_callback("view_key", client_id, email),
            )
            rename_button = InlineKeyboardButton(
                text=ALIAS,
                callback_data=f"rename_key|{client_id}",
            )
            builder.row(key_button, rename_button)

            key_rows.append(KEYS_LIST_ROW.format(name=key_display, expiry=formatted_date_full))

        response_message = render_screen(fill_text(KEYS_LIST_TEXT, table="\n".join(key_rows)), KEYS_FOOTER)

        if total_pages > 1:
            nav_row = []

            if page > 0:
                nav_row.append(
                    InlineKeyboardButton(
                        text="⬅️ Пред.",
                        callback_data=f"view_keys|{page - 1}",
                    )
                )

            nav_row.append(
                InlineKeyboardButton(
                    text=f"({page + 1}/{total_pages})",
                    callback_data=" ",
                )
            )

            if page < total_pages - 1:
                nav_row.append(
                    InlineKeyboardButton(
                        text="След. ➡️",
                        callback_data=f"view_keys|{page + 1}",
                    )
                )

            builder.row(*nav_row)
    else:
        response_message = NO_SUBSCRIPTIONS_MSG

    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    return builder.as_markup(), response_message


async def build_key_view_payload(session: AsyncSession, tg_id: int, key_ref_or_email: str):
    key_obj = await resolve_key(session, tg_id, key_ref_or_email)
    key_name = key_obj.email if key_obj else key_ref_or_email
    record = await get_key_details(session, key_name)
    if not record:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        return "<b>Информация о подписке не найдена.</b>", builder.as_markup(), False

    db_key = key_obj

    is_frozen = record["is_frozen"]
    client_id = record.get("client_id")
    final_link = record.get("link")

    builder = InlineKeyboardBuilder()

    if is_frozen:
        builder.row(InlineKeyboardButton(text=BACK, callback_data="view_keys"))
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        return FROZEN_SUBSCRIPTION_MSG, builder.as_markup(), True

    expiry_time = record["expiry_time"]
    server_name = record["server_id"]
    expiry_date = datetime.fromtimestamp(expiry_time / 1000, tz=timezone.utc)
    now = datetime.now(timezone.utc)
    time_left = expiry_date - now

    if time_left.total_seconds() <= 0:
        days_left_message = DAYS_LEFT_MESSAGE
    else:
        total_seconds = int(time_left.total_seconds())
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        days_left_message = (
            f"⏳ Осталось: <b>{format_days(days)}</b>, <b>{format_hours(hours)}</b>, <b>{format_minutes(minutes)}</b>"
        )

    formatted_expiry_date = (
        f"{expiry_date.strftime('%d')} {get_russian_month(expiry_date)} {expiry_date.strftime('%Y')} года"
    )

    is_full_task = asyncio.create_task(is_full_remnawave_cluster(server_name, session))

    tariff_name = ""
    subgroup_title = ""
    traffic_limit_gb = 0
    device_limit = 0
    vless_enabled = False
    is_tariff_configurable = False
    addons_devices_enabled = False
    addons_traffic_enabled = False

    if record.get("tariff_id"):
        (
            tariff_name,
            _subgroup_title,
            traffic_limit_gb,
            device_limit,
            vless_enabled,
            is_tariff_configurable,
            addons_devices_enabled,
            addons_traffic_enabled,
        ) = await get_key_tariff_addons_state(
            session=session,
            key_record=record,
            db_key=db_key,
        )

    is_full_remnawave = await is_full_task

    hwid_count = 0
    remna_used_gb = None
    if is_full_remnawave and client_id:
        profile = await get_remnawave_profile(
            session, str(server_name), client_id,
            username=str(record.get("email") or "") or None,
        )
        if profile:
            hwid_count = int(profile.get("hwid_count") or 0)
            remna_used_gb = profile.get("used_gb")
            traffic_limit_bytes_actual = profile.get("traffic_limit_bytes")
            if traffic_limit_bytes_actual is not None:
                try:
                    traffic_limit_bytes_actual = int(traffic_limit_bytes_actual)
                    traffic_limit_gb = int(traffic_limit_bytes_actual / GB) if traffic_limit_bytes_actual > 0 else 0
                except (TypeError, ValueError):
                    pass
            hwid_device_limit_actual = profile.get("hwid_device_limit")
            if hwid_device_limit_actual is not None:
                try:
                    device_limit = int(hwid_device_limit_actual)
                except (TypeError, ValueError):
                    pass

    country_selection_enabled = bool(MODES_CONFIG.get("COUNTRY_SELECTION_ENABLED", USE_COUNTRY_SELECTION))
    remnawave_webapp_enabled = bool(MODES_CONFIG.get("REMNAWAVE_WEBAPP_ENABLED", REMNAWAVE_WEBAPP))
    open_in_browser = bool(MODES_CONFIG.get("REMNAWAVE_WEBAPP_OPEN_IN_BROWSER", REMNAWAVE_WEBAPP_OPEN_IN_BROWSER))
    happ_cryptolink_enabled = bool(MODES_CONFIG.get("HAPP_CRYPTOLINK_ENABLED", HAPP_CRYPTOLINK))

    response_message = build_key_view_text(
        key=final_link,
        formatted_expiry_date=formatted_expiry_date,
        days_left_message=days_left_message,
        country=server_name if country_selection_enabled else None,
        hwid_count=hwid_count if device_limit is not None else 0,
        tariff_name=tariff_name,
        traffic_limit=traffic_limit_gb,
        device_limit=device_limit,
        is_remnawave=is_full_remnawave,
        remna_used_gb=remna_used_gb,
    )

    use_webapp = remnawave_webapp_enabled
    if is_full_remnawave and final_link and remnawave_webapp_enabled and not happ_cryptolink_enabled:
        use_webapp = await process_remnawave_webapp_override(
            remnawave_webapp=remnawave_webapp_enabled,
            final_link=final_link,
            session=session,
        )

    tv_button_enabled = bool(BUTTONS_CONFIG.get("ANDROID_TV_BUTTON_ENABLE"))

    tv_button = None
    if is_full_remnawave and final_link and use_webapp and not happ_cryptolink_enabled:
        if vless_enabled:
            connect_button = InlineKeyboardButton(
                text=ROUTER_BUTTON,
                callback_data=build_key_callback("connect_router", client_id, key_name),
            )
        else:
            connect_button = (
                InlineKeyboardButton(text=CONNECT_DEVICE, url=final_link)
                if open_in_browser
                else InlineKeyboardButton(text=CONNECT_DEVICE, web_app=WebAppInfo(url=final_link))
            )
            if tv_button_enabled:
                tv_button = InlineKeyboardButton(
                    text=TV_BUTTON,
                    callback_data=build_key_callback("connect_tv", client_id, key_name),
                )
    elif vless_enabled:
        connect_button = InlineKeyboardButton(
            text=ROUTER_BUTTON,
            callback_data=build_key_callback("connect_router", client_id, key_name),
        )
    else:
        connect_button = InlineKeyboardButton(
            text=CONNECT_DEVICE,
            callback_data=build_key_callback("connect_device", client_id, key_name),
        )

    addons_button = None
    if is_tariff_configurable and (addons_devices_enabled or addons_traffic_enabled):
        if addons_devices_enabled and addons_traffic_enabled:
            addons_text = ADDONS_BUTTON_DEVICES_TRAFFIC
        elif addons_devices_enabled:
            addons_text = ADDONS_BUTTON_DEVICES
        else:
            addons_text = ADDONS_BUTTON_TRAFFIC
        addons_button = InlineKeyboardButton(
            text=addons_text, callback_data=build_key_callback("key_addons", client_id, key_name)
        )

    hwid_reset_enabled = bool(BUTTONS_CONFIG.get("HWID_RESET_BUTTON_ENABLE", HWID_RESET_BUTTON))
    qrcode_enabled = bool(BUTTONS_CONFIG.get("QRCODE_BUTTON_ENABLE", QRCODE))
    delete_key_enabled = bool(BUTTONS_CONFIG.get("DELETE_KEY_BUTTON_ENABLE", ENABLE_DELETE_KEY_BUTTON))

    hook_buttons = await process_view_key_menu(key_name=key_name, session=session)
    module_buttons, module_directives = split_hook_buttons(hook_buttons)

    menu_buttons = {
        "connect": connect_button,
        "tv": tv_button,
        "renew": InlineKeyboardButton(
            text=RENEW_KEY, callback_data=build_key_callback("renew_key", client_id, key_name)
        ),
        "addons": addons_button,
        "devices": InlineKeyboardButton(
            text=MY_DEVICES,
            callback_data=build_key_callback("my_devices", client_id, key_name) + "|0",
        )
        if hwid_reset_enabled and hwid_count > 0
        else None,
        "qr": InlineKeyboardButton(text=QR, callback_data=build_key_callback("show_qr", client_id, key_name))
        if qrcode_enabled
        else None,
        "delete": InlineKeyboardButton(
            text=DELETE, callback_data=build_key_callback("delete_key", client_id, key_name)
        )
        if delete_key_enabled
        else None,
        "location": InlineKeyboardButton(
            text=CHANGE_LOCATION, callback_data=build_key_callback("change_location", client_id, key_name)
        )
        if country_selection_enabled
        else None,
        "main_menu": InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"),
        "modules": [[button] for button in module_buttons],
    }

    for row in arrange_menu(KEY_MENU, menu_buttons):
        builder.row(*row)

    builder = insert_hook_buttons(builder, module_directives)

    return response_message, builder.as_markup(), False


async def build_key_view_message(session: AsyncSession, email: str):
    text, reply_markup, _ = await build_key_view_payload(session, 0, email)
    return text, reply_markup


async def render_key_info(message: Message, session: AsyncSession, key_ref_or_email: str, image_path: str):
    text, reply_markup, _ = await build_key_view_payload(session, message.chat.id, key_ref_or_email)
    await edit_or_send_message(
        target_message=message,
        text=text,
        reply_markup=reply_markup,
        media_path=image_path,
    )


async def _build_single_subscription_text(
    session: AsyncSession,
    tg_id: int,
    key,
    username: str,
    balance_text: str,
) -> str:
    tariff_name = ""
    subgroup_title = ""
    traffic_limit = 0
    device_limit = 0
    base_device_limit = 0
    used_traffic_gb = None
    hwid_count = 0

    key_record = await get_key_details(session, key.email)

    if getattr(key, "tariff_id", None) and key_record:
        try:
            tariff_name, _subgroup_title, traffic_limit, device_limit, _, tariff = await get_key_tariff_display(
                session=session,
                key_record=key_record,
            )
            current_device_limit = key_record.get("current_device_limit")
            if current_device_limit:
                try:
                    current_device_limit = int(current_device_limit)
                    if current_device_limit > device_limit:
                        device_limit = current_device_limit
                except (TypeError, ValueError):
                    pass
            if tariff:
                tariff_default_device_limit = int(tariff.get("device_limit") or 0)
                selected = key_record.get("selected_device_limit")
                if selected is not None:
                    try:
                        base_device_limit = int(selected)
                    except (TypeError, ValueError):
                        base_device_limit = tariff_default_device_limit
                else:
                    base_device_limit = tariff_default_device_limit
        except Exception as e:
            logger.error(f"[single_sub] Ошибка тарифа для {key.email}: {e}")

    hwid_reset_enabled = bool(BUTTONS_CONFIG.get("HWID_RESET_BUTTON_ENABLE", HWID_RESET_BUTTON))
    if getattr(key, "client_id", None):
        try:
            if await is_full_remnawave_cluster(key.server_id, session):
                profile = await get_remnawave_profile(
                    session, str(key.server_id), key.client_id, username=key.email
                )
                if isinstance(profile, dict):
                    hwid_count = int(profile.get("hwid_count") or 0)
                    used_traffic_gb = profile.get("used_gb")
        except Exception as e:
            logger.error(f"[single_sub] Ошибка профиля Remnawave для {key.email}: {e}")

    expiry_date = "Неизвестно"
    is_expired = False
    if getattr(key, "expiry_time", None):
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        is_expired = key.expiry_time <= now_ms
        if is_expired:
            expiry_date = "❌ Подписка истекла"
        else:
            exp = datetime.fromtimestamp(key.expiry_time / 1000, tz=moscow_tz)
            expiry_date = exp.strftime(f"%d {get_russian_month(exp)} %Y года, %H:%M (МСК)")

    key_link = (
        getattr(key, "key", None)
        or getattr(key, "remnawave_link", None)
        or (key_record.get("link") if key_record else None)
        or "Неизвестно"
    )

    return build_single_subscription_text(
        username=username,
        tg_id=tg_id,
        balance=balance_text,
        key=key_link,
        tariff_name=tariff_name,
        traffic_limit=traffic_limit,
        used_traffic_gb=used_traffic_gb,
        device_limit=device_limit,
        base_device_limit=base_device_limit,
        hwid_count=hwid_count if hwid_reset_enabled else 0,
        expiry_date=expiry_date,
        is_expired=is_expired,
    )


async def build_single_subscription_profile(session: AsyncSession, tg_id: int, username: str, balance_text: str):
    records = await get_keys(session, tg_id)
    if len(records) != 1:
        return None

    key = records[0]
    key_ref = build_key_ref(key.client_id, key.email)
    _, markup, _ = await build_key_view_payload(session, tg_id, key_ref)

    rows: list[list[InlineKeyboardButton]] = []
    for row in markup.inline_keyboard:
        filtered = [btn for btn in row if getattr(btn, "callback_data", None) not in ("profile", "view_keys")]
        if filtered:
            rows.append(filtered)

    text = await _build_single_subscription_text(session, tg_id, key, username, balance_text)
    return text, rows


def _build_devices_keyboard(
    key_ref: str,
    page: int,
    total_pages: int,
    devices_on_page: int,
) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for idx in range(devices_on_page):
        builder.row(
            InlineKeyboardButton(
                text=f"{UNBIND_DEVICE} #{page * DEVICES_PER_PAGE + idx + 1}",
                callback_data=f"unbind_dev|{key_ref}|{page}|{idx}",
            )
        )
    nav_buttons: list[InlineKeyboardButton] = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"my_devices|{key_ref}|{page - 1}"))
    if total_pages > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data=f"my_devices|{key_ref}|{page}",
            )
        )
    if page + 1 < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"my_devices|{key_ref}|{page + 1}"))
    if nav_buttons:
        builder.row(*nav_buttons)
    back_cb = "profile" if bool(MODES_CONFIG.get("SINGLE_SUBSCRIPTION_MODE", False)) else f"view_key|{key_ref}"
    builder.row(InlineKeyboardButton(text=BACK, callback_data=back_cb))
    return builder


async def _render_my_devices(
    callback_query: CallbackQuery,
    session: AsyncSession,
    key_ref: str,
    page: int,
    *,
    notice: str | None = None,
) -> None:
    key_obj = await resolve_key(session, callback_query.from_user.id, key_ref)
    key_name = key_obj.email if key_obj else key_ref
    record = await get_key_details(session, key_name)
    if not record or not key_owned_by_user(record, callback_query.from_user.id):
        await safe_answer_callback(callback_query, "❌ Ключ не найден.", show_alert=True)
        return

    client_id = record.get("client_id")
    if not client_id:
        await safe_answer_callback(callback_query, "❌ У ключа отсутствует client_id.", show_alert=True)
        return

    server_id = str(record.get("server_id") or "")
    key_email = str(record.get("email") or "") or None

    async def _fetch(api):
        return await api.get_user_hwid_devices(client_id, username=key_email)

    devices = await with_remnawave_api(session, server_id, _fetch, fallback_any=True, timeout_sec=10.0)
    if devices is None:
        devices = []

    total = len(devices)
    if total == 0:
        text = MY_DEVICES_EMPTY_TEXT
        builder = InlineKeyboardBuilder()
        empty_back_cb = (
            "profile" if bool(MODES_CONFIG.get("SINGLE_SUBSCRIPTION_MODE", False)) else f"view_key|{key_ref}"
        )
        builder.row(InlineKeyboardButton(text=BACK, callback_data=empty_back_cb))
        await edit_or_send_message(
            target_message=callback_query.message,
            text=text,
            reply_markup=builder.as_markup(),
            media_path=None,
        )
        return

    total_pages = (total + DEVICES_PER_PAGE - 1) // DEVICES_PER_PAGE
    page = max(0, min(page, total_pages - 1))
    start = page * DEVICES_PER_PAGE
    page_devices = devices[start : start + DEVICES_PER_PAGE]

    header = fill_text(MY_DEVICES_TEXT, total=total, page=page + 1, pages=total_pages)

    blocks = [header, notice or ""]
    blocks += [_format_device_block(start + i + 1, dev) for i, dev in enumerate(page_devices)]
    text = render_screen(*blocks, MY_DEVICES_HINT)

    builder = _build_devices_keyboard(key_ref, page, total_pages, len(page_devices))
    await edit_or_send_message(
        target_message=callback_query.message,
        text=text,
        reply_markup=builder.as_markup(),
        media_path=None,
    )
