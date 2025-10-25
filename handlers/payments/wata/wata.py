import aiohttp

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    FAIL_REDIRECT_LINK,
    REDIRECT_LINK,
    WATA_INT_TOKEN,
    WATA_RU_TOKEN,
    WATA_SBP_TOKEN,
    PROVIDERS_ENABLED,
)
from handlers.payments.providers import get_providers
from handlers.buttons import BACK, PAY_2, WATA_INT, WATA_RU, WATA_SBP
from handlers.texts import (
    ENTER_SUM,
    PAYMENT_OPTIONS,
    WATA_INT_DESCRIPTION,
    WATA_PAYMENT_MESSAGE,
    WATA_PAYMENT_TITLE,
    WATA_RU_DESCRIPTION,
    WATA_SBP_DESCRIPTION,
)
from handlers.utils import edit_or_send_message
from logger import logger


router = Router()


class ReplenishBalanceWataState(StatesGroup):
    choosing_cassa = State()
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


PROVIDERS = get_providers(PROVIDERS_ENABLED)
WATA_CASSA_CONFIG = [
    {
        "enable": bool(PROVIDERS.get("WATA_RU", {}).get("enabled")),
        "token": WATA_RU_TOKEN,
        "name": "ru",
        "button": WATA_RU,
        "desc": WATA_RU_DESCRIPTION,
    },
    {
        "enable": bool(PROVIDERS.get("WATA_SBP", {}).get("enabled")),
        "token": WATA_SBP_TOKEN,
        "name": "sbp",
        "button": WATA_SBP,
        "desc": WATA_SBP_DESCRIPTION,
    },
    {
        "enable": bool(PROVIDERS.get("WATA_INT", {}).get("enabled")),
        "token": WATA_INT_TOKEN,
        "name": "int",
        "button": WATA_INT,
        "desc": WATA_INT_DESCRIPTION,
    },
]


@router.callback_query(F.data == "pay_wata")
async def process_callback_pay_wata(
    callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession, cassa_name: str = None
):
    tg_id = callback_query.message.chat.id
    logger.info(f"User {tg_id} initiated WATA payment.")

    if cassa_name:
        cassa = next((c for c in WATA_CASSA_CONFIG if c["name"] == cassa_name and c["enable"]), None)
        if not cassa:
            await edit_or_send_message(
                target_message=callback_query.message,
                text="Ошибка: выбранная касса недоступна.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                force_text=True,
            )
            return

        builder = InlineKeyboardBuilder()
        for i in range(0, len(PAYMENT_OPTIONS), 2):
            if i + 1 < len(PAYMENT_OPTIONS):
                builder.row(
                    InlineKeyboardButton(
                        text=PAYMENT_OPTIONS[i]["text"],
                        callback_data=f"wata_amount|{cassa_name}|{PAYMENT_OPTIONS[i]['callback_data']}",
                    ),
                    InlineKeyboardButton(
                        text=PAYMENT_OPTIONS[i + 1]["text"],
                        callback_data=f"wata_amount|{cassa_name}|{PAYMENT_OPTIONS[i + 1]['callback_data']}",
                    ),
                )
            else:
                builder.row(
                    InlineKeyboardButton(
                        text=PAYMENT_OPTIONS[i]["text"],
                        callback_data=f"wata_amount|{cassa_name}|{PAYMENT_OPTIONS[i]['callback_data']}",
                    )
                )

        builder.row(InlineKeyboardButton(text="Ввести сумму", callback_data=f"wata_custom_amount|{cassa_name}"))
        builder.row(InlineKeyboardButton(text=BACK, callback_data="pay"))

        try:
            await callback_query.message.delete()
        except Exception:
            pass

        new_message = await callback_query.message.answer(
            text=cassa["desc"],
            reply_markup=builder.as_markup(),
        )
        await state.update_data(message_id=new_message.message_id, chat_id=new_message.chat.id, wata_cassa=cassa_name)
        await state.set_state(ReplenishBalanceWataState.choosing_amount)
        return

    builder = InlineKeyboardBuilder()
    for cassa in WATA_CASSA_CONFIG:
        if cassa["enable"]:
            builder.row(InlineKeyboardButton(text=cassa["button"], callback_data=f"wata_cassa|{cassa['name']}"))
    builder.row(InlineKeyboardButton(text=BACK, callback_data="balance"))

    try:
        await callback_query.message.delete()
    except Exception:
        pass

    new_message = await callback_query.message.answer(
        text="Выберите способ оплаты через WATA:",
        reply_markup=builder.as_markup(),
    )
    await state.update_data(message_id=new_message.message_id, chat_id=new_message.chat.id)
    await state.set_state(ReplenishBalanceWataState.choosing_cassa)


@router.callback_query(F.data.startswith("wata_cassa|"))
async def process_cassa_selection(callback_query: types.CallbackQuery, state: FSMContext):
    cassa_name = callback_query.data.split("|")[1]
    cassa = next((c for c in WATA_CASSA_CONFIG if c["name"] == cassa_name and c["enable"]), None)

    if not cassa:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранная касса недоступна.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            force_text=True,
        )
        return

    await state.update_data(wata_cassa=cassa_name)

    builder = InlineKeyboardBuilder()
    for i in range(0, len(PAYMENT_OPTIONS), 2):
        if i + 1 < len(PAYMENT_OPTIONS):
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f"wata_amount|{cassa_name}|{PAYMENT_OPTIONS[i]['callback_data']}",
                ),
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=f"wata_amount|{cassa_name}|{PAYMENT_OPTIONS[i + 1]['callback_data']}",
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f"wata_amount|{cassa_name}|{PAYMENT_OPTIONS[i]['callback_data']}",
                )
            )
    builder.row(InlineKeyboardButton(text=BACK, callback_data="balance"))

    try:
        await callback_query.message.delete()
    except Exception:
        pass

    new_message = await callback_query.message.answer(
        text=cassa["desc"],
        reply_markup=builder.as_markup(),
    )
    await state.update_data(message_id=new_message.message_id, chat_id=new_message.chat.id)
    await state.set_state(ReplenishBalanceWataState.choosing_amount)


@router.message(ReplenishBalanceWataState.entering_custom_amount)
async def handle_custom_amount_text_input(message: types.Message, state: FSMContext):
    text = message.text.strip()

    try:
        amount = int(text)
        if amount <= 0:
            raise ValueError

        await state.update_data(required_amount=amount)
        await handle_custom_amount_input(message, state)

    except ValueError:
        await message.answer("❌ Введите корректную сумму: только цифры, без символов.")


@router.callback_query(F.data.startswith("wata_custom_amount|"))
async def process_custom_amount_button(callback_query: types.CallbackQuery, state: FSMContext):
    cassa_name = callback_query.data.split("|")[1]
    await state.update_data(wata_cassa=cassa_name)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"pay_wata_{cassa_name}"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=ENTER_SUM,
        reply_markup=builder.as_markup(),
        force_text=True,
    )
    await state.set_state(ReplenishBalanceWataState.entering_custom_amount)


async def handle_custom_amount_input(event: types.Message | types.CallbackQuery, state: FSMContext):
    tg_id = event.from_user.id
    target_message = getattr(event, "message", None) or event

    try:
        data = await state.get_data()
        required_amount = data.get("required_amount", 0)
        cassa_name = data.get("wata_cassa", "sbp")

        if not required_amount or required_amount <= 0:
            await edit_or_send_message(
                target_message=target_message,
                text="❌ Недостаточная сумма для оплаты.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            )
            return

        cassa = next((c for c in WATA_CASSA_CONFIG if c["name"] == cassa_name and c["enable"]), None)
        if not cassa:
            await edit_or_send_message(
                target_message=target_message,
                text="❌ Выбранная касса WATA недоступна.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            )
            return

        payment_url = await generate_wata_payment_link(required_amount, tg_id, cassa)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=PAY_2, url=payment_url)],
                [InlineKeyboardButton(text=BACK, callback_data="balance")],
            ]
        )

        message_text = f"💰 Вы выбрали пополнение на <b>{required_amount}₽</b>. Перейдите по ссылке для оплаты:"
        await edit_or_send_message(
            target_message=target_message,
            text=message_text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )

        await state.clear()

    except Exception as e:
        logger.error(f"[WATA] Ошибка при создании ссылки для оплаты: {e}", exc_info=True)
        await edit_or_send_message(
            target_message=target_message,
            text="⚠️ Произошла ошибка при создании ссылки на оплату. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )


@router.callback_query(F.data.startswith("wata_amount|"))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    parts = callback_query.data.split("|")
    cassa_name = parts[1]
    amount_str = parts[-1]

    cassa = next((c for c in WATA_CASSA_CONFIG if c["name"] == cassa_name and c["enable"]), None)
    if not cassa:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранная касса недоступна.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            force_text=True,
        )
        return

    try:
        amount = int(amount_str)
        if amount <= 0:
            raise ValueError
    except Exception:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Некорректная сумма.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            force_text=True,
        )
        return

    await state.update_data(amount=amount)
    payment_url = await generate_wata_payment_link(amount, callback_query.message.chat.id, cassa)

    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=PAY_2, url=payment_url)],
            [InlineKeyboardButton(text=BACK, callback_data="balance")],
        ]
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=WATA_PAYMENT_MESSAGE.format(amount=amount),
        reply_markup=confirm_keyboard,
        force_text=True,
    )

    await state.set_state(ReplenishBalanceWataState.waiting_for_payment_confirmation)


async def generate_wata_payment_link(amount, tg_id, cassa):
    url = "https://api.wata.pro/api/h2h/links"
    headers = {
        "Authorization": f"Bearer {cassa['token']}",
        "Content-Type": "application/json",
    }
    data = {
        "amount": float(amount),
        "currency": "RUB",
        "orderId": str(tg_id),
        "orderDescription": WATA_PAYMENT_TITLE,
        "successUrl": f"{REDIRECT_LINK}",
        "failUrl": f"{FAIL_REDIRECT_LINK}",
    }

    if cassa["name"] == "int":
        import xml.etree.ElementTree as ET
        from datetime import datetime

        async def get_usd_rate():
            today = datetime.now().strftime("%d/%m/%Y")
            url = f"http://www.cbr.ru/scripts/XML_daily.asp?date_req={today}"

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=15) as resp:
                        if resp.status == 200:
                            xml_content = await resp.text()
                            root = ET.fromstring(xml_content)

                            for valute in root.findall("Valute"):
                                char_code = valute.find("CharCode")
                                if char_code is not None and char_code.text == "USD":
                                    value_elem = valute.find("Value")
                                    if value_elem is not None:
                                        usd_rub_rate = float(value_elem.text.replace(",", "."))
                                        rub_usd_rate = 1 / usd_rub_rate
                                        logger.info(
                                            f"CBR USD rate: 1 USD = {usd_rub_rate} RUB, 1 RUB = {rub_usd_rate} USD"
                                        )
                                        return rub_usd_rate

                            logger.warning("USD rate not found in CBR response")

            except Exception as e:
                logger.error(f"Failed to get USD rate from CBR: {e}")

            fallback_rate = 0.0105
            logger.warning(f"Using fallback USD rate: {fallback_rate}")
            return fallback_rate

        try:
            usd_rate = await get_usd_rate()
            rub_per_usd = 1 / usd_rate
            rub_per_usd_plus_5 = rub_per_usd + 5 
            new_usd_rate = 1 / rub_per_usd_plus_5
            amount_usd = round(float(amount) * new_usd_rate, 2)
            data["amount"] = amount_usd
            data["currency"] = "USD"
        except Exception as e:
            logger.error(f"Failed to convert RUB to USD: {e}")
            fallback_usd_rate = 0.0105
            rub_per_usd = 1 / fallback_usd_rate
            rub_per_usd_plus_5 = rub_per_usd + 5
            new_usd_rate = 1 / rub_per_usd_plus_5
            amount_usd = round(float(amount) * new_usd_rate, 2)
            data["amount"] = amount_usd
            data["currency"] = "USD"

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data, timeout=60) as resp:
            if resp.status == 200:
                try:
                    resp_json = await resp.json()
                except Exception:
                    text = await resp.text()
                    logger.error(f"Ошибка при разборе JSON ответа WATA: статус={resp.status}, ответ={text}")
                    return "https://wata.pro/"

                if "url" in resp_json:
                    return resp_json["url"]

                logger.error(f"Ответ WATA без url: {resp_json}")
                return "https://wata.pro/"

            try:
                error_json = await resp.json()
                logger.error(f"Ошибка WATA API: статус={resp.status}, ответ={error_json}")
            except Exception:
                text = await resp.text()
                logger.error(f"Ошибка WATA API: статус={resp.status}, не-JSON ответ: {text}")
            return "https://wata.pro/"
