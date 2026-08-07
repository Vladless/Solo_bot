
from typing import Any

import pytz

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    get_tariffs_for_cluster,
)
from database.tariffs import find_subgroup_by_hash, get_subgroup_description, get_tariffs
from handlers.utils import edit_or_send_message
from services.tariffs.visibility import filter_visible_tariffs
from settings.buttons import BACK, MAIN_MENU
from settings.texts import (
    SELECT_TARIFF_HINT,
    SUBGROUP_TITLE_TEMPLATE,
)

from .flow import handle_key_creation
from ..utils import (
    add_tariff_button_generic,
    format_subgroup_description,
    format_tariff_descriptions,
)


router = Router()
moscow_tz = pytz.timezone("Europe/Moscow")


@router.callback_query(F.data == "create_key")
@router.callback_query(F.data == "buy")
@router.message(F.text == "/buy")
async def confirm_create_new_key(
    callback_query_or_message: CallbackQuery | Message,
    state: FSMContext,
    session: AsyncSession,
):
    if isinstance(callback_query_or_message, CallbackQuery):
        tg_id = callback_query_or_message.from_user.id
        message_or_query: Message | CallbackQuery = callback_query_or_message
    else:
        tg_id = callback_query_or_message.from_user.id
        message_or_query = callback_query_or_message

    await handle_key_creation(tg_id, state, session, message_or_query)


@router.callback_query(F.data.startswith("tariff_subgroup_user|"))
async def show_tariffs_in_subgroup_user(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    subgroup_hash = callback.data.split("|")[1]
    data = await state.get_data()
    cluster_name = data.get("cluster_name")
    group_code = data.get("group_code")

    subgroup = await find_subgroup_by_hash(session, subgroup_hash, group_code)
    if not subgroup:
        await edit_or_send_message(
            target_message=callback.message,
            text="❌ Подгруппа не найдена.",
            reply_markup=None,
        )
        return

    await state.update_data(tariff_subgroup_hash=subgroup_hash)

    tariffs_for_cluster = await get_tariffs_for_cluster(session, cluster_name)
    filtered: list[dict[str, Any]] = []

    if tariffs_for_cluster:
        group_code = tariffs_for_cluster[0].get("group_code")
        if group_code:
            tariffs = await get_tariffs(session, group_code=group_code)
            filtered = await filter_visible_tariffs(
                session,
                callback.from_user.id,
                [tariff for tariff in tariffs if tariff.get("subgroup_title") == subgroup and tariff.get("is_active")],
            )

    if not filtered:
        await edit_or_send_message(
            target_message=callback.message,
            text="❌ В этой подгруппе пока нет тарифов.",
            reply_markup=None,
        )
        return

    tg_id = callback.from_user.id
    language_code = callback.from_user.language_code

    builder = InlineKeyboardBuilder()
    for tariff in filtered:
        await add_tariff_button_generic(
            builder=builder,
            tariff=tariff,
            session=session,
            tg_id=tg_id,
            language_code=language_code,
            callback_prefix="select_tariff_plan",
        )

    builder.row(InlineKeyboardButton(text=BACK, callback_data="back_to_tariff_group_list"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    sub_desc = await get_subgroup_description(session, group_code, subgroup)
    await edit_or_send_message(
        target_message=callback.message,
        text="\n".join(
            block
            for block in (
                SUBGROUP_TITLE_TEMPLATE.format(subgroup=subgroup),
                format_subgroup_description(sub_desc),
                format_tariff_descriptions(filtered),
                SELECT_TARIFF_HINT,
            )
            if block
        ),
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "back_to_tariff_group_list")
async def back_to_tariff_group_list(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    tg_id = callback.from_user.id
    await handle_key_creation(
        tg_id=tg_id,
        state=state,
        session=session,
        message_or_query=callback,
    )


@router.callback_query(F.data == "back_to_subgroup_tariffs")
async def back_to_subgroup_tariffs(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Возврат к списку тарифов текущей подгруппы (из конфигуратора)."""
    data = await state.get_data()
    subgroup_hash = data.get("tariff_subgroup_hash")
    if not subgroup_hash:
        await back_to_tariff_group_list(callback, state, session)
        return
    cluster_name = data.get("cluster_name")
    group_code = data.get("group_code")

    subgroup = await find_subgroup_by_hash(session, subgroup_hash, group_code)
    if not subgroup:
        await back_to_tariff_group_list(callback, state, session)
        return

    tariffs_for_cluster = await get_tariffs_for_cluster(session, cluster_name)
    filtered: list[dict[str, Any]] = []
    if tariffs_for_cluster:
        gc = tariffs_for_cluster[0].get("group_code")
        if gc:
            tariffs = await get_tariffs(session, group_code=gc)
            filtered = await filter_visible_tariffs(
                session,
                callback.from_user.id,
                [t for t in tariffs if t.get("subgroup_title") == subgroup and t.get("is_active")],
            )

    if not filtered:
        await back_to_tariff_group_list(callback, state, session)
        return

    tg_id = callback.from_user.id
    language_code = getattr(callback.from_user, "language_code", None)
    builder = InlineKeyboardBuilder()
    for tariff in filtered:
        await add_tariff_button_generic(
            builder=builder,
            tariff=tariff,
            session=session,
            tg_id=tg_id,
            language_code=language_code,
            callback_prefix="select_tariff_plan",
        )
    builder.row(InlineKeyboardButton(text=BACK, callback_data="back_to_tariff_group_list"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    sub_desc = await get_subgroup_description(session, group_code, subgroup)
    await edit_or_send_message(
        target_message=callback.message,
        text="\n".join(
            block
            for block in (
                SUBGROUP_TITLE_TEMPLATE.format(subgroup=subgroup),
                format_subgroup_description(sub_desc),
                format_tariff_descriptions(filtered),
                SELECT_TARIFF_HINT,
            )
            if block
        ),
        reply_markup=builder.as_markup(),
    )
    await callback.answer()
