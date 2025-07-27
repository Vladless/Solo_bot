import aiohttp
import hashlib
import jwt
import time
import json
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    CRYPTOCLOUD_ENABLE, CRYPTOCLOUD_API_KEY, CRYPTOCLOUD_SHOP_ID, CRYPTOCLOUD_SECRET,
    CRYPTOCLOUD_CURRENCY_RATE
)

from handlers.buttons import BACK, PAY_2, CRYPTOCLOUD_CRYPTO
from handlers.texts import (
    CRYPTOCLOUD_CRYPTO_DESCRIPTION, CRYPTOCLOUD_PAYMENT_MESSAGE, ENTER_SUM, PAYMENT_OPTIONS, CRYPTOCLOUD_PAYMENT_TITLE
)
from handlers.utils import edit_or_send_message
from logger import logger

router = Router()


class ReplenishBalanceCryptoCloudState(StatesGroup):
    choosing_method = State()
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


CRYPTOCLOUD_PAYMENT_METHODS = [
    {"enable": CRYPTOCLOUD_ENABLE, "name": "crypto", "button": CRYPTOCLOUD_CRYPTO, "desc": CRYPTOCLOUD_CRYPTO_DESCRIPTION},
]


@router.callback_query(F.data == "pay_cryptocloud")
async def process_callback_pay_cryptocloud(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession, method_name: str = None):
    try:
        tg_id = callback_query.message.chat.id
        logger.info(f"User {tg_id} initiated CryptoCloud payment.")
        
        await state.clear()
        
        if method_name:
            method = next((m for m in CRYPTOCLOUD_PAYMENT_METHODS if m["name"] == method_name and m["enable"]), None)
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
                            callback_data=f'cryptocloud_amount|{method_name}|{PAYMENT_OPTIONS[i]["callback_data"].split("|")[1]}',
                        ),
                        InlineKeyboardButton(
                            text=PAYMENT_OPTIONS[i + 1]["text"],
                            callback_data=f'cryptocloud_amount|{method_name}|{PAYMENT_OPTIONS[i + 1]["callback_data"].split("|")[1]}',
                        ),
                    )
                else:
                    builder.row(
                        InlineKeyboardButton(
                            text=PAYMENT_OPTIONS[i]["text"],
                            callback_data=f'cryptocloud_amount|{method_name}|{PAYMENT_OPTIONS[i]["callback_data"].split("|")[1]}',
                        )
                    )
            builder.row(InlineKeyboardButton(text="Ввести сумму", callback_data=f"cryptocloud_custom_amount|{method_name}"))
            builder.row(InlineKeyboardButton(text=BACK, callback_data="balance"))
            
            await edit_or_send_message(
                target_message=callback_query.message,
                text=method["desc"],
                reply_markup=builder.as_markup(),
                force_text=True,
            )
            await state.update_data(cryptocloud_method=method_name)
            await state.set_state(ReplenishBalanceCryptoCloudState.choosing_amount)
            return
        
        builder = InlineKeyboardBuilder()
        for method in CRYPTOCLOUD_PAYMENT_METHODS:
            if method["enable"]:
                builder.row(InlineKeyboardButton(text=method["button"], callback_data=f'cryptocloud_method|{method["name"]}'))
        builder.row(InlineKeyboardButton(text=BACK, callback_data="balance"))
        
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Выберите способ оплаты через CryptoCloud:",
            reply_markup=builder.as_markup(),
            force_text=True,
        )
        await state.update_data(message_id=callback_query.message.message_id, chat_id=callback_query.message.chat.id)
        await state.set_state(ReplenishBalanceCryptoCloudState.choosing_method)
        
    except Exception as e:
        logger.error(f"Error in process_callback_pay_cryptocloud for user {callback_query.message.chat.id}: {e}")
        await callback_query.answer("Произошла ошибка при инициализации платежа. Попробуйте позже.", show_alert=True)


@router.callback_query(F.data.startswith("cryptocloud_method|"))
async def process_method_selection(callback_query: types.CallbackQuery, state: FSMContext):
    method_name = callback_query.data.split("|")[1]
    method = next((m for m in CRYPTOCLOUD_PAYMENT_METHODS if m["name"] == method_name), None)
    
    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            force_text=True,
        )
        return
    
    await state.update_data(cryptocloud_method=method_name)
    
    builder = InlineKeyboardBuilder()
    for i in range(0, len(PAYMENT_OPTIONS), 2):
        if i + 1 < len(PAYMENT_OPTIONS):
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'cryptocloud_amount|{method_name}|{PAYMENT_OPTIONS[i]["callback_data"].split("|")[1]}',
                ),
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=f'cryptocloud_amount|{method_name}|{PAYMENT_OPTIONS[i + 1]["callback_data"].split("|")[1]}',
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'cryptocloud_amount|{method_name}|{PAYMENT_OPTIONS[i]["callback_data"].split("|")[1]}',
                )
            )
    builder.row(InlineKeyboardButton(text="Ввести сумму", callback_data=f"cryptocloud_custom_amount|{method_name}"))
    builder.row(InlineKeyboardButton(text=BACK, callback_data="pay_cryptocloud"))
    
    await edit_or_send_message(
        target_message=callback_query.message,
        text=method["desc"],
        reply_markup=builder.as_markup(),
        force_text=True,
    )
    await state.update_data(message_id=callback_query.message.message_id, chat_id=callback_query.message.chat.id)
    await state.set_state(ReplenishBalanceCryptoCloudState.choosing_amount)


@router.callback_query(F.data.startswith("cryptocloud_custom_amount|"))
async def process_custom_amount_button(callback_query: types.CallbackQuery, state: FSMContext):
    method_name = callback_query.data.split("|")[1]
    await state.update_data(cryptocloud_method=method_name)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"pay_cryptocloud_{method_name}"))
    
    await edit_or_send_message(
        target_message=callback_query.message,
        text=ENTER_SUM,
        reply_markup=builder.as_markup(),
        force_text=True,
    )
    await state.set_state(ReplenishBalanceCryptoCloudState.entering_custom_amount)


@router.message(ReplenishBalanceCryptoCloudState.entering_custom_amount)
async def handle_custom_amount_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    method_name = data.get("cryptocloud_method")
    method = next((m for m in CRYPTOCLOUD_PAYMENT_METHODS if m["name"] == method_name), None)
    
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
    payment_url = await generate_cryptocloud_payment_link(amount, message.chat.id, method)
    
    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=PAY_2, url=payment_url)],
            [InlineKeyboardButton(text=BACK, callback_data="balance")],
        ]
    )
    
    await edit_or_send_message(
        target_message=message,
        text=CRYPTOCLOUD_PAYMENT_MESSAGE.format(amount=amount),
        reply_markup=confirm_keyboard,
        force_text=True,
    )
    
    await state.set_state(ReplenishBalanceCryptoCloudState.waiting_for_payment_confirmation)


@router.callback_query(F.data.startswith("cryptocloud_amount|"))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    parts = callback_query.data.split("|")
    method_name = parts[1]
    amount_str = parts[2]
    
    method = next((m for m in CRYPTOCLOUD_PAYMENT_METHODS if m["name"] == method_name), None)
    
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
    payment_url = await generate_cryptocloud_payment_link(amount, callback_query.message.chat.id, method)
    
    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=PAY_2, url=payment_url)],
            [InlineKeyboardButton(text=BACK, callback_data="balance")],
        ]
    )
    
    await edit_or_send_message(
        target_message=callback_query.message,
        text=CRYPTOCLOUD_PAYMENT_MESSAGE.format(amount=amount),
        reply_markup=confirm_keyboard,
        force_text=True,
    )
    
    await state.set_state(ReplenishBalanceCryptoCloudState.waiting_for_payment_confirmation)


async def generate_cryptocloud_payment_link(amount: int, tg_id: int, method: dict) -> str:
    """
    Создание счета в CryptoCloud и получение ссылки на оплату
    """
    url = "https://api.cryptocloud.plus/v2/invoice/create"

    unique_order_id = f"{int(time.time())}_{tg_id}_{amount}"

    usd_amount = round(amount / CRYPTOCLOUD_CURRENCY_RATE, 2)
    
    headers = {
        "Authorization": f"Token {CRYPTOCLOUD_API_KEY}",
        "Content-Type": "application/json",
    }
    
    data = {
        "shop_id": CRYPTOCLOUD_SHOP_ID,
        "amount": usd_amount,
        "currency": "USD",
        "order_id": unique_order_id,
        "add_fields": {
            "available_currencies": ["USDT_TRC20", "USDT_ERC20", "BTC", "ETH", "TRX"],
            "time_to_pay": {"hours": 24, "minutes": 0}
        }
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=60) as resp:
                if resp.status == 200:
                    try:
                        resp_json = await resp.json()
                        if resp_json.get("status") == "success":
                            payment_url = resp_json.get("result", {}).get("link")
                            if payment_url:
                                logger.info(f"CryptoCloud payment URL created for user {tg_id}")
                                return payment_url
                            else:
                                logger.error(f"CryptoCloud: No link in response: {resp_json}")
                                return "https://cryptocloud.plus/"
                        else:
                            logger.error(f"CryptoCloud: Unsuccessful response: {resp_json}")
                            return "https://cryptocloud.plus/"
                    except Exception as e:
                        logger.error(f"CryptoCloud: Error parsing JSON response: {e}")
                        text = await resp.text()
                        logger.error(f"CryptoCloud: Response content: {text}")
                        return "https://cryptocloud.plus/"
                else:
                    try:
                        error_json = await resp.json()
                        logger.error(f"CryptoCloud API error: status={resp.status}, response={error_json}")
                    except Exception:
                        text = await resp.text()
                        logger.error(f"CryptoCloud API error: status={resp.status}, non-JSON response: {text}")
                    return "https://cryptocloud.plus/"
    except Exception as e:
        logger.error(f"Error creating CryptoCloud payment: {e}")
        return "https://cryptocloud.plus/"


def verify_cryptocloud_jwt_token(token: str) -> dict:
    """
    Проверка JWT токена от CryptoCloud
    """
    try:
        payload = jwt.decode(token, CRYPTOCLOUD_SECRET, algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        logger.error("CryptoCloud JWT token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.error(f"CryptoCloud JWT token invalid: {e}")
        return None
    except Exception as e:
        logger.error(f"CryptoCloud JWT verification error: {e}")
        return None 