import pytz

from aiogram import F, Router, types
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Gift, GiftUsage
from filters.admin import IsAdminFilter

from .keyboard import (
    AdminUserEditorCallback,
    build_gift_delete_confirm_kb,
    build_user_gifts_kb,
)


MOSCOW_TZ = pytz.timezone("Europe/Moscow")

router = Router()


async def get_user_gifts(session: AsyncSession, tg_id: int) -> list:
    stmt = select(Gift).where(Gift.sender_tg_id == tg_id).order_by(Gift.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()


async def show_gifts_list(message: types.Message, session: AsyncSession, tg_id: int, page: int = 0):
    gifts = await get_user_gifts(session, tg_id)

    if not gifts:
        text = f"🎁 <b>Подарки пользователя</b> <code>{tg_id}</code>\n\nУ пользователя нет созданных подарков."
        await message.edit_text(
            text=text,
            reply_markup=build_user_gifts_kb(tg_id, [], page),
        )
        return

    from .keyboard import GIFTS_PER_PAGE

    start_idx = page * GIFTS_PER_PAGE
    end_idx = start_idx + GIFTS_PER_PAGE
    page_gifts = gifts[start_idx:end_idx]

    gift_ids = [g.gift_id for g in page_gifts]
    usages_stmt = select(GiftUsage).where(GiftUsage.gift_id.in_(gift_ids))
    usages_result = await session.execute(usages_stmt)
    usages = usages_result.scalars().all()
    usage_map = {u.gift_id: u.tg_id for u in usages}

    lines = [f"🎁 <b>Подарки пользователя</b> <code>{tg_id}</code>\n"]

    for i, gift in enumerate(page_gifts, start=start_idx + 1):
        if gift.is_used:
            used_by = usage_map.get(gift.gift_id)
            status = f"✅ Использован: <code>{used_by}</code>" if used_by else "✅ Использован"
        else:
            status = "⏳ Не использован"

        created_str = gift.created_at.replace(tzinfo=pytz.UTC).astimezone(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")

        lines.append(f"\n<b>{i}.🎁 </b> {gift.selected_months} мес.\n   📅 Создан: {created_str}\n   {status}")

    lines.append("\n\n<i>Нажмите кнопку для удаления:</i>")

    await message.edit_text(
        text="".join(lines),
        reply_markup=build_user_gifts_kb(tg_id, gifts, page),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_gifts"),
    IsAdminFilter(),
)
async def handle_users_gifts(
    callback: types.CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    await show_gifts_list(callback.message, session, callback_data.tg_id, page=0)


@router.callback_query(
    F.data.startswith("user_gift_page|"),
    IsAdminFilter(),
)
async def handle_gifts_page(
    callback: types.CallbackQuery,
    session: AsyncSession,
):
    _, tg_id, page = callback.data.split("|")
    await show_gifts_list(callback.message, session, int(tg_id), page=int(page))


@router.callback_query(
    F.data.startswith("user_gift_del|"),
    IsAdminFilter(),
)
async def handle_gift_delete(
    callback: types.CallbackQuery,
    session: AsyncSession,
):
    _, tg_id, gift_id, page = callback.data.split("|")
    tg_id, page = int(tg_id), int(page)

    stmt = select(Gift).where(Gift.gift_id == gift_id)
    result = await session.execute(stmt)
    gift = result.scalar_one_or_none()

    if not gift:
        await callback.answer("❌ Подарок не найден", show_alert=True)
        return

    created_str = gift.created_at.replace(tzinfo=pytz.UTC).astimezone(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")
    status = "✅ Использован" if gift.is_used else "⏳ Не использован"

    await callback.message.edit_text(
        text=(
            f"❓ <b>Удалить подарок?</b>\n\n"
            f"📆 Длительность: {gift.selected_months} мес.\n"
            f"📅 Создан: {created_str}\n"
            f"📊 Статус: {status}\n\n"
            f"⚠️ Это действие необратимо!"
        ),
        reply_markup=build_gift_delete_confirm_kb(tg_id, gift_id, page),
    )


@router.callback_query(
    F.data.startswith("user_gift_del_c|"),
    IsAdminFilter(),
)
async def handle_gift_delete_confirm(
    callback: types.CallbackQuery,
    session: AsyncSession,
):
    _, tg_id, gift_id = callback.data.split("|")
    tg_id = int(tg_id)

    await session.execute(delete(GiftUsage).where(GiftUsage.gift_id == gift_id))
    await session.execute(delete(Gift).where(Gift.gift_id == gift_id))
    await session.commit()

    await callback.answer("✅ Подарок удалён", show_alert=True)
    await show_gifts_list(callback.message, session, tg_id, page=0)
