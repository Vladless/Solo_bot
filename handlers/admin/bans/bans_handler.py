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
from database.access.resolution import resolve_user_optional
from database.models import BlockedUser, Key, ManualBan, User
from database.users import add_user
from filters.admin import HasPermission, IsAdminFilter
from filters.permissions import PERM_MANAGEMENT
from logger import logger
from middlewares.ban_checker import invalidate_ban_cache

from ..panel.headers import menu_text, quote, section, wrap_text
from ..panel.keyboard import AdminPanelCallback
from .keyboard import (
    build_bans_kb,
    build_blocked_users_kb,
    build_manual_bans_kb,
    build_shadow_bans_kb,
)


router = Router()
router.callback_query.filter(HasPermission(PERM_MANAGEMENT))
router.message.filter(HasPermission(PERM_MANAGEMENT))


class PreemptiveBanStates(StatesGroup):
    waiting_for_preemptive_ids = State()


@router.callback_query(AdminPanelCallback.filter(F.action == "bans"), IsAdminFilter())
async def handle_bans(callback_query: CallbackQuery):
    text_ = menu_text(
        "Баны",
        "Кто и как отрезан от бота.",
        section(
            "📋 Виды",
            "Забанившие: закрыли бота сами",
            "Теневые: бот их игнорирует",
            "Ручные: забанены из админки",
        ),
    )
    await callback_query.message.edit_text(text=text_, reply_markup=build_bans_kb())


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_blocked_menu"), IsAdminFilter())
async def handle_blocked_users_menu(callback_query: CallbackQuery):
    text_ = menu_text(
        "Забанившие бота",
        "Выберите действие.",
        quote("Клиенты, которые заблокировали бота сами или удалили чат."),
    )
    await callback_query.message.edit_text(text=text_, reply_markup=build_blocked_users_kb())


def get_shadow_bans_menu_text() -> str:
    return menu_text(
        "Теневые баны",
        "Выберите действие.",
        quote("Бот молча игнорирует таких клиентов — уведомления о бане они не получают."),
        quote(
            "Добавить можно сразу пачкой: пришлите список Telegram ID, "
            "по одному в строке.\n<code>123456789\n987654321</code>"
        ),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_shadow_menu"), IsAdminFilter())
async def handle_shadow_bans_menu(callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        text=wrap_text(get_shadow_bans_menu_text()), reply_markup=build_shadow_bans_kb()
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_manual_menu"), IsAdminFilter())
async def handle_manual_bans_menu(callback_query: CallbackQuery):
    text_ = menu_text(
        "Ручные баны",
        "Выберите действие.",
        quote("Клиенты, которых вы забанили из админки."),
    )
    await callback_query.message.edit_text(text=text_, reply_markup=build_manual_bans_kb())


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_export"), IsAdminFilter())
async def handle_bans_export(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_blocked_users_kb()
    try:
        result = await session.execute(
            select(User.tg_id).join(BlockedUser, BlockedUser.user_id == User.id).where(User.tg_id.isnot(None))
        )
        banned_users = [row[0] for row in result.all()]

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
            text=menu_text("Баны", f"❌ Не удалось выгрузить: {e}", markup=kb),
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_delete_banned"), IsAdminFilter())
async def handle_bans_delete_banned(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_blocked_users_kb()
    try:
        stmt = (
            select(BlockedUser.user_id).outerjoin(Key, BlockedUser.user_id == Key.user_id).where(Key.user_id.is_(None))
        )
        result = await session.execute(stmt)
        blocked_ids = [row[0] for row in result.all()]

        if not blocked_ids:
            await callback_query.message.answer(
                text=menu_text("Баны", "Никто не блокировал бота.", markup=kb),
                reply_markup=kb,
            )
            return

        for tg_id in blocked_ids:
            await delete_user_data(session, tg_id)

        await callback_query.message.answer(
            text=menu_text("Баны", f"🗑️ Удалены данные о {len(blocked_ids)} клиентах и связанных записях.", markup=kb),
            reply_markup=kb,
        )
    except Exception as e:
        await callback_query.message.answer(
            text=menu_text("Баны", f"❌ Не удалось удалить записи: {e}", markup=kb),
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "shadow_bans_export"), IsAdminFilter())
async def handle_shadow_bans_export(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_shadow_bans_kb()
    try:
        result = await session.execute(
            select(User.tg_id, ManualBan.user_id, ManualBan.banned_at, ManualBan.banned_by, ManualBan.until)
            .select_from(ManualBan)
            .join(User, ManualBan.user_id == User.id)
            .where(ManualBan.reason == "shadow")
        )
        rows = result.all()

        csv_output = io.StringIO()
        writer = csv.writer(csv_output)
        writer.writerow(["tg_id", "banned_at", "banned_by", "until"])

        for row in rows:
            display_id = row.tg_id if row.tg_id is not None else row.user_id
            writer.writerow([display_id, row.banned_at, row.banned_by, row.until])

        csv_output.seek(0)
        document = BufferedInputFile(file=csv_output.getvalue().encode("utf-8"), filename="shadow_bans.csv")

        await callback_query.message.answer_document(
            document=document,
            caption="📥 Экспорт теневых банов (CSV)",
        )
    except Exception as e:
        await callback_query.message.answer(
            text=menu_text("Баны", f"❌ Ошибка при экспорте: {e}", markup=kb),
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "manual_bans_export"), IsAdminFilter())
async def handle_manual_bans_export(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_manual_bans_kb()
    try:
        result = await session.execute(
            select(
                User.tg_id,
                ManualBan.user_id,
                ManualBan.banned_at,
                ManualBan.reason,
                ManualBan.until,
                ManualBan.banned_by,
            )
            .select_from(ManualBan)
            .join(User, ManualBan.user_id == User.id)
            .where(or_(ManualBan.reason != "shadow", ManualBan.reason.is_(None)))
        )
        rows = result.all()

        csv_output = io.StringIO()
        writer = csv.writer(csv_output)
        writer.writerow(["tg_id", "banned_at", "reason", "until", "banned_by"])

        for row in rows:
            display_id = row.tg_id if row.tg_id is not None else row.user_id
            writer.writerow([display_id, row.banned_at, row.reason, row.until, row.banned_by])

        csv_output.seek(0)
        document = BufferedInputFile(file=csv_output.getvalue().encode("utf-8"), filename="manual_bans.csv")

        await callback_query.message.answer_document(
            document=document,
            caption="📥 Экспорт вручную забаненных пользователей (CSV)",
        )
    except Exception as e:
        await callback_query.message.answer(
            text=menu_text("Баны", f"❌ Ошибка при экспорте: {e}", markup=kb),
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
                text=menu_text("Баны", "Никто не блокировал бота.", markup=kb),
                reply_markup=kb,
            )
            return

        await session.execute(delete(BlockedUser))

        await callback_query.message.answer(
            text=menu_text("Баны", f"🗑️ Очищено {total_count} записей.", markup=kb),
            reply_markup=kb,
        )
        logger.info(f"[BANS] Очищено {total_count} записей из blocked_users")
    except Exception as e:
        logger.error(f"[BANS] Ошибка при очистке blocked_users: {e}")
        await callback_query.message.answer(
            text=menu_text("Баны", f"❌ Не удалось очистить список: {e}", markup=kb),
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
                text=menu_text("Баны", "📂 Нет теневых банов для очистки.", markup=kb),
                reply_markup=kb,
            )
            return

        tg_ids_result = await session.execute(
            select(User.tg_id)
            .select_from(ManualBan)
            .join(User, ManualBan.user_id == User.id)
            .where(ManualBan.reason == "shadow")
        )
        tg_to_invalidate = [r[0] for r in tg_ids_result.all() if r[0] is not None]
        await session.execute(delete(ManualBan).where(ManualBan.reason == "shadow"))
        for tid in tg_to_invalidate:
            await invalidate_ban_cache(tid)

        await callback_query.message.answer(
            text=menu_text("Баны", f"🗑️ Очищено {total_count} записей теневых банов из базы данных.", markup=kb),
            reply_markup=kb,
        )
        logger.info(f"[BANS] Очищено {total_count} записей теневых банов из manual_bans")
    except Exception as e:
        logger.error(f"[BANS] Ошибка при очистке теневых банов: {e}")
        await callback_query.message.answer(
            text=menu_text("Баны", f"❌ Ошибка при очистке теневых банов: {e}", markup=kb),
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_clear_manual"), IsAdminFilter())
async def handle_clear_manual_bans(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_manual_bans_kb()
    try:
        count_result = await session.execute(
            select(func.count())
            .select_from(ManualBan)
            .where(or_(ManualBan.reason != "shadow", ManualBan.reason.is_(None)))
        )
        total_count = count_result.scalar() or 0

        if total_count == 0:
            await callback_query.message.answer(
                text=menu_text("Баны", "📂 Нет ручных банов для очистки.", markup=kb),
                reply_markup=kb,
            )
            return

        tg_ids_result = await session.execute(
            select(User.tg_id)
            .select_from(ManualBan)
            .join(User, ManualBan.user_id == User.id)
            .where(or_(ManualBan.reason != "shadow", ManualBan.reason.is_(None)))
        )
        tg_to_invalidate = [r[0] for r in tg_ids_result.all() if r[0] is not None]
        await session.execute(delete(ManualBan).where(or_(ManualBan.reason != "shadow", ManualBan.reason.is_(None))))
        for tid in tg_to_invalidate:
            await invalidate_ban_cache(tid)

        await callback_query.message.answer(
            text=menu_text("Баны", f"🗑️ Очищено {total_count} записей ручных банов из базы данных.", markup=kb),
            reply_markup=kb,
        )
        logger.info(f"[BANS] Очищено {total_count} записей ручных банов из manual_bans")
    except Exception as e:
        logger.error(f"[BANS] Ошибка при очистке ручных банов: {e}")
        await callback_query.message.answer(
            text=menu_text("Баны", f"❌ Ошибка при очистке ручных банов: {e}", markup=kb),
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
        menu_text(
            "Теневой бан",
            "Пришлите Telegram ID, по одному в строке.",
            section("📥 Пример", "123456789", "987654321"),
            markup=builder.as_markup(),
        ),
        reply_markup=builder.as_markup(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_cancel_preemptive"), IsAdminFilter())
async def handle_cancel_preemptive_ban(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(text=wrap_text(get_shadow_bans_menu_text()), reply_markup=build_shadow_bans_kb())


@router.message(PreemptiveBanStates.waiting_for_preemptive_ids, IsAdminFilter())
async def handle_preemptive_ids_input(message: Message, state: FSMContext, session: AsyncSession):
    lines = message.text.strip().splitlines()
    tg_ids = set()

    for line in lines:
        line = line.strip()
        if line.isdigit():
            tg_ids.add(int(line))

    if not tg_ids:
        await message.answer(menu_text("Баны", "❌ Не найдено ни одного корректного Telegram ID."))
        return

    now = datetime.now(timezone.utc)

    rows = []
    cache_tg_ids = []
    for raw_tg in tg_ids:
        u = await resolve_user_optional(session, raw_tg)
        if u is None:
            await add_user(session, raw_tg)
            await session.flush()
            u = await resolve_user_optional(session, raw_tg)
        if u is None:
            continue
        rows.append({
            "user_id": u.id,
            "tg_id": u.tg_id,
            "reason": "shadow",
            "banned_by": message.from_user.id,
            "until": None,
            "banned_at": now,
        })
        if u.tg_id is not None:
            cache_tg_ids.append(u.tg_id)

    if not rows:
        await message.answer(menu_text("Баны", "❌ Ни один Telegram ID не совпал с клиентом."))
        await state.clear()
        return

    ins = pg_insert(ManualBan).values(rows)
    stmt = ins.on_conflict_do_update(
        index_elements=[ManualBan.user_id],
        set_={
            "tg_id": ins.excluded.tg_id,
            "reason": "shadow",
            "until": None,
            "banned_by": message.from_user.id,
            "banned_at": now,
        },
    )

    await session.execute(stmt)
    for tid in cache_tg_ids:
        await invalidate_ban_cache(tid)

    await message.answer(
        menu_text(
            "Баны",
            f"✅ Успешно добавлено в теневой бан: <b>{len(rows)}</b> клиентов.",
            markup=build_shadow_bans_kb(),
        ),
        reply_markup=build_shadow_bans_kb(),
    )
    await state.clear()
