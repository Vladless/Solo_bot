from aiocryptopay import AioCryptoPay, Networks
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

from bot import bot
from config import CRYPTO_BOT_ENABLE, CRYPTO_BOT_TOKEN, RUB_TO_USDT
from database import add_connection, check_connection_exists, get_key_count, update_balance
from handlers.texts import PAYMENT_OPTIONS
from logger import logger

router = Router()

if CRYPTO_BOT_ENABLE:
    crypto = AioCryptoPay(token=CRYPTO_BOT_TOKEN, network=Networks.MAIN_NET)


class ReplenishBalanceState(StatesGroup):
    choosing_amount_crypto = State()
    waiting_for_payment_confirmation_crypto = State()
    entering_custom_amount_crypto = State()


async def send_message_with_deletion(
    chat_id, text, reply_markup=None, state=None, message_key="last_message_id"
):
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


@router.callback_query(F.data == "pay_cryptobot")
async def process_callback_pay_cryptobot(
    callback_query: types.CallbackQuery, state: FSMContext
):
    tg_id = callback_query.from_user.id

    builder = InlineKeyboardBuilder()

    for i in range(0, len(PAYMENT_OPTIONS), 2):
        if i + 1 < len(PAYMENT_OPTIONS):
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'crypto_{PAYMENT_OPTIONS[i]["callback_data"]}',
                ),
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=f'crypto_{PAYMENT_OPTIONS[i + 1]["callback_data"]}',
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'crypto_{PAYMENT_OPTIONS[i]["callback_data"]}',
                )
            )
    builder.row(
        InlineKeyboardButton(
            text="💰 Ввести свою сумму", callback_data="enter_custom_amount_crypto"
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

    await state.set_state(ReplenishBalanceState.choosing_amount_crypto)
    await callback_query.answer()


@router.callback_query(F.data.startswith("crypto_amount|"))
async def process_amount_selection(
    callback_query: types.CallbackQuery, state: FSMContext
):
    data = callback_query.data.split("|", 1)

    if len(data) != 2:
        try:
            await callback_query.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

        await callback_query.message.answer("Неверные данные для выбора суммы.")
        return

    amount_str = data[1]
    try:
        amount = int(amount_str)
    except ValueError:
        try:
            await callback_query.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

        await callback_query.message.answer("Некорректная сумма.")
        return

    await state.update_data(amount=amount)
    await state.set_state(ReplenishBalanceState.waiting_for_payment_confirmation_crypto)

    try:
        try:
            await callback_query.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

        invoice = await crypto.create_invoice(
            asset="USDT",
            amount=str(int(amount // RUB_TO_USDT)),
            description=f"Пополнения баланса на {amount} руб",
            payload=f"{callback_query.from_user.id}:{int(amount)}",
        )

        if hasattr(invoice, "bot_invoice_url"):
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text="Пополнить", url=invoice.bot_invoice_url)
            )
            builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="pay"))
            await bot.send_message(
                chat_id=callback_query.from_user.id,
                text=f"Вы выбрали пополнение на {amount} рублей.",
                reply_markup=builder.as_markup(),
            )
        else:
            await callback_query.message.answer("Ошибка при создании платежа.")
    except Exception as e:
        logger.error(f"Ошибка при создании платежа: {e}")
        await callback_query.message.answer("Произошла ошибка при создании платежа.")

    await callback_query.answer()


async def send_payment_success_notification(user_id: int, amount: float):
    try:
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="Перейти в профиль", callback_data="view_profile")
        )
        await bot.send_message(
            chat_id=user_id,
            text=f"Ваш баланс успешно пополнен на {amount} рублей. Спасибо за оплату!",
            reply_markup=builder.as_markup(),
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")


async def cryptobot_webhook(request):
    try:
        data = await request.json()
        logger.info(f"Получены данные вебхука: {data}")
        if data.get("update_type") == "invoice_paid":
            await process_crypto_payment(data["payload"])
            return web.Response(status=200)
        else:
            logger.warning(
                f"Неподдерживаемый тип обновления: {data.get('update_type')}"
            )
            return web.Response(status=400)
    except Exception as e:
        logger.error(f"Ошибка обработки вебхука: {e}")
        return web.Response(status=500)


async def process_crypto_payment(payload):
    if payload["status"] == "paid":
        custom_payload = payload["payload"]
        user_id_str, amount_str = custom_payload.split(":")
        try:
            user_id = int(user_id_str)
            amount = int(amount_str)
            logger.debug(f"Payment succeeded for user_id: {user_id}, amount: {amount}")
            await update_balance(user_id, amount)
            await send_payment_success_notification(user_id, amount)
        except ValueError as e:
            logger.error(f"Ошибка конвертации user_id или amount: {e}")
    else:
        logger.warning(f"Получен неоплаченный инвойс: {payload}")


@router.callback_query(F.data == "enter_custom_amount_crypto")
async def process_enter_custom_amount(
    callback_query: types.CallbackQuery, state: FSMContext
):
    await callback_query.message.edit_text(text="Введите сумму пополнения:")
    await state.set_state(ReplenishBalanceState.entering_custom_amount_crypto)
    await callback_query.answer()


@router.message(ReplenishBalanceState.entering_custom_amount_crypto)
async def process_custom_amount_input(message: types.Message, state: FSMContext):
    if message.text.isdigit():
        amount = int(message.text)
        if amount // RUB_TO_USDT <= 0:
            await message.answer(
                f"Сумма должна быть больше {RUB_TO_USDT}. Пожалуйста, введите сумму еще раз:"
            )
            return

        await state.update_data(amount=amount)
        await state.set_state(
            ReplenishBalanceState.waiting_for_payment_confirmation_crypto
        )
        try:
            invoice = await crypto.create_invoice(
                asset="USDT",
                amount=str(int(amount // RUB_TO_USDT)),
                description=f"Пополнения баланса на {amount} руб",
                payload=f"{message.from_user.id}:{amount}",
            )

            if hasattr(invoice, "bot_invoice_url"):
                builder = InlineKeyboardBuilder()
                builder.row(
                    InlineKeyboardButton(text="Пополнить", url=invoice.bot_invoice_url)
                )
                builder.row(
                    InlineKeyboardButton(text="⬅️ Назад", callback_data="pay"),
                )
                await message.message.edit_text(
                    text=f"Вы выбрали пополнение на {amount} рублей.",
                    reply_markup=builder.as_markup(),
                )
            else:
                await send_message_with_deletion(
                    message.from_user.id, "Ошибка при создании платежа.", state=state
                )
        except Exception as e:
            logger.error(f"Ошибка при создании платежа: {e}")
            await message.answer("Произошла ошибка при создании платежа.")
    else:
        await message.answer("Некорректная сумма. Пожалуйста, введите сумму еще раз:")
