import uuid
from urllib.parse import urlencode

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
from loguru import logger

from bot import bot
from config import ROBOKASSA_LOGIN, ROBOKASSA_PASSWORD1, ROBOKASSA_PASSWORD2, ROBOKASSA_TEST_MODE
from database import add_connection, check_connection_exists, get_key_count, update_balance
from handlers.texts import PAYMENT_OPTIONS

router = Router()


class ReplenishBalanceState(StatesGroup):
    choosing_amount_robokassa = State()
    waiting_for_payment_confirmation_robokassa = State()
    entering_custom_amount_robokassa = State()


def calculate_signature(*args) -> str:
    """Создание подписи SHA-256 для Робокассы."""
    import hashlib

    data = ":".join(map(str, args))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def generate_payment_link(
    merchant_login, password1, cost, inv_id, description, is_test
):
    """Генерация ссылки для оплаты через Робокассу."""
    signature = calculate_signature(merchant_login, cost, inv_id, password1)
    payment_url = "https://auth.robokassa.ru/Merchant/Index.aspx"
    params = {
        "MerchantLogin": merchant_login,
        "OutSum": cost,
        "InvId": inv_id,
        "Description": description,
        "SignatureValue": signature,
        "IsTest": int(is_test),
    }
    return f"{payment_url}?{urlencode(params)}"


async def send_message_with_deletion(
    chat_id, text, reply_markup=None, state=None, message_key="last_message_id"
):
    """Отправка сообщения с удалением предыдущего."""
    if state:
        try:
            state_data = await state.get_data()
            previous_message_id = state_data.get(message_key)

            if previous_message_id:
                await bot.delete_message(
                    chat_id=chat_id, message_id=previous_message_id
                )

            sent_message = await bot.send_message(
                chat_id=chat_id, text=text, reply_markup=reply_markup
            )
            await state.update_data({message_key: sent_message.message_id})

        except Exception as e:
            logger.error(f"Ошибка при удалении/отправке сообщения: {e}")
            return None

    return sent_message


@router.callback_query(F.data == "pay_robokassa")
async def process_callback_pay_robokassa(
    callback_query: types.CallbackQuery, state: FSMContext
):
    tg_id = callback_query.from_user.id

    builder = InlineKeyboardBuilder()
    for i in range(0, len(PAYMENT_OPTIONS), 2):
        if i + 1 < len(PAYMENT_OPTIONS):
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'robokassa_{PAYMENT_OPTIONS[i]["callback_data"]}',
                ),
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=f'robokassa_{PAYMENT_OPTIONS[i + 1]["callback_data"]}',
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'robokassa_{PAYMENT_OPTIONS[i]["callback_data"]}',
                )
            )
    builder.row(
        InlineKeyboardButton(
            text="💰 Ввести свою сумму", callback_data="enter_custom_amount_robokassa"
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile"))

    key_count = await get_key_count(tg_id)

    if key_count == 0:
        exists = await check_connection_exists(tg_id)
        if not exists:
            await add_connection(tg_id, balance=0.0, trial=0)

    try:
        await bot.delete_message(
            chat_id=tg_id, message_id=callback_query.message.message_id
        )
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

    await bot.send_message(
        chat_id=tg_id,
        text="Выберите сумму пополнения:",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(ReplenishBalanceState.choosing_amount_robokassa)
    await callback_query.answer()


@router.callback_query(F.data.startswith("robokassa_amount|"))
async def process_amount_selection(
    callback_query: types.CallbackQuery, state: FSMContext
):
    data = callback_query.data.split("|", 1)
    if len(data) != 2:
        await send_message_with_deletion(
            callback_query.from_user.id,
            "Неверные данные для выбора суммы.",
            state=state,
        )
        return

    amount_str = data[1]
    try:
        amount = int(amount_str)
    except ValueError:
        await send_message_with_deletion(
            callback_query.from_user.id, "Некорректная сумма.", state=state
        )
        return

    await state.update_data(amount=amount)
    inv_id = uuid.uuid4().hex[:8]
    payment_url = generate_payment_link(
        ROBOKASSA_LOGIN,
        ROBOKASSA_PASSWORD1,
        amount,
        inv_id,
        "Пополнение баланса",
        ROBOKASSA_TEST_MODE,
    )

    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оплатить", url=payment_url)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="pay")],
        ]
    )

    await callback_query.message.edit_text(
        text=f"Вы выбрали пополнение на {amount} рублей. Для оплаты перейдите по ссылке ниже:",
        reply_markup=confirm_keyboard,
    )
    await callback_query.answer()


async def robokassa_webhook(request):
    """Обработка уведомлений от Робокассы."""
    params = dict(await request.post())
    logger.debug(f"Webhook event received: {params}")

    received_signature = params.get("SignatureValue")
    amount = params.get("OutSum")
    inv_id = params.get("InvId")

    signature = calculate_signature(amount, inv_id, ROBOKASSA_PASSWORD2)

    if signature.lower() == received_signature.lower():
        user_id = params.get("Shp_user_id")
        await update_balance(int(user_id), float(amount))
        await send_payment_success_notification(user_id, float(amount))
        return web.Response(text=f"OK{inv_id}")
    else:
        return web.Response(status=400)


async def send_payment_success_notification(user_id: int, amount: float):
    """Уведомление о успешном пополнении."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Перейти в профиль", callback_data="view_profile")
    )

    await bot.send_message(
        chat_id=user_id,
        text=f"Ваш баланс успешно пополнен на {amount} рублей. Спасибо за оплату!",
        reply_markup=builder.as_markup(),
    )
