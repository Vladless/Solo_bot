from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from keyboards.admin.coupons_kb import AdminCouponDeleteCallback
from keyboards.admin.panel_kb import AdminPanelCallback
from keyboards.admin.sender_kb import AdminSenderCallback
from keyboards.admin.servers_kb import AdminServerEditorCallback
from keyboards.admin.users_kb import AdminUserEditorCallback

pass_callbacks = [
    AdminPanelCallback,
    AdminCouponDeleteCallback,
    AdminSenderCallback,
    AdminServerEditorCallback,
    AdminUserEditorCallback,
]

pass_states = [
    'UserEditorState',
    'AdminServersEditor',
    'AdminSender',
    'AdminCouponsState'
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
                    not event.text or
                    not event.text.startswith("/start") or
                    not self._check_user_state(event)
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
    async def _check_user_state(event: CallbackQuery) -> bool:
        from bot import dp
        context = dp.fsm.get_context(
            bot=event.bot,
            user_id=event.from_user.id,
            chat_id=event.chat.id
        )
        state = await context.get_state()
        return state.split(":")[0] in pass_states

    @staticmethod
    async def _check_callbacks(event: CallbackQuery) -> bool:
        for callback in pass_callbacks:
            if await callback.filter()(event):
                return True
        return False
