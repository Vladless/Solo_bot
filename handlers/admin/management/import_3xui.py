from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Key
from handlers.keys.operations import update_subscription
from logger import logger

from . import router
from .keyboard import AdminPanelCallback, build_back_to_db_menu, build_post_import_kb


class Import3xuiStates(StatesGroup):
    waiting_for_file = State()


@router.callback_query(AdminPanelCallback.filter(F.action == "request_3xui_file"))
async def prompt_for_3xui_file(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📂 Пришлите файл базы данных <code>x-ui.db</code> для восстановления подписок и клиентов.\n\n"
        "Формат: SQLite-файл с таблицей <code>inbounds</code>.\n\n"
        "<b>⚠️ Важно!</b> Убедитесь, что у всех подписок в панели прописан <code>telegram_id</code>.\n"
        "После восстановления обязательно выполните <b>синхронизацию</b> с текущими серверами!",
        reply_markup=build_back_to_db_menu(),
    )
    await state.set_state(Import3xuiStates.waiting_for_file)


@router.message(Import3xuiStates.waiting_for_file, F.document)
async def handle_3xui_db_upload(message: Message, state: FSMContext, session: AsyncSession):
    file = message.document

    if not file.file_name.endswith(".db"):
        await message.reply("❌ Пожалуйста, пришли файл с расширением .db")
        return

    file_path = f"/tmp/{file.file_name}"
    await message.bot.download(file, destination=file_path)

    processing_message = await message.reply("📥 Файл получен. Начинаю восстановление...")

    try:
        from database.importer import import_keys_from_3xui_db

        imported, skipped = await import_keys_from_3xui_db(file_path, session)

        await processing_message.edit_text(
            f"✅ Восстановление завершено:\n"
            f"🔐 Импортировано подписок: <b>{imported}</b>\n"
            f"⏭ Пропущено (уже есть): <b>{skipped}</b>",
            reply_markup=build_post_import_kb(),
        )

    except Exception as e:
        logger.error(f"[Import 3x-ui] Ошибка: {e}")
        await processing_message.edit_text(
            "❌ Произошла ошибка при импорте. Убедись, что это валидный файл <code>x-ui.db</code>",
            reply_markup=build_back_to_db_menu(),
        )

    await state.clear()


@router.callback_query(AdminPanelCallback.filter(F.action == "resync_after_import"))
async def handle_resync_after_import(callback: CallbackQuery, session: AsyncSession):
    await callback.answer("🔁 Начинаю перевыпуск подписок...")

    result = await session.execute(select(Key.tg_id, Key.email))
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
        f"🔁 Перевыпуск завершён:\n✅ Успешно: <b>{success}</b>\n❌ Ошибки: <b>{failed}</b>",
        reply_markup=build_back_to_db_menu(),
    )
