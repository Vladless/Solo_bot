import aiohttp
import hashlib
import hmac
import time
import json
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    KASSAI_ENABLE, KASSAI_API_KEY, KASSAI_SECRET_KEY, KASSAI_DOMAIN, KASSAI_SHOP_ID,
    REDIRECT_LINK, FAIL_REDIRECT_LINK, WEBHOOK_HOST, KASSAI_IP
)

from handlers.buttons import BACK, PAY_2, KASSAI_CARDS, KASSAI_SBP
from handlers.texts import (
    KASSAI_CARDS_DESCRIPTION, KASSAI_SBP_DESCRIPTION,
    KASSAI_PAYMENT_MESSAGE, ENTER_SUM, PAYMENT_OPTIONS, KASSAI_PAYMENT_TITLE
)
from handlers.utils import edit_or_send_message
from logger import logger

router = Router()


class ReplenishBalanceKassaiState(StatesGroup):
    choosing_method = State()
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


KASSAI_PAYMENT_METHODS = [
    {"enable": KASSAI_ENABLE, "method": 36, "name": "cards", "button": KASSAI_CARDS, "desc": KASSAI_CARDS_DESCRIPTION},
    {"enable": KASSAI_ENABLE, "method": 44, "name": "sbp", "button": KASSAI_SBP, "desc": KASSAI_SBP_DESCRIPTION},
]


@router.callback_query(F.data == "pay_kassai")
async def process_callback_pay_kassai(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession, method_name: str = None):
    tg_id = callback_query.message.chat.id
    logger.info(f"User {tg_id} initiated KassaAI payment.")
    
    if method_name:
        method = next((m for m in KASSAI_PAYMENT_METHODS if m["name"] == method_name and m["enable"]), None)
        if not method:
            await edit_or_send_message(
                target_message=callback_query.message,
                text="Ошибка: выбранный способ оплаты недоступен.",
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
                        callback_data=f'kassai_amount|{method_name}|{PAYMENT_OPTIONS[i]["callback_data"].split("|")[1]}',
                    ),
                    InlineKeyboardButton(
                        text=PAYMENT_OPTIONS[i + 1]["text"],
                        callback_data=f'kassai_amount|{method_name}|{PAYMENT_OPTIONS[i + 1]["callback_data"].split("|")[1]}',
                    ),
                )
            else:
                builder.row(
                    InlineKeyboardButton(
                        text=PAYMENT_OPTIONS[i]["text"],
                        callback_data=f'kassai_amount|{method_name}|{PAYMENT_OPTIONS[i]["callback_data"].split("|")[1]}',
                    )
                )
        builder.row(InlineKeyboardButton(text="Ввести сумму", callback_data=f"kassai_custom_amount|{method_name}"))
        builder.row(InlineKeyboardButton(text=BACK, callback_data="pay"))
        
        await callback_query.message.delete()
        new_message = await callback_query.message.answer(
            text=method["desc"],
            reply_markup=builder.as_markup(),
        )
        await state.update_data(message_id=new_message.message_id, chat_id=new_message.chat.id, kassai_method=method_name)
        await state.set_state(ReplenishBalanceKassaiState.choosing_amount)
        return
    
    builder = InlineKeyboardBuilder()
    for method in KASSAI_PAYMENT_METHODS:
        if method["enable"]:
            builder.row(InlineKeyboardButton(text=method["button"], callback_data=f'kassai_method|{method["name"]}'))
    builder.row(InlineKeyboardButton(text=BACK, callback_data="balance"))
    
    await callback_query.message.delete()
    new_message = await callback_query.message.answer(
        text="Выберите способ оплаты через KassaAI:",
        reply_markup=builder.as_markup(),
    )
    await state.update_data(message_id=new_message.message_id, chat_id=new_message.chat.id)
    await state.set_state(ReplenishBalanceKassaiState.choosing_method)


@router.callback_query(F.data.startswith("kassai_method|"))
async def process_method_selection(callback_query: types.CallbackQuery, state: FSMContext):
    method_name = callback_query.data.split("|")[1]
    method = next((m for m in KASSAI_PAYMENT_METHODS if m["name"] == method_name), None)
    
    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=types.InlineKeyboardMarkup(),
            force_text=True,
        )
        return
    
    await state.update_data(kassai_method=method_name)
    
    builder = InlineKeyboardBuilder()
    for i in range(0, len(PAYMENT_OPTIONS), 2):
        if i + 1 < len(PAYMENT_OPTIONS):
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'kassai_amount|{method_name}|{PAYMENT_OPTIONS[i]["callback_data"].split("|")[1]}',
                ),
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=f'kassai_amount|{method_name}|{PAYMENT_OPTIONS[i + 1]["callback_data"].split("|")[1]}',
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'kassai_amount|{method_name}|{PAYMENT_OPTIONS[i]["callback_data"].split("|")[1]}',
                )
            )
    builder.row(InlineKeyboardButton(text="Ввести сумму", callback_data=f"kassai_custom_amount|{method_name}"))
    builder.row(InlineKeyboardButton(text=BACK, callback_data="pay_kassai"))
    
    await callback_query.message.delete()
    new_message = await callback_query.message.answer(
        text=method["desc"],
        reply_markup=builder.as_markup(),
    )
    await state.update_data(message_id=new_message.message_id, chat_id=new_message.chat.id)
    await state.set_state(ReplenishBalanceKassaiState.choosing_amount)


@router.callback_query(F.data.startswith("kassai_custom_amount|"))
async def process_custom_amount_button(callback_query: types.CallbackQuery, state: FSMContext):
    method_name = callback_query.data.split("|")[1]
    await state.update_data(kassai_method=method_name)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"pay_kassai_{method_name}"))
    
    await edit_or_send_message(
        target_message=callback_query.message,
        text=ENTER_SUM,
        reply_markup=builder.as_markup(),
        force_text=True,
    )
    await state.set_state(ReplenishBalanceKassaiState.entering_custom_amount)


@router.message(ReplenishBalanceKassaiState.entering_custom_amount)
async def handle_custom_amount_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    method_name = data.get("kassai_method")
    method = next((m for m in KASSAI_PAYMENT_METHODS if m["name"] == method_name), None)
    
    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=types.InlineKeyboardMarkup(),
            force_text=True,
        )
        return
    
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
        if method_name == "sbp" and amount < 10:
            await edit_or_send_message(
                target_message=message,
                text="Минимальная сумма для оплаты через СБП — 10 рублей.",
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
    payment_url = await generate_kassai_payment_link(amount, message.chat.id, method)
    
    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=PAY_2, url=payment_url)],
            [InlineKeyboardButton(text=BACK, callback_data="pay_kassai")],
        ]
    )
    
    await edit_or_send_message(
        target_message=message,
        text=KASSAI_PAYMENT_MESSAGE.format(amount=amount),
        reply_markup=confirm_keyboard,
        force_text=True,
    )
    await state.set_state(ReplenishBalanceKassaiState.waiting_for_payment_confirmation)


@router.callback_query(F.data.startswith("kassai_amount|"))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    parts = callback_query.data.split("|")
    method_name = parts[1]
    amount_str = parts[2]
    
    method = next((m for m in KASSAI_PAYMENT_METHODS if m["name"] == method_name), None)
    
    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
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
    payment_url = await generate_kassai_payment_link(amount, callback_query.message.chat.id, method)
    
    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=PAY_2, url=payment_url)],
            [InlineKeyboardButton(text=BACK, callback_data="pay_kassai")],
        ]
    )
    
    await edit_or_send_message(
        target_message=callback_query.message,
        text=KASSAI_PAYMENT_MESSAGE.format(amount=amount),
        reply_markup=confirm_keyboard,
        force_text=True,
    )
    await state.set_state(ReplenishBalanceKassaiState.waiting_for_payment_confirmation)


async def generate_kassai_payment_link(amount: int, tg_id: int, method: dict) -> str:
    """
    Создание заказа в KassaAI и получение ссылки на оплату
    """
    nonce = int(time.time())
    unique_payment_id = f"{nonce}_{tg_id}"
    url = f"https://api.fk.life/v1/orders/create?paymentId={unique_payment_id}"
    
    headers = {
        "Content-Type": "application/json",
    }
    
    client_email = f"{tg_id}@{KASSAI_DOMAIN}"
    client_ip = KASSAI_IP
    
    data_for_signature = {
        "shopId": KASSAI_SHOP_ID,
        "nonce": nonce,
        "i": method["method"],
        "email": client_email,
        "ip": client_ip,
        "amount": int(amount),
        "currency": "RUB"
    }
    
    sorted_keys = sorted(data_for_signature.keys())
    values = [str(data_for_signature[key]) for key in sorted_keys]
    sign_string = "|".join(values)
    
    signature = hmac.new(
        KASSAI_API_KEY.encode('utf-8'),
        sign_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    data = data_for_signature.copy()
    data["signature"] = signature
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=60) as resp:
                if resp.status == 200:
                    try:
                        resp_json = await resp.json()
                        if resp_json.get("type") == "success":
                            payment_url = resp_json.get("location")
                            if payment_url:
                                logger.info(f"KassaAI payment URL created for user {tg_id}")
                                return payment_url
                            else:
                                logger.error(f"KassaAI: No location in response: {resp_json}")
                                return "https://fk.life/"
                        else:
                            logger.error(f"KassaAI: Unsuccessful response: {resp_json}")
                            return "https://fk.life/"
                    except Exception as e:
                        logger.error(f"KassaAI: Error parsing JSON response: {e}")
                        text = await resp.text()
                        logger.error(f"KassaAI: Response content: {text}")
                        return "https://fk.life/"
                else:
                    try:
                        error_json = await resp.json()
                        logger.error(f"KassaAI API error: status={resp.status}, response={error_json}")
                    except Exception:
                        text = await resp.text()
                        logger.error(f"KassaAI API error: status={resp.status}, non-JSON response: {text}")
                    return "https://fk.life/"
    except Exception as e:
        logger.error(f"Error creating KassaAI order: {e}")
        return "https://fk.life/"


def verify_kassai_signature(data: dict, signature: str) -> bool:
    """
    Проверка подписи вебхука KassaAI согласно документации FreeKassa
    Формат: MERCHANT_ID:AMOUNT:SECRET_KEY2:MERCHANT_ORDER_ID
    """
    try:
        sign_string = (
            f"{KASSAI_SHOP_ID}:"
            f"{data.get('AMOUNT', '')}:"
            f"{KASSAI_SECRET_KEY}:"
            f"{data.get('MERCHANT_ORDER_ID', '')}"
        )
        
        expected_signature = hashlib.md5(sign_string.encode('utf-8')).hexdigest()
        
        result = signature.upper() == expected_signature.upper()
        
        if not result:
            logger.error(f"KassaAI signature mismatch. Expected: {expected_signature}, Got: {signature}")
            logger.error(f"Sign string: {sign_string}")
        
        return result
    except Exception as e:
        logger.error(f"Ошибка проверки подписи KassaAI: {e}")
        return False 