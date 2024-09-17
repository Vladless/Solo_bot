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
    
    if transfer_method == 'requisites':
        message = (
            "Реквизиты для пополнения:\n"
            f'\n'
            "Банк: Пример Банк\n"
            "Расчетный счет: 12345678901234567890\n"
            "Получатель: ИП Иванов Иван Иванович"
            f'\n'
            "после перевода отправьте чек и ждите подтверждения"
        )
    elif transfer_method == 'sbp':
        message = (
            "СБП для пополнения:\n"
            f'\n'
            "Номер банка: 1234567890\n"
            "ФИО: Иванов Иван Иванович\n"
            f'\n'
            "после перевода отправьте чек и ждите подтверждения"
        )
    else:
        await callback_query.answer("Неверный метод перевода.")
        return
    
    await callback_query.message.answer(message)
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
