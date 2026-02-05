import os

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from filters.admin import IsAdminFilter
from logger import logger

from ..panel.keyboard import build_admin_back_kb
from . import router
from .keyboard import AdminPanelCallback


class FileUploadState(StatesGroup):
    waiting_for_target = State()
    waiting_for_file = State()


@router.callback_query(AdminPanelCallback.filter(F.action == "upload_file"), IsAdminFilter())
async def prompt_for_file_upload(callback: CallbackQuery, state: FSMContext):
    text = (
        "📤 <b>Загрузка файла</b>\n\n"
        "Вы можете заменить файл в корневой директории бота или в папке <code>handlers</code>.\n\n"
        "📁 Выберите директорию, а затем отправьте файл с таким же именем и расширением, "
        "как у уже существующего файла. Он будет автоматически заменён."
    )

    back_kb = build_admin_back_kb("management")
    kb = InlineKeyboardBuilder()
    kb.button(text="📁 Корень бота", callback_data="upload_target:root")
    kb.button(text="📂 Папка handlers", callback_data="upload_target:handlers")
    for row in back_kb.inline_keyboard:
        kb.row(*row)

    await callback.message.edit_text(
        text,
        reply_markup=kb.as_markup(),
    )
    await state.set_state(FileUploadState.waiting_for_target)


@router.callback_query(F.data.startswith("upload_target:"), FileUploadState.waiting_for_target, IsAdminFilter())
async def select_upload_target(callback: CallbackQuery, state: FSMContext):
    target = callback.data.split(":", 1)[1]
    if target not in {"root", "handlers"}:
        await callback.answer("Неизвестная директория.")
        return

    await state.update_data(upload_target=target)

    target_text = "Корень бота" if target == "root" else "Папка handlers"
    await callback.message.edit_text(
        "📤 <b>Загрузка файла</b>\n\n"
        f"Выбрана директория: <b>{target_text}</b>.\n\n"
        "Теперь отправьте файл с таким же именем и расширением, как у уже существующего файла. "
        "Он будет автоматически заменён.",
        reply_markup=build_admin_back_kb("management"),
    )
    await state.set_state(FileUploadState.waiting_for_file)


@router.message(FileUploadState.waiting_for_file, F.document, IsAdminFilter())
async def handle_admin_file_upload(message: Message, state: FSMContext):
    document = message.document
    file_name = document.file_name

    if not file_name or "." not in file_name:
        await message.answer("❌ У файла должно быть имя с расширением.")
        return

    data = await state.get_data()
    target = data.get("upload_target", "root")

    if target == "handlers":
        base_dir = os.path.abspath("./handlers")
    else:
        base_dir = os.path.abspath(".")

    os.makedirs(base_dir, exist_ok=True)
    dest_path = os.path.join(base_dir, file_name)

    try:
        await message.bot.download(document, destination=dest_path)

        back_kb = build_admin_back_kb("management")
        kb = InlineKeyboardBuilder()
        kb.button(
            text="🔁 Перезагрузить бота",
            callback_data=AdminPanelCallback(action="restart").pack(),
        )
        for row in back_kb.inline_keyboard:
            kb.row(*row)

        await message.answer(
            f"✅ Файл <code>{file_name}</code> успешно загружен и заменён в директории <code>{target}</code>.\n\n"
            "🔄 <b>Перезагрузите бота, чтобы изменения вступили в силу.</b>",
            reply_markup=kb.as_markup(),
        )
    except Exception as e:
        logger.error(f"[Upload File] Ошибка при загрузке файла {file_name}: {e}")
        await message.answer(
            f"❌ Не удалось сохранить файл: {e}",
            reply_markup=build_admin_back_kb("management"),
        )
    await state.clear()
