import hashlib

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
from loguru import logger
from robokassa import HashAlgorithm, Robokassa

from bot import bot
from config import ROBOKASSA_LOGIN, ROBOKASSA_PASSWORD1, ROBOKASSA_PASSWORD2, ROBOKASSA_TEST_MODE
from database import add_connection, check_connection_exists, get_key_count, update_balance
from handlers.texts import PAYMENT_OPTIONS

router = Router()


class ReplenishBalanceState(StatesGroup):
    choosing_amount_robokassa = State()
    waiting_for_payment_confirmation_robokassa = State()


robokassa = Robokassa(
    merchant_login=ROBOKASSA_LOGIN,
    password1=ROBOKASSA_PASSWORD1,
    password2=ROBOKASSA_PASSWORD2,
    algorithm=HashAlgorithm.md5,
    is_test=ROBOKASSA_TEST_MODE,
)

logger.info("Robokassa initialized with login: {}", ROBOKASSA_LOGIN)


def generate_payment_link(amount, inv_id, description):
    """Генерация ссылки на оплату."""
    logger.debug(
        f"Generating payment link for amount: {amount}, inv_id: {inv_id}, description: {description}"
    )
    payment_link = robokassa._payment.link.generate_by_script(
        out_sum=amount,
        inv_id=inv_id,
        description="description",
    )
    logger.info(f"Generated payment link: {payment_link}")
    return payment_link


async def send_message_with_deletion(
    chat_id, text, reply_markup=None, state=None, message_key="last_message_id"
):
    if state:
        try:
            state_data = await state.get_data()
            previous_message_id = state_data.get(message_key)

            if previous_message_id:
                logger.debug(
                    f"Deleting previous message with ID: {previous_message_id}"
                )
                await bot.delete_message(
                    chat_id=chat_id, message_id=previous_message_id
                )

            sent_message = await bot.send_message(
                chat_id=chat_id, text=text, reply_markup=reply_markup
            )
            await state.update_data({message_key: sent_message.message_id})

            logger.debug(f"Sent new message with ID: {sent_message.message_id}")
        except Exception as e:
            logger.error(f"Ошибка при удалении/отправке сообщения: {e}")
            return None

    return sent_message


@router.callback_query(F.data == "pay_robokassa")
async def process_callback_pay_robokassa(
    callback_query: types.CallbackQuery, state: FSMContext
):
    tg_id = callback_query.from_user.id
    logger.info(f"User {tg_id} initiated Robokassa payment.")

    builder = InlineKeyboardBuilder()
    for i in range(0, len(PAYMENT_OPTIONS), 2):
        if i + 1 < len(PAYMENT_OPTIONS):
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'robokassa_amount|{PAYMENT_OPTIONS[i]["callback_data"]}',
                ),
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=f'robokassa_amount|{PAYMENT_OPTIONS[i + 1]["callback_data"]}',
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'robokassa_amount|{PAYMENT_OPTIONS[i]["callback_data"]}',
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
            logger.info(f"Created new connection for user {tg_id} with balance 0.0.")

    try:
        await bot.delete_message(
            chat_id=tg_id, message_id=callback_query.message.message_id
        )
        logger.debug(f"Deleted message with ID: {callback_query.message.message_id}")
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

    await bot.send_message(
        chat_id=tg_id,
        text="Выберите сумму пополнения:",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(ReplenishBalanceState.choosing_amount_robokassa)
    logger.info(f"Displayed amount selection for user {tg_id}.")
    await callback_query.answer()


@router.callback_query(F.data.startswith("robokassa_amount|"))
async def process_amount_selection(
    callback_query: types.CallbackQuery, state: FSMContext
):
    logger.info(f"Получены данные callback_data: {callback_query.data}")

    data = callback_query.data.split("|")
    if len(data) != 3 or data[1] != "amount":
        logger.error("Ошибка: callback_data не соответствует формату.")
        await send_message_with_deletion(
            chat_id=callback_query.from_user.id,
            text="Неверные данные для выбора суммы.",
            state=state,
        )
        await callback_query.answer("Ошибка: данные повреждены.")
        return

    amount_str = data[2]
    try:
        amount = int(amount_str)
        if amount <= 0:
            raise ValueError("Сумма должна быть положительным числом.")
    except ValueError as e:
        logger.error(f"Некорректное значение суммы: {amount_str}. Ошибка: {e}")
        await send_message_with_deletion(
            chat_id=callback_query.from_user.id,
            text="Некорректная сумма. Попробуйте снова.",
            state=state,
        )
        await callback_query.answer("Некорректная сумма.")
        return

    await state.update_data(amount=amount)
    logger.info(f"User {callback_query.from_user.id} selected amount: {amount}.")

    tg_id = callback_query.from_user.id
    payment_url = generate_payment_link(
        amount,
        tg_id,
        "Пополнение баланса",
    )

    logger.info(f"Payment URL for user {callback_query.from_user.id}: {payment_url}")

    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оплатить", url=payment_url)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="pay_robokassa")],
        ]
    )

    await callback_query.message.edit_text(
        text=f"Вы выбрали пополнение на {amount} рублей. Для оплаты перейдите по ссылке ниже:",
        reply_markup=confirm_keyboard,
    )
    logger.info(f"Payment link sent to user {callback_query.from_user.id}.")
    await callback_query.answer()


async def robokassa_webhook(request):
    """Обработка webhook-уведомлений от Robokassa."""
    try:
        params = await request.post()
        logger.debug(f"Received webhook params: {params}")

        if not check_payment_signature(params):
            logger.error("Неверная подпись или данные запроса.")
            return web.Response(status=400)

        amount = params.get("OutSum")
        inv_id = params.get("InvId")

        if not amount or not inv_id:
            logger.error("Отсутствуют обязательные параметры.")
            return web.Response(status=400)

        tg_id = inv_id

        await update_balance(int(tg_id), float(amount))
        await send_payment_success_notification(tg_id, float(amount))

        logger.info(f"Payment successful. Balance updated for user {tg_id}.")
        return web.Response(text=f"OK{inv_id}")
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return web.Response(status=500)


def check_payment_signature(params):
    """Проверка подписи запроса от Robokassa."""
    out_sum = params.get("OutSum")
    inv_id = params.get("InvId")
    signature_value = params.get("SignatureValue")

    signature_string = f"{out_sum}:{inv_id}:{ROBOKASSA_PASSWORD2}"

    expected_signature = (
        hashlib.md5(signature_string.encode("utf-8")).hexdigest().upper()
    )

    logger.debug(f"Expected signature: {expected_signature}")
    logger.debug(f"Received signature: {signature_value}")

    return signature_value == expected_signature


async def send_payment_success_notification(user_id: int, amount: float):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Перейти в профиль", callback_data="view_profile")
    )

    await bot.send_message(
        chat_id=user_id,
        text=f"Ваш баланс успешно пополнен на {amount} рублей. Спасибо за оплату!",
        reply_markup=builder.as_markup(),
    )
    logger.info(f"Sent payment success notification to user {user_id}.")


@router.callback_query(F.data == "enter_custom_amount_robokassa")
async def process_custom_amount_selection(
    callback_query: types.CallbackQuery, state: FSMContext
):
    tg_id = callback_query.from_user.id
    logger.info(f"User {tg_id} chose to enter a custom amount.")

    await callback_query.message.edit_text(
        text="Пожалуйста, введите сумму пополнения в рублях (например, 150):"
    )
    await state.set_state(
        ReplenishBalanceState.waiting_for_payment_confirmation_robokassa
    )
    await callback_query.answer()


@router.message(ReplenishBalanceState.waiting_for_payment_confirmation_robokassa)
async def handle_custom_amount_input(message: types.Message, state: FSMContext):
    tg_id = message.from_user.id
    logger.info(f"User {tg_id} entered custom amount: {message.text}")

    try:
        amount = int(message.text)
        if amount <= 0:
            raise ValueError("Сумма должна быть положительным числом.")

        await state.update_data(amount=amount)

        payment_url = generate_payment_link(
            amount,
            tg_id,
            "Пополнение баланса",
        )

        logger.info(f"Generated payment link for user {tg_id}: {payment_url}")

        confirm_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Оплатить", url=payment_url)],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="pay_robokassa")],
            ]
        )

        await message.answer(
            text=f"Вы выбрали пополнение на {amount} рублей. Для оплаты перейдите по ссылке ниже:",
            reply_markup=confirm_keyboard,
        )
        await state.clear()
    except ValueError as e:
        logger.error(f"Некорректная сумма от пользователя {tg_id}: {e}")
        await message.answer(
            text="Введите корректную сумму в рублях (целое положительное число)."
        )
