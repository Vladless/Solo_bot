from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.types import CallbackQuery, TelegramObject

from core.bootstrap import BUTTONS_CONFIG
from database.access.resolution import TelegramId
from database.temporary_data import get_temporary_data
from handlers.payments.constants import ALLOWED_TEMP_PAYMENT_STATES
from settings.config import BALANCE_BUTTON


def balance_topup_enabled() -> bool:
    """Проверяет доступность пополнения по текущим настройкам."""
    return bool(BUTTONS_CONFIG.get("BALANCE_BUTTON_ENABLE", BALANCE_BUTTON))


class BalanceTopupMiddleware(BaseMiddleware):
    """Закрывает пополнение, сохраняя оплату заказов и готовых счетов."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        mode = get_flag(data, "balance_topup")
        if not mode or balance_topup_enabled():
            return await handler(event, data)

        state = data.get("state")
        if mode == "checkout" and isinstance(event, CallbackQuery):
            temporary = await get_temporary_data(data["session"], TelegramId(event.from_user.id))
            if (
                isinstance(temporary, dict)
                and isinstance(temporary.get("state"), str)
                and temporary["state"] in ALLOWED_TEMP_PAYMENT_STATES
                and state is not None
            ):
                from handlers.payments.fast_payment_flow import resume_checkout

                return await resume_checkout(event, state, data["session"])
        elif isinstance(mode, str) and state is not None:
            context = (await state.get_data()).get(mode)
            if isinstance(context, dict):
                if mode == "payhot_pending" and context.get("fast") is True:
                    return await handler(event, data)
                if mode == "payhot_method_choice":
                    context = context.get("checkout")
                metadata = context.get("metadata") if isinstance(context, dict) else None
                checkout = metadata.get("checkout_intent") if isinstance(metadata, dict) else None
                if (
                    isinstance(checkout, dict)
                    and isinstance(checkout.get("state"), str)
                    and checkout["state"] in ALLOWED_TEMP_PAYMENT_STATES
                    and isinstance(metadata.get("payment_flow"), str)
                    and metadata.get("payment_flow")
                    in {"tariff_purchase", "trial_purchase", "key_renewal", "key_addons", "gift_create"}
                ):
                    return await handler(event, data)

        from handlers.profile import process_callback_view_profile

        return await process_callback_view_profile(event, state, bool(data.get("admin")), data["session"])
