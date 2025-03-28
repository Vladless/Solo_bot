from typing import Any

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from database import delete_user_data
from filters.admin import IsAdminFilter

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb
from .keyboard import build_bans_kb


router = Router()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "bans"),
    IsAdminFilter(),
)
async def handle_bans(callback_query: CallbackQuery):
    text = "🚫 Заблокировавшие бота\n\nЗдесь можно просматривать и удалять пользователей, которые забанили вашего бота!"

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

        import csv
        import io

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
