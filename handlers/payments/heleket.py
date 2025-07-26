import aiohttp
import hashlib
import base64
import time
import json
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    HELEKET_ENABLE, HELEKET_MERCHANT_ID, HELEKET_API_KEY,
    HELEKET_SUCCESS_URL, HELEKET_RETURN_URL, HELEKET_CALLBACK_URL,
    HELEKET_CURRENCY_RATE
)

from handlers.buttons import BACK, PAY_2, HELEKET_CRYPTO
from handlers.texts import (
    HELEKET_CRYPTO_DESCRIPTION, HELEKET_PAYMENT_MESSAGE, ENTER_SUM, PAYMENT_OPTIONS, HELEKET_PAYMENT_TITLE
)
from handlers.utils import edit_or_send_message
from logger import logger

router = Router()


class ReplenishBalanceHeleket(StatesGroup):
    choosing_method = State()
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


HELEKET_PAYMENT_METHODS = [
    {"enable": HELEKET_ENABLE, "currency": "USD", "to_currency": None, "name": "crypto", "button": HELEKET_CRYPTO, "desc": HELEKET_CRYPTO_DESCRIPTION},
]


@router.callback_query(F.data == "pay_heleket")
async def process_callback_pay_heleket(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession, method_name: str = None):
    try:
        tg_id = callback_query.message.chat.id
        logger.info(f"User {tg_id} initiated Heleket payment.")
        
        await state.clear()
        
        if method_name:
            method = next((m for m in HELEKET_PAYMENT_METHODS if m["name"] == method_name and m["enable"]), None)
            if not method:
                await edit_or_send_message(
                    target_message=callback_query.message,
                    text="Ошибка: выбранный способ оплаты недоступен.",
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
                            callback_data=f'heleket_amount|{method_name}|{PAYMENT_OPTIONS[i]["callback_data"].split("|")[1]}',
                        ),
                        InlineKeyboardButton(
                            text=PAYMENT_OPTIONS[i + 1]["text"],
                            callback_data=f'heleket_amount|{method_name}|{PAYMENT_OPTIONS[i + 1]["callback_data"].split("|")[1]}',
                        ),
                    )
                else:
                    builder.row(
                        InlineKeyboardButton(
                            text=PAYMENT_OPTIONS[i]["text"],
                            callback_data=f'heleket_amount|{method_name}|{PAYMENT_OPTIONS[i]["callback_data"].split("|")[1]}',
                        )
                    )
            builder.row(InlineKeyboardButton(text="Ввести сумму", callback_data=f"heleket_custom_amount|{method_name}"))
            builder.row(InlineKeyboardButton(text=BACK, callback_data="balance"))
            
            await edit_or_send_message(
                target_message=callback_query.message,
                text=method["desc"],
                reply_markup=builder.as_markup(),
                force_text=True,
            )
            await state.update_data(heleket_method=method_name)
            await state.set_state(ReplenishBalanceHeleket.choosing_amount)
            return
        
        builder = InlineKeyboardBuilder()
        for method in HELEKET_PAYMENT_METHODS:
            if method["enable"]:
                builder.row(InlineKeyboardButton(text=method["button"], callback_data=f'heleket_method|{method["name"]}'))
        builder.row(InlineKeyboardButton(text=BACK, callback_data="balance"))
        
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Выберите способ оплаты через Heleket:",
            reply_markup=builder.as_markup(),
            force_text=True,
        )
        await state.update_data(message_id=callback_query.message.message_id, chat_id=callback_query.message.chat.id)
        await state.set_state(ReplenishBalanceHeleket.choosing_method)
        
    except Exception as e:
        logger.error(f"Error in process_callback_pay_heleket for user {callback_query.message.chat.id}: {e}")
        await callback_query.answer("Произошла ошибка при инициализации платежа. Попробуйте позже.", show_alert=True)


@router.callback_query(F.data.startswith("heleket_method|"))
async def process_method_selection(callback_query: types.CallbackQuery, state: FSMContext):
    method_name = callback_query.data.split("|")[1]
    method = next((m for m in HELEKET_PAYMENT_METHODS if m["name"] == method_name), None)
    
    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            force_text=True,
        )
        return
    
    await state.update_data(heleket_method=method_name)
    
    builder = InlineKeyboardBuilder()
    for i in range(0, len(PAYMENT_OPTIONS), 2):
        if i + 1 < len(PAYMENT_OPTIONS):
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'heleket_amount|{method_name}|{PAYMENT_OPTIONS[i]["callback_data"].split("|")[1]}',
                ),
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=f'heleket_amount|{method_name}|{PAYMENT_OPTIONS[i + 1]["callback_data"].split("|")[1]}',
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'heleket_amount|{method_name}|{PAYMENT_OPTIONS[i]["callback_data"].split("|")[1]}',
                )
            )
    builder.row(InlineKeyboardButton(text="Ввести сумму", callback_data=f"heleket_custom_amount|{method_name}"))
    builder.row(InlineKeyboardButton(text=BACK, callback_data="pay_heleket"))
    
    await edit_or_send_message(
        target_message=callback_query.message,
        text=method["desc"],
        reply_markup=builder.as_markup(),
        force_text=True,
    )
    await state.update_data(message_id=callback_query.message.message_id, chat_id=callback_query.message.chat.id)
    await state.set_state(ReplenishBalanceHeleket.choosing_amount)


@router.callback_query(F.data.startswith("heleket_custom_amount|"))
async def process_custom_amount_button(callback_query: types.CallbackQuery, state: FSMContext):
    method_name = callback_query.data.split("|")[1]
    await state.update_data(heleket_method=method_name)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"pay_heleket_{method_name}"))
    
    await edit_or_send_message(
        target_message=callback_query.message,
        text=ENTER_SUM,
        reply_markup=builder.as_markup(),
        force_text=True,
    )
    await state.set_state(ReplenishBalanceHeleket.entering_custom_amount)


@router.message(ReplenishBalanceHeleket.entering_custom_amount)
async def handle_custom_amount_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    method_name = data.get("heleket_method")
    method = next((m for m in HELEKET_PAYMENT_METHODS if m["name"] == method_name), None)
    
    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            force_text=True,
        )
        return
    
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
        if amount < 10:  
            await edit_or_send_message(
                target_message=message,
                text="Минимальная сумма для оплаты криптовалютой — 10 рублей.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                force_text=True,
            )
            return
    except Exception:
        await edit_or_send_message(
            target_message=message,
            text="Некорректная сумма. Введите целое число больше 0.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            force_text=True,
        )
        return
    
    await state.update_data(amount=amount)
    payment_url = await generate_heleket_payment_link(amount, message.chat.id, method)
    
    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=PAY_2, url=payment_url)],
            [InlineKeyboardButton(text=BACK, callback_data="balance")],
        ]
    )
    
    await edit_or_send_message(
        target_message=message,
        text=HELEKET_PAYMENT_MESSAGE.format(amount=amount),
        reply_markup=confirm_keyboard,
        force_text=True,
    )
    
    await state.set_state(ReplenishBalanceHeleket.waiting_for_payment_confirmation)


@router.callback_query(F.data.startswith("heleket_amount|"))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    parts = callback_query.data.split("|")
    method_name = parts[1]
    amount_str = parts[2]
    
    method = next((m for m in HELEKET_PAYMENT_METHODS if m["name"] == method_name), None)
    
    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
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
    payment_url = await generate_heleket_payment_link(amount, callback_query.message.chat.id, method)
    
    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=PAY_2, url=payment_url)],
            [InlineKeyboardButton(text=BACK, callback_data="balance")],
        ]
    )
    
    await edit_or_send_message(
        target_message=callback_query.message,
        text=HELEKET_PAYMENT_MESSAGE.format(amount=amount),
        reply_markup=confirm_keyboard,
        force_text=True,
    )
    
    await state.set_state(ReplenishBalanceHeleket.waiting_for_payment_confirmation)


async def generate_heleket_payment_link(amount: int, tg_id: int, method: dict) -> str:
    """
    Создание платежа в Heleket и получение ссылки на оплату
    """
    url = "https://api.heleket.com/v1/payment"
    
    unique_order_id = f"{int(time.time())}_{tg_id}"
    
    usd_amount = round(amount / HELEKET_CURRENCY_RATE, 2)
    
    data = {
        "amount": str(usd_amount),
        "currency": method["currency"],
        "order_id": unique_order_id,
        "url_success": HELEKET_SUCCESS_URL,
        "url_return": HELEKET_RETURN_URL,
        "url_callback": HELEKET_CALLBACK_URL,
        "additional_data": f"tg_id:{tg_id},rub_amount:{amount}",
    }
    
    if method.get("to_currency"):
        data["to_currency"] = method["to_currency"]
    
    json_data = json.dumps(data, separators=(',', ':'))
    base64_data = base64.b64encode(json_data.encode('utf-8')).decode('utf-8')
    sign_string = base64_data + HELEKET_API_KEY
    signature = hashlib.md5(sign_string.encode('utf-8')).hexdigest()
    
    headers = {
        "merchant": HELEKET_MERCHANT_ID,
        "sign": signature,
        "Content-Type": "application/json",
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=json_data, timeout=60) as resp:
                if resp.status == 200:
                    try:
                        resp_json = await resp.json()
                        if resp_json.get("state") == 0:  
                            payment_url = resp_json.get("result", {}).get("url")
                            if payment_url:
                                logger.info(f"Heleket payment URL created for user {tg_id}")
                                return payment_url
                            else:
                                logger.error(f"Heleket: No URL in response: {resp_json}")
                                return "https://heleket.com/"
                        else:
                            logger.error(f"Heleket: Unsuccessful response: {resp_json}")
                            return "https://heleket.com/"
                    except Exception as e:
                        logger.error(f"Heleket: Error parsing JSON response: {e}")
                        text = await resp.text()
                        logger.error(f"Heleket: Response content: {text}")
                        return "https://heleket.com/"
                else:
                    try:
                        error_json = await resp.json()
                        logger.error(f"Heleket API error: status={resp.status}, response={error_json}")
                    except Exception:
                        text = await resp.text()
                        logger.error(f"Heleket API error: status={resp.status}, non-JSON response: {text}")
                    return "https://heleket.com/"
    except Exception as e:
        logger.error(f"Error creating Heleket payment: {e}")
        return "https://heleket.com/" 