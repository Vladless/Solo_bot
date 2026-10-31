from pathlib import Path
from tempfile import NamedTemporaryFile

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import UserId
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
                "Поддерживается SQLite с клиентами в <code>clients</code> или <code>inbounds</code>.",
                "Нужен Telegram ID владельца. Отключённые клиенты, отложенная активация и лимиты не в целых ГБ пропускаются.",
                "После импорта перевыпустите импортированные подписки кнопкой ниже.",
            ),
        ),
        reply_markup=build_back_to_db_menu(),
    )
    await state.clear()
    await state.set_state(Import3xuiStates.waiting_for_file)


@router.message(Import3xuiStates.waiting_for_file, F.document, HasPermission(PERM_MANAGEMENT))
async def handle_3xui_db_upload(message: Message, state: FSMContext, session: AsyncSession):
    file = message.document

    if not str(file.file_name or "").lower().endswith(".db"):
        await message.reply(menu_text("Импорт из 3x-ui", "❌ пришли файл с расширением .db"))
        return

    processing_message = await message.reply(menu_text("Импорт из 3x-ui", "📥 Файл получен. Начинаю восстановление..."))

    with NamedTemporaryFile(prefix="solo-3xui-", suffix=".db", delete=False) as uploaded:
        file_path = uploaded.name

    try:
        from database.importer import import_keys_from_3xui_db

        await message.bot.download(file, destination=file_path)
        imported, skipped, imported_emails = await import_keys_from_3xui_db(file_path, session)
        await session.flush()
        await state.update_data(imported_3xui_emails=imported_emails)

        await processing_message.edit_text(
            menu_text(
                "Импорт из 3x-ui",
                "✅ Импорт завершён.",
                section("🔐 Подписки", f"Добавлено: {imported}", f"Пропущено: {skipped}"),
            ),
            reply_markup=build_post_import_kb(),
        )

    except Exception as e:
        await session.rollback()
        await state.clear()
        logger.error(f"[Import 3x-ui] Ошибка: {e}")
        await processing_message.edit_text(
            menu_text(
                "Импорт из 3x-ui", "❌ Не удалось импортировать. Убедись, что это валидный файл <code>x-ui.db</code>"
            ),
            reply_markup=build_back_to_db_menu(),
        )

    finally:
        Path(file_path).unlink(missing_ok=True)

    await state.set_state(None)


@router.callback_query(AdminPanelCallback.filter(F.action == "resync_after_import"), HasPermission(PERM_MANAGEMENT))
async def handle_resync_after_import(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    imported_emails = (await state.get_data()).get("imported_3xui_emails") or []
    if not imported_emails:
        await callback.answer("Нет импортированных подписок для перевыпуска.", show_alert=True)
        return
    await callback.answer("Начинаю перевыпуск импортированных подписок...")

    result = await session.execute(
        select(Key.user_id, Key.email)
        .select_from(Key)
        .join(User, Key.user_id == User.id)
        .where(Key.email.in_(imported_emails), Key.key == "", Key.tariff_id.is_(None))
    )
    keys = result.all()

    success = 0
    failed = 0

    for user_id, email in keys:
        try:
            await update_subscription(user_id=UserId(user_id), email=email, session=session)
            restored_link = await session.scalar(select(Key.key).where(Key.user_id == user_id, Key.email == email))
            if not restored_link:
                raise ValueError("Подписка не восстановлена на панели")
            success += 1
        except Exception as e:
            logger.error(f"[Resync] Ошибка при перевыпуске {email}: {e}")
            failed += 1

    if not failed:
        await state.clear()

    await callback.message.edit_text(
        menu_text(
            "Импорт из 3x-ui",
            "✅ Перевыпуск завершён.",
            section("🔄 Итог", f"Удачно: {success}", f"С ошибкой: {failed}"),
        ),
        reply_markup=build_back_to_db_menu(),
    )
