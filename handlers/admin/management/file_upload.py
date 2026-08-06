import os

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.executor import run_io
from filters.admin import HasPermission
from filters.permissions import PERM_MANAGEMENT
from logger import logger

from ..panel.headers import menu_text, quote
from ..panel.keyboard import build_admin_back_kb
from . import router
from .keyboard import AdminPanelCallback


class FileUploadState(StatesGroup):
    waiting_for_target = State()
    waiting_for_file = State()


@router.callback_query(AdminPanelCallback.filter(F.action == "upload_file"), HasPermission(PERM_MANAGEMENT))
async def prompt_for_file_upload(callback: CallbackQuery, state: FSMContext):
    text = menu_text(
        "Загрузка файла",
        "Выберите директорию.",
        quote(
            "Заменить можно файл в корне бота или в папке <code>handlers</code>.",
            "Дальше пришлите файл с тем же именем и расширением — он заменит существующий.",
        ),
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


@router.callback_query(
    F.data.startswith("upload_target:"), FileUploadState.waiting_for_target, HasPermission(PERM_MANAGEMENT)
)
async def select_upload_target(callback: CallbackQuery, state: FSMContext):
    target = callback.data.split(":", 1)[1]
    if target not in {"root", "handlers"}:
        await callback.answer(menu_text("Загрузка файла", "Неизвестная директория."))
        return

    await state.update_data(upload_target=target)

    target_text = "Корень бота" if target == "root" else "Папка handlers"
    await callback.message.edit_text(
        menu_text(
            "Загрузка файла",
            f"Директория: <b>{target_text}</b>",
            quote("Пришлите файл с тем же именем и расширением — он заменит существующий."),
            markup=build_admin_back_kb("management"),
        ),
        reply_markup=build_admin_back_kb("management"),
    )
    await state.set_state(FileUploadState.waiting_for_file)


@router.message(FileUploadState.waiting_for_file, F.document, HasPermission(PERM_MANAGEMENT))
async def handle_admin_file_upload(message: Message, state: FSMContext):
    document = message.document
    file_name = document.file_name

    if not file_name or "." not in file_name:
        await message.answer(menu_text("Загрузка файла", "❌ У файла должно быть имя с расширением."))
        return

    data = await state.get_data()
    target = data.get("upload_target", "root")

    if target == "handlers":
        base_dir = os.path.abspath("./handlers")
    else:
        base_dir = os.path.abspath(".")

    await run_io(lambda: os.makedirs(base_dir, exist_ok=True))
    safe_name = os.path.basename(file_name)
    dest_path = os.path.join(base_dir, safe_name)
    if not os.path.abspath(dest_path).startswith(base_dir + os.sep):
        await message.answer(menu_text("Загрузка файла", "❌ Недопустимое имя файла."))
        return

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
            menu_text(
                "Загрузка файла",
                f"✅ Файл <code>{file_name}</code> загружен и заменён в директории <code>{target}</code>.",
                quote("🔄 <b>Перезагрузите бота, чтобы изменения вступили в силу.</b>"),
                markup=kb.as_markup(),
            ),
            reply_markup=kb.as_markup(),
        )
    except Exception as e:
        logger.error(f"[Upload File] Ошибка при загрузке файла {file_name}: {e}")
        await message.answer(
            menu_text("Загрузка файла", f"❌ Не удалось сохранить файл: {e}", markup=build_admin_back_kb("management")),
            reply_markup=build_admin_back_kb("management"),
        )
    await state.clear()
