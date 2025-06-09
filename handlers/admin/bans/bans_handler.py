import csv
import io

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from database import delete_user_data
from database.models import ManualBan
from filters.admin import IsAdminFilter
from logger import logger

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb
from .keyboard import build_bans_kb

router = Router()


@router.callback_query(AdminPanelCallback.filter(F.action == "bans"), IsAdminFilter())
async def handle_bans(callback_query: CallbackQuery):
    text_ = (
        "🚫 <b>Управление банами</b>\n\n"
        "📛 <b>Забанившие бота</b> — пользователи, которые заблокировали бота вручную.\n"
        "🔒 <b>Ручной бан</b> — пользователи, которых вы забанили через админку.\n\n"
        "⬇ Выберите нужный раздел:"
    )
    await callback_query.message.edit_text(text=text_, reply_markup=build_bans_kb())


@router.callback_query(
    AdminPanelCallback.filter(F.action == "bans_export"), IsAdminFilter()
)
async def handle_bans_export(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_admin_back_kb("management")
    try:
        result = await session.execute(text("SELECT tg_id FROM blocked_users"))
        banned_users = result.all()

        csv_output = io.StringIO()
        writer = csv.writer(csv_output)
        writer.writerow(["tg_id"])

        for user in banned_users:
            writer.writerow([user.tg_id])

        csv_output.seek(0)
        document = BufferedInputFile(
            file=csv_output.getvalue().encode("utf-8"), filename="banned_users.csv"
        )

        await callback_query.message.answer_document(
            document=document,
            caption="📥 Экспорт пользователей, заблокировавших бота (CSV)",
        )
    except Exception as e:
        await callback_query.message.answer(
            text=f"❗ Произошла ошибка при экспорте: {e}",
            reply_markup=kb,
        )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "bans_delete_banned"), IsAdminFilter()
)
async def handle_bans_delete_banned(
    callback_query: CallbackQuery, session: AsyncSession
):
    kb = build_admin_back_kb("bans")
    try:
        result = await session.execute(text("SELECT tg_id FROM blocked_users"))
        blocked_users = result.all()
        blocked_ids = [user.tg_id for user in blocked_users]

        if not blocked_ids:
            await callback_query.message.answer(
                text="📂 Нет заблокировавших пользователей для удаления.",
                reply_markup=kb,
            )
            return

        for tg_id in blocked_ids:
            await delete_user_data(session, tg_id)

        await session.execute(
            text("DELETE FROM blocked_users WHERE tg_id = ANY(:blocked_ids)"),
            {"blocked_ids": blocked_ids},
        )
        await session.commit()

        await callback_query.message.answer(
            text=f"🗑️ Удалены данные о {len(blocked_ids)} пользователях и связанных записях.",
            reply_markup=kb,
        )
    except Exception as e:
        await callback_query.message.answer(
            text=f"❗ Произошла ошибка при удалении записей: {e}",
            reply_markup=kb,
        )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "manual_bans_export"), IsAdminFilter()
)
async def handle_manual_bans_export(
    callback_query: CallbackQuery, session: AsyncSession
):
    build_admin_back_kb("bans")
    try:
        result = await session.execute(
            text("SELECT tg_id, banned_at, reason, until FROM manual_bans")
        )
        rows = result.all()

        csv_output = io.StringIO()
        writer = csv.writer(csv_output)
        writer.writerow(["tg_id", "banned_at", "reason", "until"])

        for user in rows:
            writer.writerow([user.tg_id, user.banned_at, user.reason, user.until])

        csv_output.seek(0)
        document = BufferedInputFile(
            file=csv_output.getvalue().encode("utf-8"), filename="manual_bans.csv"
        )

        await callback_query.message.answer_document(
            document=document,
            caption="📥 Экспорт вручную забаненных пользователей",
        )
    except Exception as e:
        await callback_query.message.answer(
            text=f"❗ Ошибка при экспорте: {e}",
            reply_markup=build_admin_back_kb("bans"),
        )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "bans_delete_manual"), IsAdminFilter()
)
async def handle_delete_manual_banned(
    callback_query: CallbackQuery, session: AsyncSession
):
    try:
        await session.execute(delete(ManualBan))
        await session.commit()
        await callback_query.message.edit_text(
            "🗑️ Вручную забаненные пользователи удалены.",
            reply_markup=build_bans_kb(),
        )
        logger.info("[BANS] Очищены записи из manual_bans")
    except Exception as e:
        logger.error(f"[BANS] Ошибка при очистке manual_bans: {e}")
        await callback_query.message.edit_text(
            "❌ Ошибка при удалении вручную забаненных пользователей."
        )
