import asyncio
import time
import uuid

from datetime import datetime, timedelta, timezone
from typing import Any

import pytz

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import USE_COUNTRY_SELECTION
from database import (
    delete_key,
    delete_user_data,
    get_key_details,
    get_servers,
    get_tariff_by_id,
    get_tariffs_for_cluster,
    mark_key_as_frozen,
    mark_key_as_unfrozen,
    update_key_expiry,
)
from database.models import Key, Server, Tariff
from filters.admin import IsAdminFilter
from handlers.keys.operations import (
    create_key_on_cluster,
    delete_key_from_cluster,
    get_user_traffic,
    renew_key_in_cluster,
    reset_traffic_in_cluster,
    toggle_client_on_cluster,
    update_subscription,
)
from handlers.utils import generate_random_email, handle_error
from logger import logger

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn, build_admin_back_kb
from .keyboard import (
    AdminUserEditorCallback,
    AdminUserKeyEditorCallback,
    build_cluster_selection_kb,
    build_editor_kb,
    build_key_delete_kb,
    build_key_edit_kb,
    build_user_delete_kb,
    build_users_key_expiry_kb,
    build_users_key_show_kb,
)
from .users_states import RenewTariffState, UserEditorState


MOSCOW_TZ = pytz.timezone("Europe/Moscow")

router = Router()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_key_edit"),
    IsAdminFilter(),
)
async def handle_key_edit(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback | AdminUserKeyEditorCallback,
    session: AsyncSession,
    update: bool = False,
):
    email = callback_data.data
    result = await session.execute(select(Key).where(Key.email == email))
    key_obj: Key | None = result.scalar_one_or_none()

    if not key_obj:
        await callback_query.message.edit_text(
            text="🚫 Информация о подписке не найдена.",
            reply_markup=build_editor_kb(callback_data.tg_id),
        )
        return

    key_value = key_obj.key or key_obj.remnawave_link or "—"
    alias_part = f" (<i>{key_obj.alias}</i>)" if key_obj.alias else ""

    if key_obj.created_at:
        created_at_dt = datetime.fromtimestamp(int(key_obj.created_at) / 1000) + timedelta(hours=3)
        created_at = created_at_dt.strftime("%d %B %Y года %H:%M")
    else:
        created_at = "—"

    if key_obj.expiry_time:
        expiry_dt = datetime.fromtimestamp(int(key_obj.expiry_time) / 1000)
        expiry_date = expiry_dt.strftime("%d %B %Y года %H:%M")
    else:
        expiry_date = "—"

    tariff_name = "—"
    subgroup_title = "—"
    if key_obj.tariff_id:
        result = await session.execute(select(Tariff.name, Tariff.subgroup_title).where(Tariff.id == key_obj.tariff_id))
        row = result.first()
        if row:
            tariff_name = row[0]
            subgroup_title = row[1] or "—"

    text = (
        "<b>🔑 Информация о подписке</b>\n\n"
        "<blockquote>"
        f"🔗 <b>Ключ{alias_part}:</b> <code>{key_value}</code>\n"
        f"📆 <b>Создан:</b> {created_at} (МСК)\n"
        f"⏰ <b>Истекает:</b> {expiry_date} (МСК)\n"
        f"🌐 <b>Кластер:</b> {key_obj.server_id or '—'}\n"
        f"🆔 <b>ID клиента:</b> {key_obj.tg_id or '—'}\n"
        f"📁 <b>Группа:</b> {subgroup_title}\n"
        f"📦 <b>Тариф:</b> {tariff_name}\n"
        "</blockquote>"
    )

    if not update or not getattr(callback_data, "edit", False):
        await callback_query.message.edit_text(
            text=text,
            reply_markup=build_key_edit_kb(key_obj.__dict__, email),
        )
    else:
        await callback_query.message.edit_text(
            text=text,
            reply_markup=await build_users_key_expiry_kb(session, callback_data.tg_id, email),
        )


@router.callback_query(F.data == "back:renew", IsAdminFilter())
async def handle_back_to_key_menu(
    callback_query: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
):
    data = await state.get_data()
    email = data["email"]
    tg_id = data["tg_id"]
    await state.clear()

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
    email = callback_data.data
    tg_id = callback_data.tg_id

    await state.set_state(RenewTariffState.selecting_group)
    await state.update_data(email=email, tg_id=tg_id)

    result = await session.execute(select(Tariff.group_code).distinct())
    groups = [row[0] for row in result.fetchall()]

    builder = InlineKeyboardBuilder()
    for group_code in groups:
        builder.button(text=group_code, callback_data=f"group:{group_code}")
    builder.button(text="🔙 Назад", callback_data="back:renew")
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

    result = await session.execute(
        select(Tariff).where(Tariff.group_code == group_code, Tariff.is_active.is_(True)).order_by(Tariff.id)
    )
    tariffs = result.scalars().all()

    if not tariffs:
        await callback_query.message.edit_text("❌ Нет активных тарифов в группе.")
        return

    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        builder.button(text=f"{tariff.name} – {int(tariff.price_rub)}₽", callback_data=f"confirm:{tariff.id}")
    builder.button(text="🔙 Назад", callback_data="back:group")
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
    email = data["email"]
    tg_id = data["tg_id"]

    stmt = update(Key).where(Key.tg_id == tg_id, Key.email == email).values(tariff_id=tariff_id)
    await session.execute(stmt)
    await session.commit()

    await update_subscription(tg_id=tg_id, email=email, session=session)

    await state.clear()

    callback_data = AdminUserEditorCallback(action="users_key_edit", data=email, tg_id=tg_id)
    await handle_key_edit(
        callback_query=callback_query,
        callback_data=callback_data,
        session=session,
        update=False,
    )


@router.callback_query(F.data == "back:group", IsAdminFilter())
async def handle_back_to_group(
    callback_query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    await state.get_data()

    result = await session.execute(select(Tariff.group_code).distinct())
    groups = [row[0] for row in result.fetchall()]

    builder = InlineKeyboardBuilder()
    for group_code in groups:
        builder.button(text=group_code, callback_data=f"group:{group_code}")
    builder.button(text="🔙 Назад", callback_data="back:renew")
    builder.adjust(1)

    await callback_query.message.edit_text(
        text="📁 <b>Выберите тарифную группу:</b>",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(RenewTariffState.selecting_group)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_expiry_edit"),
    IsAdminFilter(),
)
async def handle_change_expiry(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    email = callback_data.data

    await callback_query.message.edit_reply_markup(reply_markup=await build_users_key_expiry_kb(session, tg_id, email))


@router.callback_query(
    AdminUserKeyEditorCallback.filter(F.action == "add"),
    IsAdminFilter(),
)
async def handle_expiry_add(
    callback_query: CallbackQuery,
    callback_data: AdminUserKeyEditorCallback,
    state: FSMContext,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    email = callback_data.data
    days = callback_data.month

    key_details = await get_key_details(session, email)

    if not key_details:
        await callback_query.message.edit_text(
            text="🚫 Информация о ключе не найдена.",
            reply_markup=build_editor_kb(tg_id),
        )
        return

    if days:
        await change_expiry_time(key_details["expiry_time"] + days * 24 * 3600 * 1000, email, session)
        await handle_key_edit(callback_query, callback_data, session, True)
        return

    await state.update_data(tg_id=tg_id, email=email, op_type="add")
    await state.set_state(UserEditorState.waiting_for_expiry_time)

    await callback_query.message.edit_text(
        text="✍️ Введите количество дней, которое хотите добавить к времени действия ключа:",
        reply_markup=build_users_key_show_kb(tg_id, email),
    )


@router.callback_query(
    AdminUserKeyEditorCallback.filter(F.action == "take"),
    IsAdminFilter(),
)
async def handle_expiry_take(
    callback_query: CallbackQuery,
    callback_data: AdminUserKeyEditorCallback,
    state: FSMContext,
):
    tg_id = callback_data.tg_id
    email = callback_data.data

    await state.update_data(tg_id=tg_id, email=email, op_type="take")
    await state.set_state(UserEditorState.waiting_for_expiry_time)

    await callback_query.message.edit_text(
        text="✍️ Введите количество дней, которое хотите вычесть из времени действия ключа:",
        reply_markup=build_users_key_show_kb(tg_id, email),
    )


@router.callback_query(
    AdminUserKeyEditorCallback.filter(F.action == "set"),
    IsAdminFilter(),
)
async def handle_expiry_set(
    callback_query: CallbackQuery,
    callback_data: AdminUserKeyEditorCallback,
    state: FSMContext,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    email = callback_data.data

    key_details = await get_key_details(session, email)

    if not key_details:
        await callback_query.message.edit_text(
            text="🚫 Информация о ключе не найдена.",
            reply_markup=build_editor_kb(tg_id),
        )
        return

    await state.update_data(tg_id=tg_id, email=email, op_type="set")
    await state.set_state(UserEditorState.waiting_for_expiry_time)

    text = (
        "✍️ Введите новое время действия ключа:"
        "\n\n📌 Формат: <b>год-месяц-день час:минута</b>"
        f"\n\n📄 Текущая дата: {datetime.fromtimestamp(key_details['expiry_time'] / 1000).strftime('%Y-%m-%d %H:%M')}"
    )

    await callback_query.message.edit_text(
        text=text,
        reply_markup=build_users_key_show_kb(tg_id, email),
    )


@router.message(UserEditorState.waiting_for_expiry_time, IsAdminFilter())
async def handle_expiry_time_input(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tg_id = data.get("tg_id")
    email = data.get("email")
    op_type = data.get("op_type")

    if op_type != "set" and (not message.text.isdigit() or int(message.text) < 0):
        await message.answer(
            text="🚫 Пожалуйста, введите корректное количество дней!",
            reply_markup=build_users_key_show_kb(tg_id, email),
        )
        return

    key_details = await get_key_details(session, email)

    if not key_details:
        await message.answer(
            text="🚫 Информация о ключе не найдена.",
            reply_markup=build_editor_kb(tg_id),
        )
        return

    try:
        current_expiry_time = datetime.fromtimestamp(
            key_details["expiry_time"] / 1000,
            tz=MOSCOW_TZ,
        )

        if op_type == "add":
            days = int(message.text)
            new_expiry_time = current_expiry_time + timedelta(days=days)
            text = f"✅ Ко времени действия ключа добавлено <b>{days} дн.</b>"
        elif op_type == "take":
            days = int(message.text)
            new_expiry_time = current_expiry_time - timedelta(days=days)
            text = f"✅ Из времени действия ключа вычтено <b>{days} дн.</b>"
        else:
            new_expiry_time = datetime.strptime(message.text, "%Y-%m-%d %H:%M")
            new_expiry_time = MOSCOW_TZ.localize(new_expiry_time)
            text = f"✅ Время действия ключа изменено на <b>{message.text} (МСК)</b>"

        new_expiry_timestamp = int(new_expiry_time.timestamp() * 1000)
        await change_expiry_time(new_expiry_timestamp, email, session)
    except ValueError:
        text = "🚫 Пожалуйста, используйте корректный формат даты (ГГГГ-ММ-ДД ЧЧ:ММ)!"
    except Exception as e:
        text = f"❗ Произошла ошибка во время изменения времени действия ключа: {e}"

    await message.answer(text=text, reply_markup=build_users_key_show_kb(tg_id, email))


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_update_key"),
    IsAdminFilter(),
)
async def handle_update_key(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    email = callback_data.data

    await callback_query.message.edit_text(
        text=f"📡 Выберите кластер, на котором пересоздать ключ <b>{email}</b>:",
        reply_markup=await build_cluster_selection_kb(
            session,
            tg_id,
            email,
            action="confirm_admin_key_reissue",
        ),
    )


@router.callback_query(F.data.startswith("confirm_admin_key_reissue|"), IsAdminFilter())
async def confirm_admin_key_reissue(callback_query: CallbackQuery, session: AsyncSession, state: FSMContext):
    _, tg_id, email, cluster_id = callback_query.data.split("|")
    tg_id = int(tg_id)

    try:
        servers = await get_servers(session)
        cluster_servers = servers.get(cluster_id, [])

        tariffs = await get_tariffs_for_cluster(session, cluster_id)
        if not tariffs:
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(
                    text="🔗 Привязать тариф",
                    callback_data=AdminPanelCallback(action="clusters").pack(),
                )
            )
            builder.row(
                InlineKeyboardButton(
                    text="🔙 Назад",
                    callback_data=AdminUserEditorCallback(
                        action="users_key_edit",
                        tg_id=tg_id,
                        data=email,
                    ).pack(),
                )
            )
            await callback_query.message.edit_text(
                f"🚫 <b>Невозможно пересоздать подписку</b>\n\n"
                f"📊 <b>Информация о кластере:</b>\n<blockquote>"
                f"🌐 <b>Кластер:</b> <code>{cluster_id}</code>\n"
                f"⚠️ <b>Статус:</b> Нет привязанного тарифа\n</blockquote>"
                f"💡 <b>Привяжите тариф к кластеру</b>",
                reply_markup=builder.as_markup(),
            )
            return

        if USE_COUNTRY_SELECTION:
            unique_countries = {srv["server_name"] for srv in cluster_servers}
            await state.update_data(tg_id=tg_id, email=email, cluster_id=cluster_id)
            builder = InlineKeyboardBuilder()
            for country in sorted(unique_countries):
                builder.button(
                    text=country,
                    callback_data=f"admin_reissue_country|{tg_id}|{email}|{country}",
                )
            builder.row(
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=f"users_key_edit|{email}",
                )
            )
            await callback_query.message.edit_text(
                "🌍 Выберите сервер (страну) для пересоздания подписки:",
                reply_markup=builder.as_markup(),
            )
            return

        result = await session.execute(select(Key.remnawave_link).where(Key.email == email))
        remnawave_link = result.scalar_one_or_none()

        await update_subscription(
            tg_id,
            email,
            session,
            cluster_override=cluster_id,
            remnawave_link=remnawave_link,
        )

        await handle_key_edit(
            callback_query,
            AdminUserEditorCallback(tg_id=tg_id, data=email, action="view_key"),
            session,
            True,
        )
    except Exception as e:
        logger.error(f"Ошибка при перевыпуске ключа {email}: {e}")
        await callback_query.message.answer(f"❗ Ошибка: {e}")


@router.callback_query(F.data.startswith("admin_reissue_country|"), IsAdminFilter())
async def admin_reissue_country(callback_query: CallbackQuery, session: AsyncSession, state: FSMContext):
    _, tg_id, email, country = callback_query.data.split("|")
    tg_id = int(tg_id)

    try:
        data = await state.get_data()
        cluster_id = data.get("cluster_id")

        if cluster_id:
            tariffs = await get_tariffs_for_cluster(session, cluster_id)
            if not tariffs:
                builder = InlineKeyboardBuilder()
                builder.row(
                    InlineKeyboardButton(
                        text="🔗 Привязать тариф",
                        callback_data=AdminPanelCallback(action="clusters").pack(),
                    )
                )
                builder.row(
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data=AdminUserEditorCallback(
                            action="users_key_edit",
                            tg_id=tg_id,
                            data=email,
                        ).pack(),
                    )
                )
                await callback_query.message.edit_text(
                    f"🚫 <b>Невозможно пересоздать подписку</b>\n\n"
                    f"📊 <b>Информация о кластере:</b>\n<blockquote>"
                    f"🌐 <b>Кластер:</b> <code>{cluster_id}</code>\n"
                    f"⚠️ <b>Статус:</b> Нет привязанного тарифа\n</blockquote>"
                    f"💡 <b>Привяжите тариф к кластеру</b>",
                    reply_markup=builder.as_markup(),
                )
                return

        result = await session.execute(select(Key.remnawave_link, Key.tariff_id).where(Key.email == email))
        remnawave_link, _tariff_id = result.one_or_none() or (None, None)

        await update_subscription(
            tg_id=tg_id,
            email=email,
            session=session,
            country_override=country,
            remnawave_link=remnawave_link,
        )

        await handle_key_edit(
            callback_query,
            AdminUserEditorCallback(tg_id=tg_id, data=email, action="view_key"),
            session,
            True,
        )
    except Exception as e:
        logger.error(f"Ошибка при перевыпуске ключа для страны {country}: {e}")
        await callback_query.message.answer(f"❗ Ошибка: {e}")


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_key"),
    IsAdminFilter(),
)
async def handle_delete_key(
    callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, session: AsyncSession
):
    email = callback_data.data

    result = await session.execute(select(Key.client_id).where(Key.email == email))
    client_id = result.scalar_one_or_none()

    if client_id is None:
        await callback_query.message.edit_text(
            text="🚫 Ключ не найден!",
            reply_markup=build_editor_kb(callback_data.tg_id),
        )
        return

    await callback_query.message.edit_text(
        text="❓ Вы уверены, что хотите удалить ключ?",
        reply_markup=build_key_delete_kb(callback_data.tg_id, email),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_key_confirm"),
    IsAdminFilter(),
)
async def handle_delete_key_confirm(
    callback_query: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    email = callback_data.data

    result = await session.execute(select(Key.client_id).where(Key.email == email))
    client_id = result.scalar_one_or_none()

    kb = build_editor_kb(callback_data.tg_id)

    if client_id:
        clusters = await get_servers(session=session)

        async def delete_key_from_servers():
            tasks = []
            for cluster_name, cluster_servers in clusters.items():
                for _ in cluster_servers:
                    tasks.append(delete_key_from_cluster(cluster_name, email, client_id, session))
            await asyncio.gather(*tasks, return_exceptions=True)

        await delete_key_from_servers()
        await delete_key(session, client_id)

        await callback_query.message.edit_text(text="✅ Ключ успешно удален.", reply_markup=kb)
    else:
        await callback_query.message.edit_text(
            text="🚫 Ключ не найден или уже удален.",
            reply_markup=kb,
        )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_user"),
    IsAdminFilter(),
)
async def handle_delete_user(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
):
    tg_id = callback_data.tg_id
    await callback_query.message.edit_text(
        text=f"❗️ Вы уверены, что хотите удалить пользователя с ID {tg_id}?",
        reply_markup=build_user_delete_kb(tg_id),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_delete_user_confirm"),
    IsAdminFilter(),
)
async def handle_delete_user_confirm(
    callback_query: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id

    result = await session.execute(select(Key.email, Key.client_id).where(Key.tg_id == tg_id))
    key_records = result.all()

    async def delete_keys_from_servers():
        try:
            tasks = []
            servers = await get_servers(session=session)
            for email, client_id in key_records:
                for cluster_id, _cluster in servers.items():
                    tasks.append(delete_key_from_cluster(cluster_id, email, client_id, session))
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Ошибка при удалении ключей с серверов для пользователя {tg_id}: {e}")

    await delete_keys_from_servers()

    try:
        await delete_user_data(session, tg_id)
        await callback_query.message.edit_text(
            text=f"🗑️ Пользователь с ID {tg_id} был удален.",
            reply_markup=build_admin_back_kb(),
        )
    except Exception as e:
        logger.error(f"Ошибка при удалении данных из базы данных для пользователя {tg_id}: {e}")
        await callback_query.message.edit_text(
            text=f"❌ Произошла ошибка при удалении пользователя с ID {tg_id}. Попробуйте снова.",
            reply_markup=build_admin_back_kb(),
        )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_traffic"),
    IsAdminFilter(),
)
async def handle_user_traffic(
    callback_query: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    email = callback_data.data

    await callback_query.message.edit_text("⏳ Получаем данные о трафике, пожалуйста, подождите...")

    traffic_data = await get_user_traffic(session, tg_id, email)

    if traffic_data["status"] == "error":
        await callback_query.message.edit_text(
            traffic_data["message"],
            reply_markup=build_editor_kb(tg_id, True),
        )
        return

    total_traffic = 0
    result_text = f"📊 <b>Трафик подписки {email}:</b>\n\n"

    for server, traffic in traffic_data["traffic"].items():
        if isinstance(traffic, str):
            result_text += f"❌ {server}: {traffic}\n"
        else:
            result_text += f"🌍 {server}: <b>{traffic} ГБ</b>\n"
            total_traffic += traffic

    result_text += f"\n🔢 <b>Общий трафик:</b> {total_traffic:.2f} ГБ"

    await callback_query.message.edit_text(
        result_text,
        reply_markup=build_editor_kb(tg_id, True),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_create_key"),
    IsAdminFilter(),
)
async def handle_create_key_start(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    state: FSMContext,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    await state.update_data(tg_id=tg_id)

    if USE_COUNTRY_SELECTION:
        await state.set_state(UserEditorState.selecting_country)

        stmt = select(Server.server_name).distinct().order_by(Server.server_name)
        result = await session.execute(stmt)
        countries = [row[0] for row in result.all()]

        if not countries:
            await callback_query.message.edit_text(
                "❌ Нет доступных стран для создания ключа.",
                reply_markup=build_editor_kb(tg_id),
            )
            return

        builder = InlineKeyboardBuilder()
        for country in countries:
            builder.button(text=country, callback_data=country)
        builder.adjust(1)
        builder.row(build_admin_back_btn())

        await callback_query.message.edit_text(
            "🌍 <b>Выберите страну для создания ключа:</b>",
            reply_markup=builder.as_markup(),
        )
        return

    await state.set_state(UserEditorState.selecting_cluster)

    servers = await get_servers(session=session)
    cluster_names = list(servers.keys())

    if not cluster_names:
        await callback_query.message.edit_text(
            "❌ Нет доступных кластеров для создания ключа.",
            reply_markup=build_editor_kb(tg_id),
        )
        return

    builder = InlineKeyboardBuilder()
    for cluster in cluster_names:
        builder.button(text=f"🌐 {cluster}", callback_data=cluster)
    builder.adjust(2)
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        "🌐 <b>Выберите кластер для создания ключа:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(UserEditorState.selecting_country, IsAdminFilter())
async def handle_create_key_country(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    country = callback_query.data
    await state.update_data(country=country)
    await state.set_state(UserEditorState.selecting_duration)

    builder = InlineKeyboardBuilder()

    result = await session.execute(select(Server.cluster_name).where(Server.server_name == country))
    row = result.mappings().first()

    if not row:
        await callback_query.message.edit_text("❌ Сервер не найден.")
        return

    cluster_name = row["cluster_name"]
    await state.update_data(cluster_name=cluster_name)

    tariffs = await get_tariffs_for_cluster(session, cluster_name)

    for tariff in tariffs:
        if tariff["duration_days"] < 1:
            continue
        builder.button(
            text=f"{tariff['name']} — {tariff['price_rub']}₽",
            callback_data=f"tariff_{tariff['id']}",
        )

    builder.adjust(1)
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text=f"🕒 <b>Выберите срок действия ключа для страны <code>{country}</code>:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(UserEditorState.selecting_cluster, IsAdminFilter())
async def handle_create_key_cluster(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    cluster_name = callback_query.data

    data = await state.get_data()
    tg_id = data.get("tg_id")

    if not tg_id:
        await callback_query.message.edit_text("❌ Ошибка: tg_id клиента не найден.")
        return

    await state.update_data(cluster_name=cluster_name)
    await state.set_state(UserEditorState.selecting_duration)

    tariffs = await get_tariffs_for_cluster(session, cluster_name)

    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        if tariff["duration_days"] < 1:
            continue
        builder.button(
            text=f"{tariff['name']} — {tariff['price_rub']}₽",
            callback_data=f"tariff_{tariff['id']}",
        )

    builder.adjust(1)
    builder.row(build_admin_back_btn())

    await callback_query.message.edit_text(
        text=f"🕒 <b>Выберите срок действия ключа для кластера <code>{cluster_name}</code>:</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(UserEditorState.selecting_duration, IsAdminFilter())
async def handle_create_key_duration(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tg_id = data.get("tg_id", callback_query.from_user.id)

    try:
        if not callback_query.data.startswith("tariff_"):
            raise ValueError("Некорректный callback_data")
        tariff_id = int(callback_query.data.replace("tariff_", ""))

        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            raise ValueError("Тариф не найден.")

        duration_days = tariff["duration_days"]
        client_id = str(uuid.uuid4())
        email = await generate_random_email(session=session)
        expiry = datetime.now(tz=timezone.utc) + timedelta(days=duration_days)
        expiry_ms = int(expiry.timestamp() * 1000)

        if USE_COUNTRY_SELECTION and "country" in data:
            country = data["country"]
            await create_key_on_cluster(
                country,
                tg_id,
                client_id,
                email,
                expiry_ms,
                plan=tariff_id,
                session=session,
            )

            await state.clear()
            await callback_query.message.edit_text(
                f"✅ Ключ успешно создан для страны <b>{country}</b> на {duration_days} дней.",
                reply_markup=build_editor_kb(tg_id),
            )
        elif "cluster_name" in data:
            cluster_name = data["cluster_name"]
            await create_key_on_cluster(
                cluster_name,
                tg_id,
                client_id,
                email,
                expiry_ms,
                plan=tariff_id,
                session=session,
            )

            await state.clear()
            await callback_query.message.edit_text(
                f"✅ Ключ успешно создан в кластере <b>{cluster_name}</b> на {duration_days} дней.",
                reply_markup=build_editor_kb(tg_id),
            )
        else:
            await callback_query.message.edit_text("❌ Не удалось определить источник — страна или кластер.")
    except Exception as e:
        logger.error(f"[CreateKey] Ошибка при создании ключа: {e}")
        await callback_query.message.edit_text(
            "❌ Не удалось создать ключ. Попробуйте позже.",
            reply_markup=build_editor_kb(tg_id),
        )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_reset_traffic"),
    IsAdminFilter(),
)
async def handle_reset_traffic(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    email = callback_data.data

    stmt = select(Key.server_id, Key.client_id).where((Key.tg_id == tg_id) & (Key.email == email))
    result = await session.execute(stmt)
    record = result.first()

    if not record:
        await callback_query.message.edit_text(
            "❌ Ключ не найден в базе данных.",
            reply_markup=build_editor_kb(tg_id),
        )
        return

    cluster_id, _client_id = record

    try:
        await reset_traffic_in_cluster(cluster_id, email, session)
        await callback_query.message.edit_text(
            f"✅ Трафик для ключа <b>{email}</b> успешно сброшен.",
            reply_markup=build_editor_kb(tg_id),
        )
    except Exception as e:
        logger.error(f"Ошибка при сбросе трафика: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при сбросе трафика. Попробуйте позже.",
            reply_markup=build_editor_kb(tg_id),
        )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_freeze"),
    IsAdminFilter(),
)
async def handle_admin_freeze_subscription(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    email = str(callback_data.data)

    try:
        record = await get_key_details(session, email)
        if not record:
            await callback_query.message.edit_text(
                text="🚫 Информация о ключе не найдена.",
                reply_markup=build_editor_kb(tg_id),
            )
            return

        client_id = record["client_id"]
        cluster_id = record["server_id"]

        result = await toggle_client_on_cluster(cluster_id, email, client_id, enable=False, session=session)
        if result["status"] != "success":
            text_error = (
                "Произошла ошибка при заморозке подписки.\n"
                f"Детали: {result.get('error') or result.get('results')}"
            )
            await callback_query.message.edit_text(
                text_error,
                reply_markup=build_editor_kb(tg_id, True),
            )
            return

        now_ms = int(time.time() * 1000)
        time_left = record["expiry_time"] - now_ms
        if time_left < 0:
            time_left = 0

        await mark_key_as_frozen(session, record["tg_id"], client_id, time_left)
        await session.commit()

        await callback_query.answer("✅ Подписка заморожена")

        await handle_key_edit(
            callback_query=callback_query,
            callback_data=callback_data,
            session=session,
            update=False,
        )
    except Exception as e:
        await handle_error(tg_id, callback_query, f"Ошибка при заморозке подписки: {e}")


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_unfreeze"),
    IsAdminFilter(),
)
async def handle_admin_unfreeze_subscription(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    email = str(callback_data.data)

    try:
        record = await get_key_details(session, email)
        if not record:
            await callback_query.message.edit_text(
                text="🚫 Информация о ключе не найдена.",
                reply_markup=build_editor_kb(tg_id),
            )
            return

        client_id = record["client_id"]
        cluster_id = record["server_id"]

        result = await toggle_client_on_cluster(cluster_id, email, client_id, enable=True, session=session)
        if result["status"] != "success":
            text_error = (
                "Произошла ошибка при включении подписки.\n"
                f"Детали: {result.get('error') or result.get('results')}"
            )
            await callback_query.message.edit_text(
                text_error,
                reply_markup=build_editor_kb(tg_id, True),
            )
            return

        tariff = await get_tariff_by_id(session, record["tariff_id"]) if record.get("tariff_id") else None
        if not tariff:
            total_gb = 0
            hwid_limit = 0
        else:
            total_gb = int(tariff.get("traffic_limit") or 0)
            hwid_limit = int(tariff.get("device_limit") or 0)

        now_ms = int(time.time() * 1000)
        leftover = record["expiry_time"]
        if leftover < 0:
            leftover = 0
        new_expiry_time = now_ms + leftover

        await mark_key_as_unfrozen(session, record["tg_id"], client_id, new_expiry_time)
        await session.commit()

        await renew_key_in_cluster(
            cluster_id=cluster_id,
            email=email,
            client_id=client_id,
            new_expiry_time=new_expiry_time,
            total_gb=total_gb,
            session=session,
            hwid_device_limit=hwid_limit,
            reset_traffic=False,
            plan=record.get("tariff_id"),
        )

        await callback_query.answer("✅ Подписка разморожена")

        await handle_key_edit(
            callback_query=callback_query,
            callback_data=callback_data,
            session=session,
            update=False,
        )
    except Exception as e:
        await handle_error(tg_id, callback_query, f"Ошибка при включении подписки: {e}")


async def change_expiry_time(expiry_time: int, email: str, session: AsyncSession) -> Exception | None:
    result = await session.execute(select(Key.client_id, Key.tariff_id, Key.server_id).where(Key.email == email))
    row = result.first()
    if not row:
        return ValueError(f"User with email {email} was not found")

    client_id, tariff_id, server_id = row
    if server_id is None:
        return ValueError(f"Key with client_id {client_id} was not found")

    traffic_limit = 0
    device_limit = None
    key_subgroup = None
    if tariff_id:
        result = await session.execute(
            select(Tariff.traffic_limit, Tariff.device_limit, Tariff.subgroup_title).where(
                Tariff.id == tariff_id,
                Tariff.is_active.is_(True),
            )
        )
        tariff = result.first()
        if tariff:
            traffic_limit = int(tariff[0]) if tariff[0] is not None else 0
            device_limit = int(tariff[1]) if tariff[1] is not None else 0
            key_subgroup = tariff[2]

    servers = await get_servers(session=session)

    if server_id in servers:
        target_cluster = server_id
    else:
        target_cluster = None
        for cluster_name, cluster_servers in servers.items():
            if any(s.get("server_name") == server_id for s in cluster_servers):
                target_cluster = cluster_name
                break

        if not target_cluster:
            return ValueError(f"No suitable cluster found for server {server_id}")

    await renew_key_in_cluster(
        cluster_id=target_cluster,
        email=email,
        client_id=client_id,
        new_expiry_time=expiry_time,
        total_gb=traffic_limit,
        session=session,
        hwid_device_limit=device_limit,
        reset_traffic=False,
        target_subgroup=key_subgroup,
        old_subgroup=key_subgroup,
        plan=tariff_id,
    )

    await update_key_expiry(session, client_id, expiry_time)
    return None
