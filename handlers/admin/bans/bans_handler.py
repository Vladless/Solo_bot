from typing import Any

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from database import delete_user_data
from filters.admin import IsAdminFilter

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb
from .keyboard import build_bans_kb
import csv
import io


router = Router()


@router.callback_query(AdminPanelCallback.filter(F.action == "bans"), IsAdminFilter())
async def handle_bans(callback_query: CallbackQuery):
    text = (
        "🚫 <b>Управление банами</b>\n\n"
        "📛 <b>Забанившие бота</b> — пользователи, которые заблокировали бота вручную.\n"
        "🔒 <b>Ручной бан</b> — пользователи, которых вы забанили через админку.\n\n"
        "⬇ Выберите нужный раздел:"
    )

    await callback_query.message.edit_text(
        text=text,
        reply_markup=build_bans_kb(),
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "bans_export"),
    IsAdminFilter(),
)
async def handle_bans_export(callback_query: CallbackQuery, session: Any):
    kb = build_admin_back_kb("management")

    try:
        banned_users = await session.fetch("SELECT tg_id, blocked_at FROM blocked_users")
        csv_output = io.StringIO()
        writer = csv.writer(csv_output)
        writer.writerow(["tg_id", "blocked_at"])
        for user in banned_users:
            writer.writerow([user["tg_id"], user["blocked_at"]])

        csv_output.seek(0)

        document = BufferedInputFile(file=csv_output.getvalue().encode("utf-8"), filename="banned_users.csv")

        await callback_query.message.answer_document(
            document=document,
            caption="📥 Экспорт пользователей, заблокировавших бота в CSV",
        )
    except Exception as e:
        await callback_query.message.answer(
            text=f"❗ Произошла ошибка при экспорте: {e}",
            reply_markup=kb,
        )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "bans_delete_banned"),
    IsAdminFilter(),
)
async def handle_bans_delete_banned(callback_query: CallbackQuery, session: Any):
    kb = build_admin_back_kb("bans")

    try:
        blocked_users = await session.fetch("SELECT tg_id FROM blocked_users")
        blocked_ids = [record["tg_id"] for record in blocked_users]

        if not blocked_ids:
            await callback_query.message.answer(
                text="📂 Нет заблокировавших пользователей для удаления.",
                reply_markup=kb,
            )
            return

        for tg_id in blocked_ids:
            await delete_user_data(session, tg_id)

        await session.execute("DELETE FROM blocked_users WHERE tg_id = ANY($1)", blocked_ids)

        await callback_query.message.answer(
            text=f"🗑️ Удалены данные о {len(blocked_ids)} пользователях и связанных записях.",
            reply_markup=kb,
        )
    except Exception as e:
        await callback_query.message.answer(
            text=f"❗ Произошла ошибка при удалении записей: {e}",
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "manual_bans_export"), IsAdminFilter())
async def handle_manual_bans_export(callback_query: CallbackQuery, session: Any):
    try:
        rows = await session.fetch("SELECT tg_id, banned_at, reason, until FROM manual_bans")

        import csv
        import io

        csv_output = io.StringIO()
        writer = csv.writer(csv_output)
        writer.writerow(["tg_id", "banned_at", "reason", "until"])
        for user in rows:
            writer.writerow([user["tg_id"], user["banned_at"], user["reason"], user["until"]])

        csv_output.seek(0)

        document = BufferedInputFile(file=csv_output.getvalue().encode("utf-8"), filename="manual_bans.csv")

        await callback_query.message.answer_document(
            document=document,
            caption="📥 Экспорт вручную забаненных пользователей",
        )
    except Exception as e:
        await callback_query.message.answer(
            text=f"❗ Ошибка при экспорте: {e}",
            reply_markup=build_admin_back_kb("bans"),
        )
