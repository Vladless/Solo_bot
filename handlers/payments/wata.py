import aiohttp
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta
from sqlalchemy import select, and_

from config import (
    WATA_RU_ENABLE, WATA_RU_TOKEN,
    WATA_SBP_ENABLE, WATA_SBP_TOKEN,
    WATA_INT_ENABLE, WATA_INT_TOKEN,
    SUCCESS_REDIRECT_LINK,
    FAIL_REDIRECT_LINK,
)
from database import (
    add_payment,
    add_user,
    async_session_maker,
    check_user_exists,
    get_key_count,
    get_temporary_data,
    update_balance,
    Payment
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
        # Сразу открываем выбор суммы для нужной кассы
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
        builder.row(InlineKeyboardButton(text=BACK, callback_data="pay"))
        await callback_query.message.delete()
        new_message = await callback_query.message.answer(
            text=cassa["desc"],
            reply_markup=builder.as_markup(),
        )
        await state.update_data(message_id=new_message.message_id, chat_id=new_message.chat.id)
        await state.set_state(ReplenishBalanceWataState.choosing_amount)
        return
    # Если cassa_name не передан — обычное меню выбора кассы
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

@router.callback_query(F.data.startswith("wata_amount|"))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    _, cassa_name, amount_str = callback_query.data.split("|")
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
    url = "https://api.wata.pro/api/h2h/payment-link"
    headers = {
        "Authorization": f"Bearer {cassa['token']}",
        "Content-Type": "application/json",
    }
    data = {
        "amount": float(amount),
        "currency": "RUB",
        "orderId": str(tg_id),
        "orderDescription": WATA_PAYMENT_TITLE,
        "successUrl": f"{SUCCESS_REDIRECT_LINK}",
        "failUrl": f"{FAIL_REDIRECT_LINK}",
    }
    #if cassa["name"] == "sbp":
        #data["paymentMethod"] = "SBP"
    if cassa["name"] == "int":
        # Здесь конвертируем сумму из рублей в USD по курсу гугла + 5 рублей к курсу
        import json

        async def get_usd_rate():
            # Получаем курс доллара с помощью публичного API (например, exchangerate.host)
            url = "https://api.exchangerate.host/latest?base=RUB&symbols=USD"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    data = await resp.json()
                    return data["rates"]["USD"]

        usd_rate = await get_usd_rate()
        # Прибавляем 5 рублей к курсу (то есть к 1 USD в рублях)
        # 1 USD = 1 / usd_rate RUB, значит RUB за 1 USD = 1 / usd_rate
        rub_per_usd = 1 / usd_rate
        rub_per_usd_plus_5 = rub_per_usd + 5
        # Новый курс USD = 1 / (RUB за 1 USD + 5)
        new_usd_rate = 1 / rub_per_usd_plus_5
        amount_usd = round(float(amount) * new_usd_rate, 2)
        data["amount"] = amount_usd
        data["currency"] = "USD"  # или другую валюту, если нужно
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data, timeout=60) as resp:
            resp_json = await resp.json()
            if resp.status == 200 and "paymentLink" in resp_json:
                return resp_json["paymentLink"]
            else:
                logger.error(f"Ошибка при создании ссылки WATA: {resp_json}")
                return "https://wata.pro/"  # fallback
