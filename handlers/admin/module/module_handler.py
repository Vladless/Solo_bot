import os

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from core.executor import run_io
from filters.admin import HasPermission, IsAdminFilter
from filters.permissions import PERM_MODULES
from handlers.admin.panel.headers import menu_text, quote, section, wrap_text
from handlers.admin.panel.keyboard import AdminPanelCallback
from utils.modules_manager import manager

from .keyboard import build_module_menu_kb, build_modules_kb


router = Router()
router.callback_query.filter(HasPermission(PERM_MODULES))
router.message.filter(HasPermission(PERM_MODULES))


def list_installed_modules() -> list[tuple[str, str | None]]:
    base = "modules"
    if not os.path.isdir(base):
        return []
    items: list[tuple[str, str | None]] = []
    for raw_name in sorted(os.listdir(base)):
        path = os.path.join(base, raw_name)
        if os.path.isdir(path) and not raw_name.startswith("."):
            name = (raw_name or "").strip()
            if not name:
                continue
            ver = None
            vp = os.path.join(path, "VERSION")
            if os.path.isfile(vp):
                try:
                    with open(vp, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                ver = line
                                break
                except Exception:
                    ver = None
            items.append((name, ver))
    return items


@router.callback_query(AdminPanelCallback.filter(F.action == "modules"), IsAdminFilter())
async def handle_modules(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()

    packed = AdminPanelCallback.unpack(callback_query.data)
    page = max(1, packed.page or 1)

    all_items = await run_io(list_installed_modules)
    items = [(n, v) for n, v in all_items if n != "web_admin_panel"]

    per_page = 12
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    chunk = items[start : start + per_page]

    if chunk:

        def fmt(n, v):
            return f"{n} v{v}" if v else n

        text = menu_text(
            "Мои модули",
            f"Установлено: <b>{len(items)}</b>",
            section("📦 Список", *[fmt(n, v) for n, v in chunk]),
        )
    else:
        text = menu_text(
            "Мои модули", "Пока ничего не установлено.", quote("Модули ставятся из магазина в боте автора.")
        )

    markup = build_modules_kb(page, total_pages, chunk)
    try:
        await callback_query.message.edit_text(text=text, reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            try:
                await callback_query.message.edit_reply_markup(reply_markup=None)
                await callback_query.message.edit_text(text=text, reply_markup=markup, disable_web_page_preview=True)
            except TelegramBadRequest:
                pass
        else:
            raise
    finally:
        await callback_query.answer()


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("module_restart__")), IsAdminFilter())
async def handle_module_restart(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()

    packed = AdminPanelCallback.unpack(callback_query.data)
    action = packed.action
    page = packed.page or 1
    name = action.split("module_restart__", 1)[-1]

    try:
        await manager.restart(name)
        result = "✅ Модуль перезапущен."
    except Exception as e:
        result = f"❌ Ошибка перезапуска: {e}"

    items = dict(await run_io(list_installed_modules))
    ver = items.get(name)
    title = f"{name} v{ver}" if ver else name
    text = menu_text("Модули", f"🧩 Модуль: <b>{title}</b>", quote(f"{result}"))

    markup = build_module_menu_kb(name, page)
    try:
        await callback_query.message.edit_text(text=text, reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        raise


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("module_stop__")), IsAdminFilter())
async def handle_module_stop(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()

    packed = AdminPanelCallback.unpack(callback_query.data)
    action = packed.action
    page = packed.page or 1
    name = action.split("module_stop__", 1)[-1]

    try:
        await manager.stop(name)
        result = "🛑 Модуль остановлен."
    except Exception as e:
        result = f"❌ Ошибка остановки: {e}"

    items = dict(await run_io(list_installed_modules))
    ver = items.get(name)
    title = f"{name} v{ver}" if ver else name
    text = menu_text("Модули", f"🧩 Модуль: <b>{title}</b>", quote(f"{result}"))

    markup = build_module_menu_kb(name, page)
    try:
        await callback_query.message.edit_text(text=text, reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        raise


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("module_start__")), IsAdminFilter())
async def handle_module_start(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()

    packed = AdminPanelCallback.unpack(callback_query.data)
    action = packed.action
    page = packed.page or 1
    name = action.split("module_start__", 1)[-1]

    try:
        await manager.start(name)
        result = "▶️ Модуль запущен."
    except Exception as e:
        result = f"❌ Ошибка запуска: {e}"

    items = dict(await run_io(list_installed_modules))
    ver = items.get(name)
    title = f"{name} v{ver}" if ver else name
    text = menu_text("Модули", f"🧩 Модуль: <b>{title}</b>", quote(f"{result}"))

    markup = build_module_menu_kb(name, page)
    try:
        await callback_query.message.edit_text(text=text, reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        raise
