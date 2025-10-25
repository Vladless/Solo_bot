import asyncio
import os
import subprocess
import sys

import psutil

from aiogram import F, Router
from aiogram.types import CallbackQuery

from filters.admin import IsAdminFilter

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb


router = Router()


@router.callback_query(AdminPanelCallback.filter(F.action == "restart"), IsAdminFilter())
async def handle_restart_confirm(callback_query: CallbackQuery, callback_data: AdminPanelCallback):
    kb = build_admin_back_kb()
    await callback_query.message.edit_text("🔄 Перезапускаем бота...", reply_markup=kb)

    asyncio.create_task(restart_bot())


async def restart_bot():
    await asyncio.sleep(1)

    try:
        parent = psutil.Process(os.getpid()).parent()
        is_systemd = parent and "systemd" in parent.name().lower()

        if is_systemd:
            subprocess.run(
                ["sudo", "systemctl", "restart", "bot.service"],
                check=True,
            )
        else:
            python_exe = sys.executable
            script_path = os.path.abspath(sys.argv[0])
            os.execv(python_exe, [python_exe, script_path] + sys.argv[1:])

    except Exception as e:
        print(f"[Restart] Ошибка при перезапуске: {e}")
        os._exit(1)
