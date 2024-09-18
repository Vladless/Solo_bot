from aiogram import types, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import aiosqlite
from config import DATABASE_PATH, ADMIN_ID
from database import get_balance, update_balance
from bot import bot

router = Router()

class ReplenishBalanceState(StatesGroup):
    choosing_transfer_method = State()
    waiting_for_admin_confirmation = State()

@router.callback_query(lambda c: c.data == 'replenish_balance')
async def process_callback_replenish_balance(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(ReplenishBalanceState.choosing_transfer_method)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='По реквизитам', callback_data='transfer_method_requisites')]
    ])
    await bot.send_message(callback_query.from_user.id, "Выберите метод перевода:", reply_markup=keyboard)
    await callback_query.answer()  # Уведомляем Telegram, что запрос обработан

@router.callback_query(lambda c: c.data.startswith('transfer_method_'))
async def process_transfer_method_selection(callback_query: types.CallbackQuery, state: FSMContext):
    data = callback_query.data.split('_', 2)
    
    if len(data) != 3:
        await bot.send_message(callback_query.from_user.id, "Неверные данные для выбора метода перевода.")
        return
    
    transfer_method = data[2]  # 'requisites'
    
    if transfer_method == 'requisites':
        message = (
            "Банк: Т-Банк\n"
            "2200701036597224\n"
            "Получатель: Лисицын В.Д.\n"
            f"\n"
            "После перевода отправьте чек и дождитесь подтверждения."
        )
        
        # Удаляем старое сообщение о выборе метода
        await bot.edit_message_text(
            text="Вы выбрали метод перевода. Теперь вы увидите реквизиты.",
            chat_id=callback_query.from_user.id,
            message_id=callback_query.message.message_id
        )

        # Отправляем реквизиты в новом сообщении
        await bot.send_message(callback_query.from_user.id, message)

    else:
        await bot.send_message(callback_query.from_user.id, "Неверный метод перевода.")
        return
    
    await state.update_data(transfer_method=transfer_method)
    await state.set_state(ReplenishBalanceState.waiting_for_admin_confirmation)

    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Подтвердить', callback_data=f'admin_confirm_{callback_query.from_user.id}_{transfer_method}')],
        [InlineKeyboardButton(text='Отклонить', callback_data=f'admin_decline_{callback_query.from_user.id}_{transfer_method}')]
    ])
    
    admin_message = (
        f"Пользователь {callback_query.from_user.full_name} запросил пополнение баланса.\n"
        f"Метод перевода: {transfer_method}\n"
        "Пожалуйста, подтвердите или отклоните запрос."
    )

    await bot.send_message(ADMIN_ID, admin_message, reply_markup=admin_keyboard)
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith('admin_'))
async def process_admin_confirmation(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id != ADMIN_ID:
        await bot.send_message(callback_query.from_user.id, "Вы не являетесь администратором.")
        return

    data = callback_query.data.split('_', 4)
    
    if len(data) < 4:
        await bot.send_message(callback_query.from_user.id, "Неверные данные для обработки запроса.")
        return

    action = data[1]  # 'confirm' или 'decline'
    user_id_str = data[2]
    transfer_method = data[3]

    try:
        user_id = int(user_id_str)  # Преобразование user_id в целое число

        if action == 'confirm':
            amount = 100  # Пример суммы, на которую нужно пополнить баланс

            async with aiosqlite.connect(DATABASE_PATH) as db:
                await update_balance(user_id, amount)
                await db.commit()

            balance = await get_balance(user_id)
            await bot.send_message(callback_query.from_user.id, f"Баланс пользователя успешно пополнен на {amount} RUB.\nТекущий баланс: {balance}")
            await bot.send_message(user_id, f"Ваш баланс был успешно пополнен на {amount} RUB.")

        elif action == 'decline':
            await bot.send_message(callback_query.from_user.id, "Пополнение баланса отклонено.")
            await bot.send_message(user_id, "Ваш запрос на пополнение баланса был отклонен.")

    except Exception as e:
        await bot.send_message(callback_query.from_user.id, f"Ошибка при пополнении баланса: {e}")
        print(f"Ошибка при пополнении баланса: {e}")  # Отладочное сообщение в консоль

    await state.clear()
    await callback_query.answer()
