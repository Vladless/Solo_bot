from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import PAYMENTS_CONFIG, update_payments_config
from core.settings.providers_order_config import PROVIDERS_ORDER, update_providers_order
from filters.admin import IsAdminFilter
from handlers.payments.providers import PROVIDERS_BASE, _get_effective_order

from ..panel.keyboard import AdminPanelCallback
from .keyboard import PAYMENT_PROVIDER_TITLES, build_providers_order_kb, build_settings_cashboxes_kb


router = Router(name="admin_settings_cashboxes")
router.callback_query.filter(IsAdminFilter())


async def load_payment_providers_settings() -> dict[str, bool]:
    config = PAYMENTS_CONFIG or {}
    return {k: bool(config.get(k, False)) for k in PAYMENT_PROVIDER_TITLES.keys()}


def _get_sorted_provider_names() -> list[str]:
    """Возвращает все провайдеры, отсортированные по текущему порядку."""
    all_names = list(PROVIDERS_BASE.keys())
    return sorted(
        all_names,
        key=lambda n: _get_effective_order(n, PROVIDERS_BASE.get(n, {})),
    )


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

@router.callback_query(AdminPanelCallback.filter(F.action == "settings_providers_order"))
async def open_providers_order_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    sorted_names = _get_sorted_provider_names()
    text = (
        "📋 <b>Порядок отображения касс</b>\n\n"
        "⬆️ — поднять выше\n"
        "⬇️ — опустить ниже\n\n"
        "Порядок влияет на меню оплаты и fast flow."
    )
    await callback.message.edit_text(
        text=text,
        reply_markup=build_providers_order_kb(sorted_names),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_order_up"))
async def move_provider_up(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    session: AsyncSession,
) -> None:
    sorted_names = _get_sorted_provider_names()
    idx = callback_data.page - 1

    if idx <= 0:
        await callback.answer("Уже на первом месте", show_alert=False)
        return

    if idx >= len(sorted_names):
        await callback.answer("Неизвестная касса", show_alert=True)
        return

    sorted_names[idx], sorted_names[idx - 1] = sorted_names[idx - 1], sorted_names[idx]

    new_order = {name: (i + 1) * 10 for i, name in enumerate(sorted_names)}
    await update_providers_order(session, new_order)

    await callback.message.edit_reply_markup(
        reply_markup=build_providers_order_kb(sorted_names),
    )
    await callback.answer("✅ Перемещено выше")


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_order_down"))
async def move_provider_down(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    session: AsyncSession,
) -> None:
    sorted_names = _get_sorted_provider_names()
    idx = callback_data.page - 1

    if idx >= len(sorted_names) - 1:
        await callback.answer("Уже на последнем месте", show_alert=False)
        return

    if idx < 0:
        await callback.answer("Неизвестная касса", show_alert=True)
        return

    sorted_names[idx], sorted_names[idx + 1] = sorted_names[idx + 1], sorted_names[idx]

    new_order = {name: (i + 1) * 10 for i, name in enumerate(sorted_names)}
    await update_providers_order(session, new_order)

    await callback.message.edit_reply_markup(
        reply_markup=build_providers_order_kb(sorted_names),
    )
    await callback.answer("✅ Перемещено ниже")


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_order_reset"))
async def reset_providers_order(callback: CallbackQuery, session: AsyncSession) -> None:
    await update_providers_order(session, {})

    sorted_names = _get_sorted_provider_names()
    await callback.message.edit_reply_markup(
        reply_markup=build_providers_order_kb(sorted_names),
    )
    await callback.answer("✅ Порядок сброшен на дефолтный")
