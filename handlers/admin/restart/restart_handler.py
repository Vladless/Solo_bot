import subprocess

from aiogram import F, Router
from aiogram.types import CallbackQuery

from filters.admin import IsAdminFilter

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb, build_restart_kb


router = Router()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "restart"),
    IsAdminFilter(),
)
async def handle_restart(callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        text="🤔 Вы уверены, что хотите перезагрузить бота?",
        reply_markup=build_restart_kb(),
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "restart_confirm"),
    IsAdminFilter(),
)
async def handle_restart_confirm(callback_query: CallbackQuery):
    kb = build_admin_back_kb()
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", "bot.service"],
            check=True,
            capture_output=True,
            text=True,
        )
        await callback_query.message.edit_text(text="🔄 Бот успешно перезагружен!", reply_markup=kb)
    except subprocess.CalledProcessError:
        await callback_query.message.edit_text(text="🔄 Бот успешно перезагружен!", reply_markup=kb)
    except Exception as e:
        await callback_query.message.edit_text(text=f"⚠️ Ошибка при перезагрузке бота: {e.stderr}", reply_markup=kb)
