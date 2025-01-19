from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from keyboards.admin.coupons_kb import AdminCouponDeleteCallback
from keyboards.admin.panel_kb import AdminPanelCallback
from keyboards.admin.sender_kb import AdminSenderCallback
from keyboards.admin.servers_kb import AdminServerEditorCallback
from keyboards.admin.users_kb import AdminUserEditorCallback, AdminUserKeyEditorCallback

pass_callbacks = [
    AdminPanelCallback,
    AdminCouponDeleteCallback,
    AdminSenderCallback,
    AdminServerEditorCallback,
    AdminUserEditorCallback,
    AdminUserKeyEditorCallback,
]


class DeleteMessageMiddleware(BaseMiddleware):
    async def __call__(
            self,
            handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            if (
                    not event.text
                    or not event.text.startswith("/start")
            ):
                try:
                    await event.bot.delete_message(
                        event.chat.id, event.message_id - 1
                    )
                except Exception:
                    pass
                await event.delete()

        if isinstance(event, CallbackQuery):
            await event.answer()

            if not await self._check_callbacks(event):
                await event.message.delete()

        return await handler(event, data)

    @staticmethod
    async def _check_callbacks(event: CallbackQuery) -> bool:
        for callback in pass_callbacks:
            if await callback.filter()(event):
                return True
        return False
