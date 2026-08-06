from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Key, User
from filters.admin import HasPermission
from filters.permissions import PERM_MANAGEMENT
from logger import logger
from services.operations import update_subscription

from ..panel.headers import menu_text, quote, section
from . import router
from .keyboard import AdminPanelCallback, build_back_to_db_menu, build_post_import_kb


class Import3xuiStates(StatesGroup):
    waiting_for_file = State()


@router.callback_query(AdminPanelCallback.filter(F.action == "request_3xui_file"), HasPermission(PERM_MANAGEMENT))
async def prompt_for_3xui_file(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        menu_text(
            "Импорт из 3x-ui",
            "Пришлите файл <code>x-ui.db</code>.",
            quote(
                "Это SQLite с таблицей <code>inbounds</code>.",
                "⚠️ У всех подписок в панели должен быть прописан <code>telegram_id</code>.",
                "После импорта обязательно синхронизируйте серверы.",
            ),
            markup=build_back_to_db_menu(),
        ),
        reply_markup=build_back_to_db_menu(),
    )
    await state.set_state(Import3xuiStates.waiting_for_file)


@router.message(Import3xuiStates.waiting_for_file, F.document, HasPermission(PERM_MANAGEMENT))
async def handle_3xui_db_upload(message: Message, state: FSMContext, session: AsyncSession):
    file = message.document

    if not file.file_name.endswith(".db"):
        await message.reply(menu_text("Импорт из 3x-ui", "❌ пришли файл с расширением .db"))
        return

    file_path = f"/tmp/{file.file_name}"
    await message.bot.download(file, destination=file_path)

    processing_message = await message.reply(menu_text("Импорт из 3x-ui", "📥 Файл получен. Начинаю восстановление..."))

    try:
        from database.importer import import_keys_from_3xui_db

        imported, skipped = await import_keys_from_3xui_db(file_path, session)

        await processing_message.edit_text(
            menu_text(
                "Импорт из 3x-ui",
                "✅ Импорт завершён.",
                section("🔐 Подписки", f"Добавлено: {imported}", f"Пропущено: {skipped}"),
                markup=build_post_import_kb(),
            ),
            reply_markup=build_post_import_kb(),
        )

    except Exception as e:
        logger.error(f"[Import 3x-ui] Ошибка: {e}")
        await processing_message.edit_text(
            menu_text(
                "Импорт из 3x-ui",
                "❌ Не удалось импортировать. Убедись, что это валидный файл <code>x-ui.db</code>",
                markup=build_back_to_db_menu(),
            ),
            reply_markup=build_back_to_db_menu(),
        )

    await state.clear()


@router.callback_query(AdminPanelCallback.filter(F.action == "resync_after_import"), HasPermission(PERM_MANAGEMENT))
async def handle_resync_after_import(callback: CallbackQuery, session: AsyncSession):
    await callback.answer(menu_text("Импорт из 3x-ui", "🔁 Начинаю перевыпуск подписок..."))

    result = await session.execute(
        select(User.tg_id, Key.email).select_from(Key).join(User, Key.user_id == User.id).where(User.tg_id.isnot(None))
    )
    keys = result.all()

    success = 0
    failed = 0

    for tg_id, email in keys:
        try:
            await update_subscription(tg_id=tg_id, email=email, session=session)
            success += 1
        except Exception as e:
            logger.error(f"[Resync] Ошибка при перевыпуске {email}: {e}")
            failed += 1

    await callback.message.edit_text(
        menu_text(
            "Импорт из 3x-ui",
            "✅ Перевыпуск завершён.",
            section("🔄 Итог", f"Удачно: {success}", f"С ошибкой: {failed}"),
            markup=build_back_to_db_menu(),
        ),
        reply_markup=build_back_to_db_menu(),
    )
