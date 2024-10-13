import logging
import uuid

from aiogram import Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiohttp import web
from yookassa import Configuration, Payment 

from bot import bot
from config import YOOKASSA_SECRET_KEY, YOOKASSA_SHOP_ID
from database import (add_connection, check_connection_exists, get_key_count,
                      update_balance)
from handlers.profile import process_callback_view_profile

router = Router()

logging.basicConfig(level=logging.DEBUG)

Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

logging.debug(f"Account ID: {YOOKASSA_SHOP_ID}")
logging.debug(f"Secret Key: {YOOKASSA_SECRET_KEY}")

class ReplenishBalanceState(StatesGroup):
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()

async def send_message_with_deletion(chat_id, text, reply_markup=None, state=None, message_key='last_message_id'):
    if state:
        try:
            state_data = await state.get_data()
            previous_message_id = state_data.get(message_key)

            if previous_message_id:
                await bot.delete_message(chat_id=chat_id, message_id=previous_message_id)
    
            sent_message = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
            await state.update_data({message_key: sent_message.message_id})
    
        except Exception as e:
            print(f"Ошибка при удалении/отправке сообщения: {e}")
            return None

    return sent_message

@router.callback_query(lambda c: c.data == 'replenish_balance')
async def process_callback_replenish_balance(callback_query: types.CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id

    key_count = await get_key_count(tg_id)
    
    if key_count == 0:
        exists = await check_connection_exists(tg_id)
        if not exists:
            await add_connection(tg_id, balance=0.0, trial=0)

    # Клавиатура с фиксированными суммами и новой кнопкой "Ввести сумму"
    amount_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='100 RUB', callback_data='amount_100'), InlineKeyboardButton(text='300 RUB', callback_data='amount_300')],
        [InlineKeyboardButton(text='600 RUB', callback_data='amount_600'), InlineKeyboardButton(text='1000 RUB', callback_data='amount_1000')],
        [InlineKeyboardButton(text='💰 Ввести свою сумму', callback_data='enter_custom_amount')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='back_to_profile')]
    ])
    
    await callback_query.message.edit_text(
        text="Выберите сумму пополнения:",
        reply_markup=amount_keyboard
    )
    await state.set_state(ReplenishBalanceState.choosing_amount)
    await callback_query.answer()

@router.callback_query(lambda c: c.data == 'back_to_profile')
async def back_to_profile_handler(callback_query: types.CallbackQuery, state: FSMContext):
    await process_callback_view_profile(callback_query, state)

@router.callback_query(lambda c: c.data.startswith('amount_'))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    data = callback_query.data.split('_', 1)

    if len(data) != 2:
        await send_message_with_deletion(callback_query.from_user.id, "Неверные данные для выбора суммы.", state=state, message_key='amount_error_message_id')
        return

    amount_str = data[1]
    try:
        amount = int(amount_str)
    except ValueError:
        await send_message_with_deletion(callback_query.from_user.id, "Некорректная сумма.", state=state, message_key='amount_error_message_id')
        return

    await state.update_data(amount=amount)
    await state.set_state(ReplenishBalanceState.waiting_for_payment_confirmation)

    state_data = await state.get_data()
    customer_name = callback_query.from_user.full_name
    customer_id = callback_query.from_user.id

    # Используем tg_id для email
    customer_email = f"{customer_id}@solo.net"

    payment = Payment.create({
        "amount": {
            "value": str(amount),
            "currency": "RUB"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": "https://pocomacho.ru/" 
        },
        "capture": True,
        "description": "Пополнение баланса",
        "receipt": {
            "customer": {
                "full_name": customer_name,
                "email": customer_email,  # Используем email в формате "tg_id@solo.net"
                "phone": "79000000000"
            },
            "items": [
                {
                    "description": "Пополнение баланса",
                    "quantity": "1.00",
                    "amount": {
                        "value": str(amount),
                        "currency": "RUB"
                    },
                    "vat_code": 6
                }
            ]
        },
        "metadata": {
            "user_id": customer_id
        }
    }, uuid.uuid4())

    if payment['status'] == 'pending':
        payment_url = payment['confirmation']['confirmation_url']

        confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Пополнить', url=payment_url)],
            [InlineKeyboardButton(text='⬅️ Назад', callback_data='back_to_profile')]
        ])

        await callback_query.message.edit_text(
            text=f"Вы выбрали пополнение на {amount} рублей.",
            reply_markup=confirm_keyboard
        )
    else:
        await send_message_with_deletion(callback_query.from_user.id, "Ошибка при создании платежа.", state=state)

    await callback_query.answer()

async def send_payment_success_notification(user_id: int, amount: float):
    try:
        profile_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Перейти в профиль', callback_data='view_profile')]
        ])
        
        await bot.send_message(
            chat_id=user_id,
            text=f"Ваш баланс успешно пополнен на {amount} рублей. Спасибо за оплату!",
            reply_markup=profile_keyboard
        )
    except Exception as e:
        logging.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")

async def payment_webhook(request):
    event = await request.json()

    logging.debug(f"Webhook event received: {event}")

    if event['event'] == 'payment.succeeded':
        user_id_str = event['object']['metadata']['user_id']
        amount_str = event['object']['amount']['value']
        
        try:
            user_id = int(user_id_str)
            amount = float(amount_str)
            
            logging.debug(f"Payment succeeded for user_id: {user_id}, amount: {amount}")
            await update_balance(user_id, amount)

            await send_payment_success_notification(user_id, amount)

        except ValueError as e:
            logging.error(f"Ошибка конвертации user_id или amount: {e}")
            return web.Response(status=400)

    return web.Response(status=200)

@router.callback_query(lambda c: c.data == 'enter_custom_amount')
async def process_enter_custom_amount(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text(
        text="Введите сумму пополнения:"
    )
    await state.set_state(ReplenishBalanceState.entering_custom_amount)
    await callback_query.answer()

@router.message(State(ReplenishBalanceState.entering_custom_amount))
async def process_custom_amount_input(message: types.Message, state: FSMContext):
    # Проверяем, является ли введенное значение числом
    if message.text.isdigit():
        amount = int(message.text)
        if amount <= 0:
            await message.answer("Сумма должна быть больше нуля. Пожалуйста, введите сумму еще раз:")
            return
        
        await state.update_data(amount=amount)
        await state.set_state(ReplenishBalanceState.waiting_for_payment_confirmation)

        # Создание платежа
        try:
            payment = Payment.create({
                "amount": {
                    "value": str(amount),
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": "https://pocomacho.ru/"
                },
                "capture": True,
                "description": "Пополнение баланса",
                "receipt": {
                    "customer": {
                        "full_name": message.from_user.full_name,
                        "email": f"{message.from_user.id}@solo.net",  # Используем tg_id как email
                        "phone": "79000000000"  # Здесь можно использовать номер телефона клиента, если он доступен
                    },
                    "items": [
                        {
                            "description": "Пополнение баланса",
                            "quantity": "1.00",
                            "amount": {
                                "value": str(amount),
                                "currency": "RUB"
                            },
                            "vat_code": 6
                        }
                    ]
                },
                "metadata": {
                    "user_id": message.from_user.id
                }
            }, uuid.uuid4())

            if payment['status'] == 'pending':
                payment_url = payment['confirmation']['confirmation_url']

                confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text='Пополнить', url=payment_url)],
                    [InlineKeyboardButton(text='⬅️ Назад', callback_data='back_to_profile')]
                ])

                await message.answer(
                    text=f"Вы выбрали пополнение на {amount} рублей.",
                    reply_markup=confirm_keyboard
                )
            else:
                await message.answer("Ошибка при создании платежа.")
        except Exception as e:
            await message.answer(f"Произошла ошибка при обработке платежа: {str(e)}")
    else:
        await message.answer("Некорректный ввод. Пожалуйста, введите сумму числом:")