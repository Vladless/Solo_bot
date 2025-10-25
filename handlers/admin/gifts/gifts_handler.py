from collections import defaultdict

from aiogram import Bot, F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Gift, GiftUsage, Tariff
from database.tariffs import create_subgroup_hash, find_subgroup_by_hash, get_tariffs
from handlers.utils import edit_or_send_message, format_days, format_months
from logger import logger

from ..panel.keyboard import AdminPanelCallback
from .keyboard import build_admin_gifts_kb, build_gifts_list_kb


router = Router()


class GiftCreationState(StatesGroup):
    waiting_for_gift_limit = State()
    waiting_for_limit_input_or_unlimited = State()


@router.callback_query(AdminPanelCallback.filter(F.action == "gifts"))
async def admin_gift_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        text="🎁 <b>Подарки</b>\nВыберите, что хотите сделать:", reply_markup=build_admin_gifts_kb()
    )


@router.callback_query(F.data == "admin_gift_create")
async def admin_create_gift_step1(callback: CallbackQuery, session: AsyncSession):
    tariffs_data = await get_tariffs(session, group_code="gifts", with_subgroup_weights=True)
    tariffs = [t for t in tariffs_data["tariffs"] if t.get("is_active")]
    subgroup_weights = tariffs_data["subgroup_weights"]

    if not tariffs:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад", callback_data=AdminPanelCallback(action="gifts").pack())
        await callback.message.edit_text("❌ Нет активных тарифов в группе 'gifts'.", reply_markup=builder.as_markup())
        return

    grouped_tariffs = defaultdict(list)
    for t in tariffs:
        grouped_tariffs[t.get("subgroup_title")].append(t)

    builder = InlineKeyboardBuilder()

    for t in grouped_tariffs.get(None, []):
        if t.get("duration_days") % 30 == 0:
            duration_text = format_months(t.get("duration_days") // 30)
        else:
            duration_text = format_days(t.get("duration_days"))

        builder.row(
            types.InlineKeyboardButton(
                text=f"{t.get('name')} – {duration_text}", callback_data=f"admin_gift_select|{t.get('id')}"
            )
        )

    sorted_subgroups = sorted([k for k in grouped_tariffs if k], key=lambda x: (subgroup_weights.get(x, 999999), x))

    for subgroup in sorted_subgroups:
        subgroup_hash = create_subgroup_hash(subgroup, "gifts")
        builder.row(
            types.InlineKeyboardButton(
                text=subgroup,
                callback_data=f"admin_gift_subgroup|{subgroup_hash}",
            )
        )

    builder.row(types.InlineKeyboardButton(text="🔙 Назад", callback_data=AdminPanelCallback(action="gifts").pack()))

    await callback.message.edit_text("🎁 Выберите тариф для подарка:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("admin_gift_subgroup|"))
async def admin_gift_show_tariffs_in_subgroup(callback: CallbackQuery, session: AsyncSession):
    try:
        subgroup_hash = callback.data.split("|", 1)[1]

        subgroup = await find_subgroup_by_hash(session, subgroup_hash, "gifts")
        if not subgroup:
            await callback.message.edit_text("❌ Подгруппа не найдена.")
            return

        tariffs = await get_tariffs(session, group_code="gifts")
        filtered = [t for t in tariffs if t.get("subgroup_title") == subgroup and t.get("is_active")]
        if not filtered:
            await callback.message.edit_text("❌ В этой подгруппе пока нет тарифов.")
            return

        builder = InlineKeyboardBuilder()
        for t in filtered:
            if t.get("duration_days") % 30 == 0:
                duration_text = format_months(t.get("duration_days") // 30)
            else:
                duration_text = format_days(t.get("duration_days"))

            builder.row(
                types.InlineKeyboardButton(
                    text=f"{t.get('name')} – {duration_text}",
                    callback_data=f"admin_gift_select|{t.get('id')}",
                )
            )

        builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_gift_create"))

        await edit_or_send_message(
            target_message=callback.message,
            text=f"<b>{subgroup}</b>\n\nВыберите тариф:",
            reply_markup=builder.as_markup(),
        )

    except Exception as e:
        logger.error(f"[ADMIN_GIFT_SUBGROUP] Ошибка при отображении подгруппы: {e}")
        await callback.message.answer("❌ Произошла ошибка при отображении тарифов.")


@router.callback_query(F.data.startswith("admin_gift_select|"))
async def handle_tariff_selection(callback: CallbackQuery, state: FSMContext):
    tariff_id = int(callback.data.split("|")[1])
    await state.update_data(tariff_id=tariff_id)
    await state.set_state(GiftCreationState.waiting_for_limit_input_or_unlimited)

    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Назад", callback_data="admin_gift_create")
    await callback.message.edit_text(
        "🔢 Введите максимальное количество активаций подарка:", reply_markup=kb.as_markup()
    )


@router.callback_query(F.data == "gift_limit_unlimited")
async def handle_unlimited_gift(callback: CallbackQuery, state: FSMContext, bot: Bot):
    from handlers.payments.gift import finalize_gift

    data = await state.get_data()
    session: AsyncSession = callback.bot["session"]
    await state.clear()
    await finalize_gift(callback.message, session, bot, data, is_unlimited=True)


@router.message(GiftCreationState.waiting_for_limit_input_or_unlimited)
async def handle_limited_gift_input(message: types.Message, session: AsyncSession, state: FSMContext, bot: Bot):
    from handlers.payments.gift import finalize_gift

    try:
        max_usages = int(message.text.strip())
        if max_usages <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное положительное число.")
        return

    data = await state.get_data()
    data["max_usages"] = max_usages
    await state.clear()
    await finalize_gift(message, session, bot, data, is_unlimited=False)


@router.callback_query(F.data == "admin_gifts_all")
async def show_gifts_page(callback: CallbackQuery, session: AsyncSession):
    await show_gift_list(callback, session, page=1)


@router.callback_query(F.data.startswith("gifts_page|"))
async def paginate_gifts(callback: CallbackQuery, session: AsyncSession):
    page = int(callback.data.split("|")[1])
    await show_gift_list(callback, session, page)


async def show_gift_list(callback: CallbackQuery, session: AsyncSession, page: int):
    limit = 10
    offset = (page - 1) * limit

    stmt = select(Gift).order_by(Gift.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    gifts = result.scalars().all()

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()

    if not gifts:
        builder.button(text="🔙 Назад", callback_data=AdminPanelCallback(action="gifts").pack())
        await callback.message.edit_text("❌ Подарки не найдены.", reply_markup=builder.as_markup())
        return

    keyboard = build_gifts_list_kb(gifts, page, total=len(gifts))

    builder.inline_keyboard.extend(keyboard.inline_keyboard)
    builder.row(types.InlineKeyboardButton(text="🔙 Назад", callback_data=AdminPanelCallback(action="gifts").pack()))

    await callback.message.edit_text(f"🎁 <b>Список подарков</b>\nСтраница {page}:", reply_markup=builder.as_markup())


async def show_gift_list(callback: CallbackQuery, session: AsyncSession, page: int):
    limit = 10
    offset = (page - 1) * limit

    stmt = select(Gift).order_by(Gift.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    gifts = result.scalars().all()

    builder = InlineKeyboardBuilder()

    if not gifts:
        builder.button(text="🔙 Назад", callback_data=AdminPanelCallback(action="gifts").pack())
        await callback.message.edit_text("❌ Подарки не найдены.", reply_markup=builder.as_markup())
        return

    keyboard = build_gifts_list_kb(gifts, page, total=len(gifts))

    for row in keyboard.inline_keyboard:
        builder.row(*row)

    await callback.message.edit_text(f"🎁 <b>Список подарков</b>\nСтраница {page}:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("gift_view|"))
async def view_gift(callback: CallbackQuery, session: AsyncSession):
    gift_id = callback.data.split("|")[1]

    result = await session.execute(select(Gift).where(Gift.gift_id == gift_id))
    gift = result.scalar_one_or_none()

    if not gift:
        await callback.message.edit_text("❌ Подарок не найден.")
        return

    usage_result = await session.execute(
        select(func.count()).select_from(GiftUsage).where(GiftUsage.gift_id == gift_id)
    )
    used_count = usage_result.scalar_one()
    usage_text = f"{used_count}/{gift.max_usages}" if gift.max_usages else "∞"

    duration_days = (gift.expiry_time.date() - gift.created_at.date()).days
    if duration_days % 30 == 0:
        duration_text = format_months(duration_days // 30)
    else:
        duration_text = format_days(duration_days)

    text = (
        f"🎁 <b>Подарок</b>\n"
        f"ID: <code>{gift.gift_id}</code>\n"
        f"Срок: <b>{duration_text}</b>\n"
        f"Активаций: <b>{usage_text}</b>\n"
        f"<b>Ссылка для активации:</b>\n<blockquote>{gift.gift_link}</blockquote>"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Удалить", callback_data=f"gift_delete|{gift_id}")
    builder.button(text="🔙 Назад", callback_data="admin_gifts_all")

    await callback.message.edit_text(text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("gift_delete|"))
async def delete_gift(callback: CallbackQuery, session: AsyncSession):
    gift_id = callback.data.split("|")[1]

    await session.execute(delete(GiftUsage).where(GiftUsage.gift_id == gift_id))
    await session.execute(delete(Gift).where(Gift.gift_id == gift_id))
    await session.commit()

    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад к списку", callback_data="admin_gifts_all")

    await callback.message.edit_text("✅ Подарок удалён.", reply_markup=builder.as_markup())
