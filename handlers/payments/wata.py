import aiohttp
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    WATA_RU_ENABLE, WATA_RU_TOKEN,
    WATA_SBP_ENABLE, WATA_SBP_TOKEN,
    WATA_INT_ENABLE, WATA_INT_TOKEN,
    REDIRECT_LINK,
    FAIL_REDIRECT_LINK,
)

from handlers.buttons import BACK, PAY_2, WATA_RU, WATA_SBP, WATA_INT
from handlers.texts import (
    WATA_RU_DESCRIPTION, WATA_SBP_DESCRIPTION, WATA_INT_DESCRIPTION,
    WATA_PAYMENT_MESSAGE, ENTER_SUM, PAYMENT_OPTIONS, WATA_PAYMENT_TITLE
)
from handlers.utils import edit_or_send_message
from logger import logger


router = Router()


class ReplenishBalanceWataState(StatesGroup):
    choosing_cassa = State()
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()  


WATA_CASSA_CONFIG = [
    {"enable": WATA_RU_ENABLE, "token": WATA_RU_TOKEN, "name": "ru", "button": WATA_RU, "desc": WATA_RU_DESCRIPTION},
    {"enable": WATA_SBP_ENABLE, "token": WATA_SBP_TOKEN, "name": "sbp", "button": WATA_SBP, "desc": WATA_SBP_DESCRIPTION},
    {"enable": WATA_INT_ENABLE, "token": WATA_INT_TOKEN, "name": "int", "button": WATA_INT, "desc": WATA_INT_DESCRIPTION},
]


@router.callback_query(F.data == "pay_wata")
async def process_callback_pay_wata(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession, cassa_name: str = None):
    tg_id = callback_query.message.chat.id
    logger.info(f"User {tg_id} initiated WATA payment.")
    if cassa_name:
        cassa = next((c for c in WATA_CASSA_CONFIG if c["name"] == cassa_name and c["enable"]), None)
        if not cassa:
            await edit_or_send_message(
                target_message=callback_query.message,
                text="Ошибка: выбранная касса недоступна.",
                reply_markup=types.InlineKeyboardMarkup(),
                force_text=True,
            )
            return
        builder = InlineKeyboardBuilder()
        for i in range(0, len(PAYMENT_OPTIONS), 2):
            if i + 1 < len(PAYMENT_OPTIONS):
                builder.row(
                    InlineKeyboardButton(
                        text=PAYMENT_OPTIONS[i]["text"],
                        callback_data=f'wata_amount|{cassa_name}|{PAYMENT_OPTIONS[i]["callback_data"]}',
                    ),
                    InlineKeyboardButton(
                        text=PAYMENT_OPTIONS[i + 1]["text"],
                        callback_data=f'wata_amount|{cassa_name}|{PAYMENT_OPTIONS[i + 1]["callback_data"]}',
                    ),
                )
            else:
                builder.row(
                    InlineKeyboardButton(
                        text=PAYMENT_OPTIONS[i]["text"],
                        callback_data=f'wata_amount|{cassa_name}|{PAYMENT_OPTIONS[i]["callback_data"]}',
                    )
                )
        builder.row(InlineKeyboardButton(text="Ввести сумму", callback_data=f"wata_custom_amount|{cassa_name}"))
        builder.row(InlineKeyboardButton(text=BACK, callback_data="pay"))
        await callback_query.message.delete()
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
            builder.row(InlineKeyboardButton(text=cassa["button"], callback_data=f'wata_cassa|{cassa["name"]}'))
    builder.row(InlineKeyboardButton(text=BACK, callback_data="balance"))
    await callback_query.message.delete()
    new_message = await callback_query.message.answer(
        text="Выберите способ оплаты через WATA:",
        reply_markup=builder.as_markup(),
    )
    await state.update_data(message_id=new_message.message_id, chat_id=new_message.chat.id)
    await state.set_state(ReplenishBalanceWataState.choosing_cassa)


@router.callback_query(F.data.startswith("wata_cassa|"))
async def process_cassa_selection(callback_query: types.CallbackQuery, state: FSMContext):
    cassa_name = callback_query.data.split("|")[1]
    cassa = next((c for c in WATA_CASSA_CONFIG if c["name"] == cassa_name), None)
    if not cassa or not cassa["enable"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранная касса недоступна.",
            reply_markup=types.InlineKeyboardMarkup(),
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
                    callback_data=f'wata_amount|{cassa_name}|{PAYMENT_OPTIONS[i]["callback_data"]}',
                ),
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=f'wata_amount|{cassa_name}|{PAYMENT_OPTIONS[i + 1]["callback_data"]}',
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'wata_amount|{cassa_name}|{PAYMENT_OPTIONS[i]["callback_data"]}',
                )
            )
    builder.row(InlineKeyboardButton(text=BACK, callback_data="pay_wata"))
    await callback_query.message.delete()
    new_message = await callback_query.message.answer(
        text=cassa["desc"],
        reply_markup=builder.as_markup(),
    )
    await state.update_data(message_id=new_message.message_id, chat_id=new_message.chat.id)
    await state.set_state(ReplenishBalanceWataState.choosing_amount)


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


@router.message(ReplenishBalanceWataState.entering_custom_amount)
async def handle_custom_amount_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cassa_name = data.get("wata_cassa")
    cassa = next((c for c in WATA_CASSA_CONFIG if c["name"] == cassa_name), None)
    if not cassa or not cassa["enable"]:
        await edit_or_send_message(
            target_message=message,
            text="Ошибка: выбранная касса недоступна.",
            reply_markup=types.InlineKeyboardMarkup(),
            force_text=True,
        )
        return
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
        if cassa_name == "sbp" and amount < 50:
            await edit_or_send_message(
                target_message=message,
                text="Минимальная сумма для оплаты через СБП — 50 рублей.",
                reply_markup=types.InlineKeyboardMarkup(),
                force_text=True,
            )
            return
    except Exception:
        await edit_or_send_message(
            target_message=message,
            text="Некорректная сумма. Введите целое число больше 0.",
            reply_markup=types.InlineKeyboardMarkup(),
            force_text=True,
        )
        return
    await state.update_data(amount=amount)
    payment_url = await generate_wata_payment_link(amount, message.chat.id, cassa)
    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=PAY_2, url=payment_url)],
            [InlineKeyboardButton(text=BACK, callback_data="pay_wata")],
        ]
    )
    await edit_or_send_message(
        target_message=message,
        text=WATA_PAYMENT_MESSAGE.format(amount=amount),
        reply_markup=confirm_keyboard,
        force_text=True,
    )
    await state.set_state(ReplenishBalanceWataState.waiting_for_payment_confirmation)


@router.callback_query(F.data.startswith("wata_amount|"))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    parts = callback_query.data.split("|")
    cassa_name = parts[1]
    amount_str = parts[-1]
    cassa = next((c for c in WATA_CASSA_CONFIG if c["name"] == cassa_name), None)
    if not cassa or not cassa["enable"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранная касса недоступна.",
            reply_markup=types.InlineKeyboardMarkup(),
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
            reply_markup=types.InlineKeyboardMarkup(),
            force_text=True,
        )
        return
    await state.update_data(amount=amount)
    payment_url = await generate_wata_payment_link(amount, callback_query.message.chat.id, cassa)
    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=PAY_2, url=payment_url)],
            [InlineKeyboardButton(text=BACK, callback_data="pay_wata")],
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
        import json
        async def get_usd_rate():
            url = "https://api.exchangerate.host/latest?base=RUB&symbols=USD"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    data = await resp.json()
                    return data["rates"]["USD"]
        usd_rate = await get_usd_rate()
        rub_per_usd = 1 / usd_rate
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
                else:
                    logger.error(f"Ответ WATA без url: {resp_json}")
                    return "https://wata.pro/"
            else:

                try:
                    error_json = await resp.json()
                    logger.error(f"Ошибка WATA API: статус={resp.status}, ответ={error_json}")
                except Exception:
                    text = await resp.text()
                    logger.error(f"Ошибка WATA API: статус={resp.status}, не-JSON ответ: {text}")
                return "https://wata.pro/"
