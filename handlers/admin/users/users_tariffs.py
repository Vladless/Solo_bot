from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.tariffs_config import normalize_tariff_config
from database import (
    get_active_tariffs_by_group_code,
    get_key_by_email,
    get_tariff_by_id,
    get_tariff_group_codes,
    reset_key_tariff_state,
    save_key_tariff_selection,
)
from database.models import Tariff
from filters.admin import IsAdminFilter
from handlers.buttons import BACK
from logger import logger
from middlewares.session import release_session_early
from services.operations import renew_key_in_cluster
from services.users_utils import resolve_admin_key

from .keyboard import AdminUserEditorCallback, build_editor_kb
from .users_keys import handle_key_edit
from .users_states import RenewTariffState


router = Router()


@router.callback_query(F.data == "back:renew", IsAdminFilter())
async def handle_back_to_key_menu(
    callback_query: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
):
    data = await state.get_data()
    email = data.get("email")
    tg_id = data.get("tg_id")
    await state.clear()

    if not email or not tg_id:
        await callback_query.message.edit_text("❌ Не найдены данные сессии.")
        return

    callback_data = AdminUserEditorCallback(action="users_key_edit", data=email, tg_id=tg_id)

    await handle_key_edit(
        callback_query=callback_query,
        callback_data=callback_data,
        session=session,
        update=False,
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_renew"),
    IsAdminFilter(),
)
async def handle_user_choose_tariff_group(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
    state: FSMContext,
):
    tg_id = callback_data.tg_id
    key_obj = await resolve_admin_key(session, tg_id, callback_data.data)
    if not key_obj:
        await callback_query.message.edit_text("❌ Ключ не найден.", reply_markup=build_editor_kb(tg_id))
        return
    email = key_obj.email

    await state.set_state(RenewTariffState.selecting_group)
    await state.update_data(email=email, tg_id=tg_id)

    groups = await get_tariff_group_codes(session)

    builder = InlineKeyboardBuilder()
    for group_code in groups:
        builder.button(text=group_code, callback_data=f"group:{group_code}")
    builder.button(text=BACK, callback_data="back:renew")
    builder.adjust(1)

    await callback_query.message.edit_text(
        text="📁 <b>Выберите тарифную группу:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("group:"), IsAdminFilter())
async def handle_user_choose_tariff(
    callback_query: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
):
    group_code = callback_query.data.split(":", 1)[1]
    await state.update_data(group_code=group_code)
    await state.set_state(RenewTariffState.selecting_tariff)

    tariffs = await get_active_tariffs_by_group_code(session, group_code)

    if not tariffs:
        await callback_query.message.edit_text("❌ Нет активных тарифов в группе.")
        return

    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        builder.button(text=f"{tariff.name} – {int(tariff.price_rub)}₽", callback_data=f"confirm:{tariff.id}")
    builder.button(text=BACK, callback_data="back:group")
    builder.adjust(1)

    await callback_query.message.edit_text(
        text=f"📦 <b>Выберите тариф для группы <code>{group_code}</code>:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("confirm:"), IsAdminFilter())
async def handle_user_renew_confirm(
    callback_query: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
):
    tariff_id = int(callback_query.data.split(":")[1])
    data = await state.get_data()
    email = data.get("email")
    tg_id = data.get("tg_id")

    if not email or not tg_id:
        await callback_query.message.edit_text("❌ Не найдены данные сессии.")
        await state.clear()
        return

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await callback_query.message.edit_text("❌ Тариф не найден.")
        await state.clear()
        return

    key_obj = await get_key_by_email(session, email, tg_id)
    if not key_obj:
        await callback_query.message.edit_text("❌ Ключ не найден.")
        await state.clear()
        return

    if tariff.get("configurable"):
        raw_device_options = tariff.get("device_options")
        raw_traffic_options = tariff.get("traffic_options_gb")

        raw_device_options = raw_device_options if isinstance(raw_device_options, list) else []
        raw_traffic_options = raw_traffic_options if isinstance(raw_traffic_options, list) else []

        try:
            device_options = sorted(raw_device_options, key=lambda v: (int(v) == 0, int(v)))
        except (TypeError, ValueError):
            device_options = raw_device_options

        try:
            traffic_options = sorted(raw_traffic_options, key=lambda v: (int(v) == 0, int(v)))
        except (TypeError, ValueError):
            traffic_options = raw_traffic_options

        device_int_options: list[int] = []
        for value in device_options:
            try:
                device_int_options.append(int(value))
            except (TypeError, ValueError):
                continue

        traffic_int_options: list[int] = []
        for value in traffic_options:
            try:
                traffic_int_options.append(int(value))
            except (TypeError, ValueError):
                continue

        if not device_int_options and not traffic_int_options:
            await callback_query.message.edit_text(
                "❌ Конфигуратор для этого тарифа не настроен. Попробуйте выбрать другой тариф."
            )
            await state.clear()
            return

        cfg = normalize_tariff_config(tariff)

        positive_device_values = [v for v in device_int_options if v > 0]
        positive_traffic_values = [v for v in traffic_int_options if v > 0]

        base_device_limit = cfg.get("base_device_limit")
        if base_device_limit is None:
            base_device_limit = tariff.get("device_limit")
        if base_device_limit is None:
            if positive_device_values:
                base_device_limit = min(positive_device_values)
            elif device_int_options:
                base_device_limit = device_int_options[0]
        base_device_limit = int(base_device_limit) if base_device_limit is not None else None

        base_traffic_gb = cfg.get("base_traffic_gb")
        if base_traffic_gb is None:
            traffic_limit_raw = tariff.get("traffic_limit")
            if traffic_limit_raw is not None:
                try:
                    base_traffic_gb = int(traffic_limit_raw)
                except (TypeError, ValueError):
                    base_traffic_gb = None
        if base_traffic_gb is None:
            if positive_traffic_values:
                base_traffic_gb = min(positive_traffic_values)
            elif traffic_int_options:
                base_traffic_gb = traffic_int_options[0]
        base_traffic_gb = int(base_traffic_gb) if base_traffic_gb is not None else None

        selected_devices = (
            base_device_limit
            if base_device_limit is not None
            else (device_int_options[0] if device_int_options else None)
        )
        selected_traffic_gb = (
            base_traffic_gb
            if base_traffic_gb is not None
            else (traffic_int_options[0] if traffic_int_options else None)
        )

        await state.update_data(
            renew_tariff_id=tariff_id,
            renew_selected_device_limit=selected_devices,
            renew_selected_traffic_gb=selected_traffic_gb,
            renew_mode="renew",
        )

        builder = InlineKeyboardBuilder()

        device_buttons: list[InlineKeyboardButton] = []
        traffic_buttons: list[InlineKeyboardButton] = []

        if device_int_options and len(device_int_options) > 1:
            sel = int(selected_devices or 0)
            for value in device_int_options:
                mark = " ✅" if value == sel else ""
                caption = "Безлимит устройств" if value == 0 else f"{value} устройств"
                device_buttons.append(
                    InlineKeyboardButton(
                        text=f"{caption}{mark}",
                        callback_data=f"cfg_renew_devices|{tariff_id}|{value}",
                    )
                )

        if traffic_int_options and len(traffic_int_options) > 1:
            sel = int(selected_traffic_gb or 0)
            for value in traffic_int_options:
                mark = " ✅" if value == sel else ""
                caption = "Безлимит трафика" if value == 0 else f"{value} ГБ"
                traffic_buttons.append(
                    InlineKeyboardButton(
                        text=f"{caption}{mark}",
                        callback_data=f"cfg_renew_traffic|{tariff_id}|{value}",
                    )
                )

        if device_buttons and traffic_buttons:
            max_len = max(len(device_buttons), len(traffic_buttons))
            for i in range(max_len):
                row = []
                if i < len(device_buttons):
                    row.append(device_buttons[i])
                if i < len(traffic_buttons):
                    row.append(traffic_buttons[i])
                builder.row(*row)
        elif device_buttons:
            for b in device_buttons:
                builder.row(b)
        elif traffic_buttons:
            for b in traffic_buttons:
                builder.row(b)

        builder.row(InlineKeyboardButton(text="✅ Применить", callback_data=f"cfg_renew_apply|{tariff_id}"))
        builder.row(InlineKeyboardButton(text=BACK, callback_data="back:group"))

        devices_label = (
            "Безлимит устройств"
            if (selected_devices is not None and int(selected_devices) <= 0)
            else (f"{int(selected_devices)} устройств" if selected_devices is not None else "—")
        )
        traffic_label = (
            "Безлимит трафика"
            if (selected_traffic_gb is not None and int(selected_traffic_gb) <= 0)
            else (f"{int(selected_traffic_gb)} ГБ" if selected_traffic_gb is not None else "—")
        )

        await callback_query.message.edit_text(
            text=(
                "🧩 <b>Выбор конфигурации тарифа</b>\n\n"
                f"📦 <b>Тариф:</b> {tariff.get('name', '—')}\n"
                f"📱 <b>Устройства:</b> {devices_label}\n"
                f"📊 <b>Трафик:</b> {traffic_label}\n\n"
                "Выберите параметры и нажмите «✅ Применить»."
            ),
            reply_markup=builder.as_markup(),
        )
        return

    device_limit = int(tariff.get("device_limit") or 0)

    raw_traffic_limit = tariff.get("traffic_limit")
    traffic_gb = 0
    if raw_traffic_limit is not None:
        try:
            traffic_gb = int(raw_traffic_limit)
        except (TypeError, ValueError):
            traffic_gb = 0

    old_tariff_id = key_obj.tariff_id
    old_subgroup = None
    if old_tariff_id:
        old_tariff = await get_tariff_by_id(session, old_tariff_id)
        old_subgroup = old_tariff.get("subgroup_title") if old_tariff else None

    new_tariff = await get_tariff_by_id(session, tariff_id)
    new_subgroup = new_tariff.get("subgroup_title") if new_tariff else None

    new_expiry_time = int(key_obj.expiry_time or 0) or int(datetime.utcnow().timestamp() * 1000)

    await reset_key_tariff_state(session, tg_id, email, tariff_id)
    await release_session_early(session)

    try:
        ok = await renew_key_in_cluster(
            cluster_id=key_obj.server_id,
            email=email,
            client_id=key_obj.client_id,
            new_expiry_time=new_expiry_time,
            total_gb=traffic_gb,
            session=session,
            hwid_device_limit=device_limit,
            reset_traffic=False,
            target_subgroup=new_subgroup,
            old_subgroup=old_subgroup,
            plan=tariff_id,
        )
    except Exception as e:
        logger.error(
            f"[AdminRenew] renew_key_in_cluster failed: tg_id={tg_id} email={email} tariff_id={tariff_id}: {e}"
        )
        ok = False

    await state.clear()

    if not ok:
        await callback_query.message.answer("❌ Не удалось обновить подписку на серверах (renew).")

    callback_data_back = AdminUserEditorCallback(action="users_key_edit", data=email, tg_id=tg_id)

    await handle_key_edit(
        callback_query=callback_query, callback_data=callback_data_back, session=session, update=False
    )


@router.callback_query(F.data.startswith("cfg_renew_devices|"), IsAdminFilter(), flags={"popup": True})
async def handle_cfg_renew_devices(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    _, tariff_id_str, value_str = callback_query.data.split("|", 2)
    tariff_id = int(tariff_id_str)
    value = int(value_str)

    data = await state.get_data()
    if int(data.get("renew_tariff_id") or 0) != tariff_id:
        await callback_query.answer("⚠️ Сессия устарела", show_alert=True)
        return

    await state.update_data(renew_selected_device_limit=value)

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await callback_query.message.edit_text("❌ Тариф не найден.")
        await state.clear()
        return

    selected_devices = int((await state.get_data()).get("renew_selected_device_limit") or 0)
    selected_traffic_gb = (await state.get_data()).get("renew_selected_traffic_gb")

    raw_device_options = tariff.get("device_options")
    raw_traffic_options = tariff.get("traffic_options_gb")

    raw_device_options = raw_device_options if isinstance(raw_device_options, list) else []
    raw_traffic_options = raw_traffic_options if isinstance(raw_traffic_options, list) else []

    try:
        device_options = sorted(raw_device_options, key=lambda v: (int(v) == 0, int(v)))
    except (TypeError, ValueError):
        device_options = raw_device_options

    try:
        traffic_options = sorted(raw_traffic_options, key=lambda v: (int(v) == 0, int(v)))
    except (TypeError, ValueError):
        traffic_options = raw_traffic_options

    device_int_options: list[int] = []
    for v in device_options:
        try:
            device_int_options.append(int(v))
        except (TypeError, ValueError):
            continue

    traffic_int_options: list[int] = []
    for v in traffic_options:
        try:
            traffic_int_options.append(int(v))
        except (TypeError, ValueError):
            continue

    builder = InlineKeyboardBuilder()

    device_buttons: list[InlineKeyboardButton] = []
    traffic_buttons: list[InlineKeyboardButton] = []

    if device_int_options and len(device_int_options) > 1:
        for v in device_int_options:
            mark = " ✅" if v == selected_devices else ""
            caption = "Безлимит устройств" if v == 0 else f"{v} устройств"
            device_buttons.append(
                InlineKeyboardButton(text=f"{caption}{mark}", callback_data=f"cfg_renew_devices|{tariff_id}|{v}")
            )

    if traffic_int_options and len(traffic_int_options) > 1:
        sel_tr = int(selected_traffic_gb or 0)
        for v in traffic_int_options:
            mark = " ✅" if v == sel_tr else ""
            caption = "Безлимит трафика" if v == 0 else f"{v} ГБ"
            traffic_buttons.append(
                InlineKeyboardButton(text=f"{caption}{mark}", callback_data=f"cfg_renew_traffic|{tariff_id}|{v}")
            )

    if device_buttons and traffic_buttons:
        max_len = max(len(device_buttons), len(traffic_buttons))
        for i in range(max_len):
            row = []
            if i < len(device_buttons):
                row.append(device_buttons[i])
            if i < len(traffic_buttons):
                row.append(traffic_buttons[i])
            builder.row(*row)
    elif device_buttons:
        for b in device_buttons:
            builder.row(b)
    elif traffic_buttons:
        for b in traffic_buttons:
            builder.row(b)

    builder.row(InlineKeyboardButton(text="✅ Применить", callback_data=f"cfg_renew_apply|{tariff_id}"))
    builder.row(InlineKeyboardButton(text=BACK, callback_data="back:group"))

    devices_label = "Безлимит устройств" if selected_devices <= 0 else f"{selected_devices} устройств"
    traffic_label = (
        "Безлимит трафика"
        if (selected_traffic_gb is not None and int(selected_traffic_gb) <= 0)
        else (f"{int(selected_traffic_gb)} ГБ" if selected_traffic_gb is not None else "—")
    )

    text = (
        "🧩 <b>Выбор конфигурации тарифа</b>\n\n"
        f"📦 <b>Тариф:</b> {tariff.get('name', '—')}\n"
        f"📱 <b>Устройства:</b> {devices_label}\n"
        f"📊 <b>Трафик:</b> {traffic_label}\n\n"
        "Выберите параметры и нажмите «✅ Применить»."
    )

    try:
        await callback_query.message.edit_text(text=text, reply_markup=builder.as_markup())
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

    await callback_query.answer()


@router.callback_query(F.data.startswith("cfg_renew_traffic|"), IsAdminFilter(), flags={"popup": True})
async def handle_cfg_renew_traffic(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    _, tariff_id_str, value_str = callback_query.data.split("|", 2)
    tariff_id = int(tariff_id_str)
    value = int(value_str)

    data = await state.get_data()
    if int(data.get("renew_tariff_id") or 0) != tariff_id:
        await callback_query.answer("⚠️ Сессия устарела", show_alert=True)
        return

    await state.update_data(renew_selected_traffic_gb=value)

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await callback_query.message.edit_text("❌ Тариф не найден.")
        await state.clear()
        return

    selected_devices = (await state.get_data()).get("renew_selected_device_limit")
    selected_traffic_gb = int((await state.get_data()).get("renew_selected_traffic_gb") or 0)

    raw_device_options = tariff.get("device_options")
    raw_traffic_options = tariff.get("traffic_options_gb")

    raw_device_options = raw_device_options if isinstance(raw_device_options, list) else []
    raw_traffic_options = raw_traffic_options if isinstance(raw_traffic_options, list) else []

    try:
        device_options = sorted(raw_device_options, key=lambda v: (int(v) == 0, int(v)))
    except (TypeError, ValueError):
        device_options = raw_device_options

    try:
        traffic_options = sorted(raw_traffic_options, key=lambda v: (int(v) == 0, int(v)))
    except (TypeError, ValueError):
        traffic_options = raw_traffic_options

    device_int_options: list[int] = []
    for v in device_options:
        try:
            device_int_options.append(int(v))
        except (TypeError, ValueError):
            continue

    traffic_int_options: list[int] = []
    for v in traffic_options:
        try:
            traffic_int_options.append(int(v))
        except (TypeError, ValueError):
            continue

    builder = InlineKeyboardBuilder()

    device_buttons: list[InlineKeyboardButton] = []
    traffic_buttons: list[InlineKeyboardButton] = []

    if device_int_options and len(device_int_options) > 1:
        sel_dev = int(selected_devices or 0)
        for v in device_int_options:
            mark = " ✅" if v == sel_dev else ""
            caption = "Безлимит устройств" if v == 0 else f"{v} устройств"
            device_buttons.append(
                InlineKeyboardButton(text=f"{caption}{mark}", callback_data=f"cfg_renew_devices|{tariff_id}|{v}")
            )

    if traffic_int_options and len(traffic_int_options) > 1:
        for v in traffic_int_options:
            mark = " ✅" if v == selected_traffic_gb else ""
            caption = "Безлимит трафика" if v == 0 else f"{v} ГБ"
            traffic_buttons.append(
                InlineKeyboardButton(text=f"{caption}{mark}", callback_data=f"cfg_renew_traffic|{tariff_id}|{v}")
            )

    if device_buttons and traffic_buttons:
        max_len = max(len(device_buttons), len(traffic_buttons))
        for i in range(max_len):
            row = []
            if i < len(device_buttons):
                row.append(device_buttons[i])
            if i < len(traffic_buttons):
                row.append(traffic_buttons[i])
            builder.row(*row)
    elif device_buttons:
        for b in device_buttons:
            builder.row(b)
    elif traffic_buttons:
        for b in traffic_buttons:
            builder.row(b)

    builder.row(InlineKeyboardButton(text="✅ Применить", callback_data=f"cfg_renew_apply|{tariff_id}"))
    builder.row(InlineKeyboardButton(text=BACK, callback_data="back:group"))

    devices_label = (
        "Безлимит устройств"
        if (selected_devices is not None and int(selected_devices) <= 0)
        else (f"{int(selected_devices)} устройств" if selected_devices is not None else "—")
    )
    traffic_label = "Безлимит трафика" if selected_traffic_gb <= 0 else f"{selected_traffic_gb} ГБ"

    text = (
        "🧩 <b>Выбор конфигурации тарифа</b>\n\n"
        f"📦 <b>Тариф:</b> {tariff.get('name', '—')}\n"
        f"📱 <b>Устройства:</b> {devices_label}\n"
        f"📊 <b>Трафик:</b> {traffic_label}\n\n"
        "Выберите параметры и нажмите «✅ Применить»."
    )

    try:
        await callback_query.message.edit_text(text=text, reply_markup=builder.as_markup())
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

    await callback_query.answer()


@router.callback_query(F.data.startswith("cfg_renew_apply|"), IsAdminFilter())
async def handle_cfg_renew_apply(callback_query: CallbackQuery, session: AsyncSession, state: FSMContext):
    _, tariff_id_str = callback_query.data.split("|", 1)
    tariff_id = int(tariff_id_str)

    data = await state.get_data()
    email = data.get("email")
    tg_id = data.get("tg_id")

    if not email or not tg_id:
        await callback_query.message.edit_text("❌ Не найдены данные сессии.")
        await state.clear()
        return

    if int(data.get("renew_tariff_id") or 0) != tariff_id:
        await callback_query.message.edit_text("❌ Сессия устарела. Выберите тариф заново.")
        await state.clear()
        return

    selected_devices = data.get("renew_selected_device_limit")
    selected_traffic_gb = data.get("renew_selected_traffic_gb")

    if selected_devices is None and selected_traffic_gb is None:
        await callback_query.message.edit_text("❌ Не выбраны параметры конфигурации.")
        await state.clear()
        return

    key_obj = await get_key_by_email(session, email, tg_id)
    if not key_obj:
        await callback_query.message.edit_text("❌ Ключ не найден.")
        await state.clear()
        return

    old_tariff_id = key_obj.tariff_id
    old_subgroup = None
    if old_tariff_id:
        old_tariff = await get_tariff_by_id(session, old_tariff_id)
        old_subgroup = old_tariff.get("subgroup_title") if old_tariff else None

    new_tariff = await get_tariff_by_id(session, tariff_id)
    new_subgroup = new_tariff.get("subgroup_title") if new_tariff else None

    new_expiry_time = int(key_obj.expiry_time or 0) or int(datetime.utcnow().timestamp() * 1000)

    await save_key_tariff_selection(session, tg_id, email, tariff_id, selected_devices, selected_traffic_gb)
    await release_session_early(session)

    try:
        ok = await renew_key_in_cluster(
            cluster_id=key_obj.server_id,
            email=email,
            client_id=key_obj.client_id,
            new_expiry_time=new_expiry_time,
            total_gb=int(selected_traffic_gb or 0),
            session=session,
            hwid_device_limit=int(selected_devices or 0),
            reset_traffic=False,
            target_subgroup=new_subgroup,
            old_subgroup=old_subgroup,
            plan=tariff_id,
        )
    except Exception as e:
        logger.error(
            f"[AdminRenewCfg] renew_key_in_cluster failed: tg_id={tg_id} email={email} tariff_id={tariff_id}: {e}"
        )
        ok = False

    await state.clear()

    if not ok:
        await callback_query.message.answer("❌ Не удалось обновить подписку на серверах (renew).")

    callback_data_back = AdminUserEditorCallback(action="users_key_edit", data=email, tg_id=int(tg_id))

    await handle_key_edit(
        callback_query=callback_query, callback_data=callback_data_back, session=session, update=False
    )


@router.callback_query(F.data == "back:group", IsAdminFilter())
async def handle_back_to_group(
    callback_query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    groups = await get_tariff_group_codes(session)

    builder = InlineKeyboardBuilder()
    for group_code in groups:
        builder.button(text=group_code, callback_data=f"group:{group_code}")
    builder.button(text=BACK, callback_data="back:renew")
    builder.adjust(1)

    await callback_query.message.edit_text(
        text="📁 <b>Выберите тарифную группу:</b>",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(RenewTariffState.selecting_group)
