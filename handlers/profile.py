from aiogram import Bot, Dispatcher, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime
import aiosqlite
from config import DATABASE_PATH, ADMIN_ID
from database import get_balance, update_balance, has_active_key
from bot import bot

router = Router()

class ReplenishBalanceState(StatesGroup):
    choosing_transfer_method = State()
    waiting_for_admin_confirmation = State()

@router.callback_query(lambda c: c.data == 'view_profile')
async def process_callback_view_profile(callback_query: types.CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id
    username = callback_query.from_user.username

    try:
        # Получаем активный ключ
        email = await has_active_key(tg_id)
        if email:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                async with db.execute(
                    "SELECT expiry_time FROM connections WHERE tg_id = ? AND expiry_time > ?",
                    (tg_id, int(datetime.utcnow().timestamp() * 1000))
                ) as cursor:
                    record = await cursor.fetchone()
                    expiry_date = "Неизвестно" if record is None else datetime.utcfromtimestamp(record[0] / 1000).strftime("%Y-%m-%d %H:%M:%S")
            
            balance = await get_balance(tg_id)
            profile_message = (
                f"Профиль клиента:\n"
                f"Никнейм: @{username}\n"
                f"Email: {email}\n"
                f"Дата окончания ключа: {expiry_date}\n"
                f"Баланс: {balance}\n"
            )
            
            button_view_keys = InlineKeyboardButton(text='Мои ключи', callback_data='view_keys')
            button_replenish_balance = InlineKeyboardButton(text='Пополнить баланс', callback_data='replenish_balance')
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[button_view_keys], [button_replenish_balance]])
            
            profile_message += "Вы можете просмотреть ваши ключи или пополнить баланс ниже:"
        else:
            profile_message = "У вас нет активных ключей."
            keyboard = None
    
    except Exception as e:
        profile_message = f"Ошибка при получении данных профиля: {e}"
        keyboard = None
    
    # Отправляем сообщение с клавиатурой внизу
    await callback_query.message.reply(profile_message, reply_markup=keyboard)
    await callback_query.answer()


@router.callback_query(lambda c: c.data == 'replenish_balance')
async def process_callback_replenish_balance(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(ReplenishBalanceState.choosing_transfer_method)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='По реквизитам', callback_data='transfer_method_requisites')],
        [InlineKeyboardButton(text='По СБП', callback_data='transfer_method_sbp')]
    ])
    await callback_query.message.answer("Выберите метод перевода:", reply_markup=keyboard)
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith('transfer_method_'))
async def process_transfer_method_selection(callback_query: types.CallbackQuery, state: FSMContext):
    data = callback_query.data.split('_', 2)
    
    if len(data) != 3:
        await callback_query.answer("Неверные данные для выбора метода перевода.")
        return
    
    transfer_method = data[2]  # 'requisites' или 'sbp'
    amount = 100  # Можно заменить на 300, если нужно
    user_id = callback_query.from_user.id
    
    # Сохраняем данные в состояние
    await state.update_data(transfer_method=transfer_method, amount=amount, user_id=user_id)
    
    # Выводим состояние для отладки
    state_data = await state.get_data()
    print("Сохраненные данные состояния:", state_data)

    # Отправляем запрос на подтверждение администратору
    admin_message = (
        f"Запрос на пополнение баланса:\n"
        f"Метод перевода: {transfer_method.replace('requisites', 'По реквизитам').replace('sbp', 'По СБП')}\n"
        f"Сумма: {amount} единиц\n"
        f"Пользователь: {user_id}\n\n"
        f"Подтвердите или отклоните пополнение."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Подтвердить', callback_data=f'admin_confirm_replenish_{user_id}_{amount}_{transfer_method}')],
        [InlineKeyboardButton(text='Отклонить', callback_data=f'admin_decline_replenish_{user_id}')]
    ])
    
    # Отправляем сообщение администратору
    await callback_query.message.answer("Запрос на пополнение отправлен администратору.")
    await bot.send_message(ADMIN_ID, admin_message, reply_markup=keyboard)
    
    await state.set_state(ReplenishBalanceState.waiting_for_admin_confirmation)
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith('admin_'))
async def process_admin_confirmation(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Вы не являетесь администратором.")
        return

    data = callback_query.data.split('_', 4)
    
    if len(data) < 4:
        await callback_query.answer("Неверные данные для обработки запроса.")
        return

    action = data[1]  # 'confirm' или 'decline'
    user_id_str = data[3]

    try:
        user_id = int(user_id_str)  # Преобразование user_id в целое число

        if action == 'confirm':
            
            amount_str = data[4].split('_')[0]

            # Убедимся, что amount_str действительно является числом
            if amount_str.isdigit():
                amount = int(amount_str)  # Преобразование amount в целое число
            else:
                await callback_query.answer("Неверная сумма для пополнения. Проверьте формат данных.")
                print(f"Ошибка преобразования суммы: amount_str='{amount_str}'")  # Отладочное сообщение в консоль
                return

            async with aiosqlite.connect(DATABASE_PATH) as db:
                # Обновляем баланс пользователя
                await update_balance(user_id, amount)
                await db.commit()

            balance = await get_balance(user_id)
            await callback_query.message.answer(f"Баланс пользователя успешно пополнен на {amount} единиц.\nТекущий баланс: {balance}")

            # Сообщаем пользователю о пополнении
            await bot.send_message(user_id, f"Ваш баланс был успешно пополнен на {amount} RUB.")

        elif action == 'decline':
            await callback_query.message.answer("Пополнение баланса отклонено.")
            
            # Сообщаем пользователю об отклонении
            await bot.send_message(user_id, "Ваш запрос на пополнение баланса был отклонен.")

    except Exception as e:
        # Отладочное сообщение
        await callback_query.message.answer(f"Ошибка при пополнении баланса: {e}")
        print(f"Ошибка при пополнении баланса: {e}")  # Отладочное сообщение в консоль

    # Очистка состояния
    await state.clear()
    await callback_query.answer()
