import csv
import io

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import delete_user_data
from database.models import BlockedUser, Key, ManualBan
from filters.admin import IsAdminFilter
from logger import logger

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb
from .keyboard import (
    build_bans_kb,
    build_blocked_users_kb,
    build_manual_bans_kb,
    build_shadow_bans_kb,
)


router = Router()


class PreemptiveBanStates(StatesGroup):
    waiting_for_preemptive_ids = State()


@router.callback_query(AdminPanelCallback.filter(F.action == "bans"), IsAdminFilter())
async def handle_bans(callback_query: CallbackQuery):
    text_ = (
        "🚫 <b>Управление банами</b>\n\n"
        "📛 <b>Забанившие бота</b> — пользователи, которые заблокировали бота вручную.\n"
        "👻 <b>Теневые баны</b> — пользователи, действия которых игнорируются.\n"
        "🔒 <b>Ручные баны</b> — пользователи, которых вы забанили через админку.\n\n"
        "⬇ Выберите нужный раздел:"
    )
    await callback_query.message.edit_text(text=text_, reply_markup=build_bans_kb())


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_blocked_menu"), IsAdminFilter())
async def handle_blocked_users_menu(callback_query: CallbackQuery):
    text_ = (
        "📛 <b>Забанившие бота</b>\n\n"
        "Пользователи, которые заблокировали бота вручную или удалили чат.\n"
        "⬇ Выберите действие:"
    )
    await callback_query.message.edit_text(text=text_, reply_markup=build_blocked_users_kb())


def get_shadow_bans_menu_text() -> str:
    return (
        "👻 <b>Теневые баны</b>\n\n"
        "Пользователи, действия которых игнорируются ботом.\n"
        "Они не получают уведомлений о бане.\n\n"
        "💡 <b>Можно добавить несколько пользователей за раз:</b>\n"
        "Отправьте список Telegram ID (один на строке).\n"
        "Пример:\n<code>123456789\n987654321\n555666777</code>\n\n"
        "⬇ Выберите действие:"
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_shadow_menu"), IsAdminFilter())
async def handle_shadow_bans_menu(callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        text=get_shadow_bans_menu_text(),
        reply_markup=build_shadow_bans_kb()
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_manual_menu"), IsAdminFilter())
async def handle_manual_bans_menu(callback_query: CallbackQuery):
    text_ = (
        "🔒 <b>Ручные баны</b>\n\n"
        "Пользователи, которых вы забанили через админку.\n"
        "⬇ Выберите действие:"
    )
    await callback_query.message.edit_text(text=text_, reply_markup=build_manual_bans_kb())


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_export"), IsAdminFilter())
async def handle_bans_export(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_blocked_users_kb()
    try:
        result = await session.execute(select(BlockedUser.tg_id))
        banned_users = result.scalars().all()

        csv_output = io.StringIO()
        writer = csv.writer(csv_output)
        writer.writerow(["tg_id"])

        for tg_id in banned_users:
            writer.writerow([tg_id])

        csv_output.seek(0)
        document = BufferedInputFile(file=csv_output.getvalue().encode("utf-8"), filename="banned_users.csv")

        await callback_query.message.answer_document(
            document=document,
            caption="📥 Экспорт пользователей, заблокировавших бота (CSV)",
        )
    except Exception as e:
        await callback_query.message.answer(
            text=f"❗ Произошла ошибка при экспорте: {e}",
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_delete_banned"), IsAdminFilter())
async def handle_bans_delete_banned(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_blocked_users_kb()
    try:
        stmt = (
            select(BlockedUser.tg_id)
            .outerjoin(Key, BlockedUser.tg_id == Key.tg_id)
            .where(Key.tg_id.is_(None))
        )
        result = await session.execute(stmt)
        blocked_ids = [row[0] for row in result.all()]

        if not blocked_ids:
            await callback_query.message.answer(
                text="📂 Нет заблокировавших пользователей для удаления.",
                reply_markup=kb,
            )
            return

        for tg_id in blocked_ids:
            await delete_user_data(session, tg_id)

        await callback_query.message.answer(
            text=f"🗑️ Удалены данные о {len(blocked_ids)} пользователях и связанных записях.",
            reply_markup=kb,
        )
    except Exception as e:
        await callback_query.message.answer(
            text=f"❗ Произошла ошибка при удалении записей: {e}",
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "shadow_bans_export"), IsAdminFilter())
async def handle_shadow_bans_export(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_shadow_bans_kb()
    try:
        result = await session.execute(
            select(ManualBan.tg_id, ManualBan.banned_at, ManualBan.banned_by, ManualBan.until)
            .where(ManualBan.reason == "shadow")
        )
        rows = result.all()

        csv_output = io.StringIO()
        writer = csv.writer(csv_output)
        writer.writerow(["tg_id", "banned_at", "banned_by", "until"])

        for user in rows:
            writer.writerow([user.tg_id, user.banned_at, user.banned_by, user.until])

        csv_output.seek(0)
        document = BufferedInputFile(file=csv_output.getvalue().encode("utf-8"), filename="shadow_bans.csv")

        await callback_query.message.answer_document(
            document=document,
            caption="📥 Экспорт теневых банов (CSV)",
        )
    except Exception as e:
        await callback_query.message.answer(
            text=f"❗ Ошибка при экспорте: {e}",
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "manual_bans_export"), IsAdminFilter())
async def handle_manual_bans_export(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_manual_bans_kb()
    try:
        result = await session.execute(
            select(ManualBan.tg_id, ManualBan.banned_at, ManualBan.reason, ManualBan.until, ManualBan.banned_by)
            .where(or_(ManualBan.reason != "shadow", ManualBan.reason.is_(None)))
        )
        rows = result.all()

        csv_output = io.StringIO()
        writer = csv.writer(csv_output)
        writer.writerow(["tg_id", "banned_at", "reason", "until", "banned_by"])

        for user in rows:
            writer.writerow([user.tg_id, user.banned_at, user.reason, user.until, user.banned_by])

        csv_output.seek(0)
        document = BufferedInputFile(file=csv_output.getvalue().encode("utf-8"), filename="manual_bans.csv")

        await callback_query.message.answer_document(
            document=document,
            caption="📥 Экспорт вручную забаненных пользователей (CSV)",
        )
    except Exception as e:
        await callback_query.message.answer(
            text=f"❗ Ошибка при экспорте: {e}",
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_clear_blocked"), IsAdminFilter())
async def handle_clear_blocked_users(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_blocked_users_kb()
    try:
        count_result = await session.execute(select(func.count()).select_from(BlockedUser))
        total_count = count_result.scalar() or 0
        
        if total_count == 0:
            await callback_query.message.answer(
                text="📂 Нет забанивших пользователей для очистки.",
                reply_markup=kb,
            )
            return

        await session.execute(delete(BlockedUser))
        await session.commit()
        
        await callback_query.message.answer(
            text=f"🗑️ Очищено {total_count} записей забанивших пользователей из базы данных.",
            reply_markup=kb,
        )
        logger.info(f"[BANS] Очищено {total_count} записей из blocked_users")
    except Exception as e:
        logger.error(f"[BANS] Ошибка при очистке blocked_users: {e}")
        await callback_query.message.answer(
            text=f"❗ Ошибка при очистке забанивших пользователей: {e}",
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_clear_shadow"), IsAdminFilter())
async def handle_clear_shadow_bans(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_shadow_bans_kb()
    try:
        count_result = await session.execute(
            select(func.count()).select_from(ManualBan).where(ManualBan.reason == "shadow")
        )
        total_count = count_result.scalar() or 0
        
        if total_count == 0:
            await callback_query.message.answer(
                text="📂 Нет теневых банов для очистки.",
                reply_markup=kb,
            )
            return

        await session.execute(delete(ManualBan).where(ManualBan.reason == "shadow"))
        await session.commit()
        
        await callback_query.message.answer(
            text=f"🗑️ Очищено {total_count} записей теневых банов из базы данных.",
            reply_markup=kb,
        )
        logger.info(f"[BANS] Очищено {total_count} записей теневых банов из manual_bans")
    except Exception as e:
        logger.error(f"[BANS] Ошибка при очистке теневых банов: {e}")
        await callback_query.message.answer(
            text=f"❗ Ошибка при очистке теневых банов: {e}",
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_clear_manual"), IsAdminFilter())
async def handle_clear_manual_bans(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_manual_bans_kb()
    try:
        count_result = await session.execute(
            select(func.count()).select_from(ManualBan).where(
                or_(ManualBan.reason != "shadow", ManualBan.reason.is_(None))
            )
        )
        total_count = count_result.scalar() or 0
        
        if total_count == 0:
            await callback_query.message.answer(
                text="📂 Нет ручных банов для очистки.",
                reply_markup=kb,
            )
            return

        await session.execute(
            delete(ManualBan).where(
                or_(ManualBan.reason != "shadow", ManualBan.reason.is_(None))
            )
        )
        await session.commit()
        
        await callback_query.message.answer(
            text=f"🗑️ Очищено {total_count} записей ручных банов из базы данных.",
            reply_markup=kb,
        )
        logger.info(f"[BANS] Очищено {total_count} записей ручных банов из manual_bans")
    except Exception as e:
        logger.error(f"[BANS] Ошибка при очистке ручных банов: {e}")
        await callback_query.message.answer(
            text=f"❗ Ошибка при очистке ручных банов: {e}",
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_preemptive"), IsAdminFilter())
async def handle_preemptive_ban_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PreemptiveBanStates.waiting_for_preemptive_ids)

    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ Отмена",
        callback_data=AdminPanelCallback(action="bans_cancel_preemptive").pack(),
    )
    await callback.message.edit_text(
        "📥 Отправьте список Telegram ID (один на строке), которых нужно заранее забанить (теневой бан).\n\n"
        "Пример:\n<code>123456789\n987654321</code>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_cancel_preemptive"), IsAdminFilter())
async def handle_cancel_preemptive_ban(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        text=get_shadow_bans_menu_text(),
        reply_markup=build_shadow_bans_kb()
    )


@router.message(PreemptiveBanStates.waiting_for_preemptive_ids, IsAdminFilter())
async def handle_preemptive_ids_input(message: Message, state: FSMContext, session: AsyncSession):
    lines = message.text.strip().splitlines()
    tg_ids = set()

    for line in lines:
        line = line.strip()
        if line.isdigit():
            tg_ids.add(int(line))

    if not tg_ids:
        await message.answer("❌ Не найдено ни одного корректного Telegram ID.")
        return

    now = datetime.now(timezone.utc)

    stmt = (
        pg_insert(ManualBan)
        .values([
            {
                "tg_id": tg_id,
                "reason": "shadow",
                "banned_by": message.from_user.id,
                "until": None,
                "banned_at": now,
            }
            for tg_id in tg_ids
        ])
        .on_conflict_do_update(
            index_elements=[ManualBan.tg_id],
            set_={
                "reason": "shadow",
                "until": None,
                "banned_by": message.from_user.id,
                "banned_at": now,
            },
        )
    )

    await session.execute(stmt)
    await session.commit()

    await message.answer(
        f"✅ Успешно добавлено в теневой бан: <b>{len(tg_ids)}</b> пользователей.",
        reply_markup=build_shadow_bans_kb(),
    )
    await state.clear()
