from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import PAYMENTS_CONFIG, update_payments_config
from filters.admin import IsAdminFilter

from ..panel.keyboard import AdminPanelCallback
from .keyboard import PAYMENT_PROVIDER_TITLES, build_settings_cashboxes_kb


router = Router(name="admin_settings_cashboxes")
router.callback_query.filter(IsAdminFilter())


async def load_payment_providers_settings() -> dict[str, bool]:
    config = PAYMENTS_CONFIG or {}
    return {k: bool(config.get(k, False)) for k in PAYMENT_PROVIDER_TITLES.keys()}


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_cashboxes"))
async def open_settings_cashboxes_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    providers_state = await load_payment_providers_settings()
    text = "Здесь можно включать и отключать платёжные провайдеры."
    await callback.message.edit_text(text=text, reply_markup=build_settings_cashboxes_kb(providers_state))
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_cashbox_toggle"))
async def toggle_cashbox_setting(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    session: AsyncSession,
) -> None:
    keys = list(PAYMENT_PROVIDER_TITLES.keys())
    index = callback_data.page

    if not 1 <= index <= len(keys):
        await callback.answer("Неизвестная касса", show_alert=True)
        return

    provider_code = keys[index - 1]

    config = dict(PAYMENTS_CONFIG or {})
    current_value = bool(config.get(provider_code, False))
    config[provider_code] = not current_value

    await update_payments_config(
        session,
        config,
    )
    await session.commit()

    updated_state = {k: bool(config.get(k, False)) for k in PAYMENT_PROVIDER_TITLES.keys()}
    await callback.message.edit_reply_markup(
        reply_markup=build_settings_cashboxes_kb(updated_state),
    )
    await callback.answer("Настройка обновлена")
