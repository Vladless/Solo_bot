from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import bot
from config import RUB_TO_XTR
from logger import logger


class DonateState(StatesGroup):
    entering_donate_amount = State()
    waiting_for_donate_confirmation = State()
    waiting_for_donate_payment = State()


router = Router()


@router.callback_query(F.data == "donate")
async def process_donate(callback_query: types.CallbackQuery, state: FSMContext):

    try:
        await callback_query.message.delete()
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🤖 Бот для покупки звезд", url="https://t.me/PremiumBot"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="💰 Ввести сумму доната", callback_data="enter_custom_donate_amount"
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="view_profile"))

    await bot.send_message(
        chat_id=callback_query.from_user.id,
        text="🌟 Поддержите наш проект!\n\n"
        "Каждый донат помогает развивать и улучшать сервис. "
        "Мы ценим вашу поддержку и работаем над тем, чтобы сделать наш продукт еще лучше. 💡",
        reply_markup=builder.as_markup(),
    )

    await callback_query.answer()


@router.callback_query(F.data == "enter_custom_donate_amount")
async def process_enter_donate_amount(
    callback_query: types.CallbackQuery, state: FSMContext
):
    await callback_query.message.edit_text(f"💸 Введите сумму доната в рублях:")
    await state.set_state(DonateState.entering_donate_amount)
    await callback_query.answer()


@router.message(DonateState.entering_donate_amount)
async def process_donate_amount_input(message: types.Message, state: FSMContext):

    try:
        await message.delete()
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

    if message.text.isdigit():
        amount = int(message.text)
        if amount // RUB_TO_XTR <= 0:
            await message.answer(
                f"Сумма доната должна быть больше {RUB_TO_XTR}. Пожалуйста, введите сумму еще раз:"
            )
            return

        await state.update_data(amount=amount)
        await state.set_state(DonateState.waiting_for_donate_confirmation)

        try:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="Задонатить", pay=True))
            builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="donate"))

            await message.answer_invoice(
                title=f"Донат проекту {amount} рублей",
                description="Спасибо за вашу поддержку!",
                prices=[LabeledPrice(label="Донат", amount=int(amount // RUB_TO_XTR))],
                provider_token="",
                payload=f"{amount}_donate",
                currency="XTR",
                reply_markup=builder.as_markup(),
            )
            await state.set_state(DonateState.waiting_for_donate_payment)
        except Exception as e:
            logger.error(f"Ошибка при создании доната: {e}")
            await message.answer("Произошла ошибка при создании доната.")
    else:
        await message.answer("Некорректная сумма. Пожалуйста, введите сумму еще раз:")


@router.pre_checkout_query(DonateState.waiting_for_donate_payment)
async def on_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment, DonateState.waiting_for_donate_payment)
async def on_successful_donate(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.from_user.id)
        amount = float(message.successful_payment.invoice_payload.split("_")[0])
        logger.debug(f"Donate succeeded for user_id: {user_id}, amount: {amount}")

        state_data = await state.get_data()
        previous_message_id = state_data.get("last_message_id")

        if previous_message_id:
            try:
                await bot.delete_message(
                    chat_id=user_id, message_id=previous_message_id
                )
            except Exception as e:
                logger.error(f"Не удалось удалить сообщение: {e}")

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text="Вернуться в профиль", callback_data="view_profile"
            )
        )

        sent_message = await bot.send_message(
            chat_id=user_id,
            text=f"🙏 Спасибо за донат {amount} рублей! Ваша поддержка очень важна для нас. 💖",
            reply_markup=builder.as_markup(),
        )

        await state.update_data(last_message_id=sent_message.message_id)
        await state.clear()

    except ValueError as e:
        logger.error(f"Ошибка конвертации user_id или amount: {e}")
    except Exception as e:
        logger.error(f"Произошла ошибка при обработке доната: {e}")
