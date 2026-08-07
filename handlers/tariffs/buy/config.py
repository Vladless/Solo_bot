from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery

from core.settings.tariffs_config import normalize_tariff_config
from database import get_tariff_by_id
from handlers.utils import edit_or_send_message, safe_answer_callback
from logger import logger


CREATING_KEY_BUTTON_TEXT = "⏳ Подождите..."
from .screens import render_user_config_screen


class TariffUserConfigState(StatesGroup):
    """Состояния конфигуратора тарифа для пользователя."""

    configuring = State()


async def start_tariff_config(
    callback_query: CallbackQuery,
    state: FSMContext,
    session: Any,
    tariff_id: int,
):
    """Запускает конфигуратор тарифа по id."""
    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Указанный тариф не найден.",
        )
        await safe_answer_callback(callback_query)
        logger.warning(f"[TARIFF_CFG] start_tariff_config tariff_not_found: tariff_id={tariff_id}")
        return

    if not tariff.get("configurable"):
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Этот тариф нельзя настроить.",
        )
        await safe_answer_callback(callback_query)
        logger.info(f"[TARIFF_CFG] start_tariff_config not_configurable: tariff_id={tariff_id}")
        return

    await start_user_tariff_configurator(
        callback_query=callback_query,
        session=session,
        state=state,
        tariff=tariff,
    )


async def start_user_tariff_configurator(
    callback_query: CallbackQuery,
    session: Any,
    state: FSMContext,
    tariff: dict,
):
    """Запускает конфигуратор тарифа для пользователя."""
    cfg = normalize_tariff_config(tariff)

    raw_device_options = tariff.get("device_options")
    raw_traffic_options = tariff.get("traffic_options_gb")

    raw_device_options = raw_device_options if isinstance(raw_device_options, list) else []
    raw_traffic_options = raw_traffic_options if isinstance(raw_traffic_options, list) else []

    try:
        device_options = sorted(raw_device_options, key=lambda v: (int(v) == 0, int(v)))
    except (TypeError, ValueError):
        device_options = raw_device_options

    try:
        traffic_options = sorted(raw_traffic_options, key=lambda v: (int(v) == 0, int(v)))
    except (TypeError, ValueError):
        traffic_options = raw_traffic_options

    logger.info(
        "[TARIFF_CFG] start_user_tariff_configurator: "
        f"tg_id={callback_query.from_user.id} tariff_id={tariff.get('id')} "
        f"device_options={device_options} traffic_options={traffic_options}"
    )

    if not device_options and not traffic_options:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Конфигуратор для этого тарифа не настроен. Попробуйте выбрать другой тариф.",
            reply_markup=None,
        )
        await safe_answer_callback(callback_query)
        logger.warning(f"[TARIFF_CFG] configurator_not_configured: tariff_id={tariff.get('id')}")
        return

    cfg_for_state = dict(cfg)
    cfg_for_state["device_options"] = device_options
    cfg_for_state["traffic_options_gb"] = traffic_options

    data = await state.get_data()
    renew_mode = data.get("renew_mode")

    update_payload: dict[str, Any] = {
        "config_tariff_id": tariff["id"],
        "tariff_config": cfg_for_state,
    }
    if renew_mode == "renew":
        update_payload["renew_tariff_id"] = tariff["id"]
    else:
        update_payload["config_selected_device_limit"] = None
        update_payload["config_selected_traffic_gb"] = None

    await state.update_data(**update_payload)
    await state.set_state(TariffUserConfigState.configuring)

    await render_user_config_screen(callback_query, state, session)
