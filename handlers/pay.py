import asyncpg
from aiogram import Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import bot
from config import ADMIN_ID, DATABASE_URL
from database import get_balance, get_key_count, update_balance
from handlers.profile import process_callback_view_profile

router = Router()

class ReplenishBalanceState(StatesGroup):
    choosing_transfer_method = State()
    choosing_amount = State()
    waiting_for_admin_confirmation = State()

async def send_message_with_deletion(chat_id, text, reply_markup=None, state=None, message_key='last_message_id'):
    """
    Отправляет новое сообщение и удаляет предыдущее сообщение (если оно существует), сохраненное в состоянии FSM.
    """
    if state:
        try:
            state_data = await state.get_data()
            previous_message_id = state_data.get(message_key)

            if previous_message_id:
                await bot.delete_message(chat_id=chat_id, message_id=previous_message_id)
    
            sent_message = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
            if state:
                await state.update_data({message_key: sent_message.message_id})
    
        except Exception as e:
            print(f"Ошибка при удалении/отправке сообщения: {e}")
            return None

    return sent_message

@router.callback_query(lambda c: c.data == 'replenish_balance')
async def process_callback_replenish_balance(callback_query: types.CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id

    key_count = await get_key_count(tg_id)
    if key_count <= 0:
        # Создаем кнопки "Создать ключ" и "Назад"
        create_key_button = InlineKeyboardButton(text='Создать ключ', callback_data='create_key')
        back_button = InlineKeyboardButton(text='⬅️ Назад', callback_data='back_to_profile')
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[create_key_button, back_button]])

        await callback_query.message.edit_text(
            text="У вас нет ключей. Пополнение баланса возможно только при наличии ключа.\n"
                 "Пожалуйста, создайте ключ для продолжения.",
            reply_markup=keyboard
        )
        return

    await state.set_state(ReplenishBalanceState.choosing_transfer_method)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='По реквизитам', callback_data='transfer_method_requisites')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='back_to_profile')]  # Кнопка "Назад"
    ])
    
    await callback_query.message.edit_text(
        text="Выберите метод перевода:",
        reply_markup=keyboard
    )
    await callback_query.answer()


@router.callback_query(lambda c: c.data == 'back_to_profile')
async def back_to_profile_handler(callback_query: types.CallbackQuery, state: FSMContext):
    await process_callback_view_profile(callback_query, state)



@router.callback_query(lambda c: c.data.startswith('transfer_method_'))
async def process_transfer_method_selection(callback_query: types.CallbackQuery, state: FSMContext):
    data = callback_query.data.split('_', 2)
    
    if len(data) != 3:
        await send_message_with_deletion(callback_query.from_user.id, "Неверные данные для выбора метода перевода.", state=state, message_key='transfer_method_error_message_id')
        return
    
    transfer_method = data[2]
    
    if transfer_method == 'requisites':
        amount_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='100 RUB', callback_data='amount_100')],
            [InlineKeyboardButton(text='300 RUB', callback_data='amount_300')],
            [InlineKeyboardButton(text='500 RUB', callback_data='amount_500')],
            [InlineKeyboardButton(text='⬅️ Назад', callback_data='back_to_profile')]  # Кнопка "Назад"
        ])
        
        await callback_query.message.edit_text(
            text="Выберите сумму пополнения:",
            reply_markup=amount_keyboard
        )
        await state.update_data(transfer_method=transfer_method)
        await state.set_state(ReplenishBalanceState.choosing_amount)
    
    else:
        await send_message_with_deletion(callback_query.from_user.id, "Неверный метод перевода.", state=state, message_key='transfer_method_error_message_id')
        return


@router.callback_query(lambda c: c.data.startswith('amount_'))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    data = callback_query.data.split('_', 1)

    if len(data) != 2:
        await send_message_with_deletion(callback_query.from_user.id, "Неверные данные для выбора суммы.", state=state, message_key='amount_error_message_id')
        return

    amount_str = data[1]
    amount = int(amount_str)

    # Получаем ID сообщения с кнопками выбора суммы
    state_data = await state.get_data()
    amount_selection_message_id = state_data.get('amount_selection_message_id')

    # Удаляем сообщение с кнопками выбора суммы
    if amount_selection_message_id:
        try:
            await bot.delete_message(chat_id=callback_query.from_user.id, message_id=amount_selection_message_id)
        except Exception as e:
            print(f"Ошибка при удалении сообщения: {e}")

    # Обновляем состояние и сохраняем выбранную сумму
    await state.update_data(amount=amount)
    await state.set_state(ReplenishBalanceState.waiting_for_admin_confirmation)

    transfer_method = state_data.get('transfer_method')
    message = (
        "Банк: Т-Банк\n"
        "2200701036597224\n"
        "Получатель: Лисицын В.Д.\n"
        "\n"
        "После перевода отправьте чек и дождитесь подтверждения."
    )

    # Редактируем текущее сообщение
    await callback_query.message.edit_text(
        text=message,
        reply_markup=None  # Или добавьте клавиатуру, если нужно
    )

    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Подтвердить', callback_data=f'admin_confirm_{callback_query.from_user.id}_{transfer_method}_{amount}')],
        [InlineKeyboardButton(text='Отклонить', callback_data=f'admin_decline_{callback_query.from_user.id}_{transfer_method}_{amount}')]
    ])

    admin_message = (
        f"Пользователь {callback_query.from_user.full_name} запросил пополнение баланса.\n"
        f"Метод перевода: {transfer_method}\n"
        f"Сумма пополнения: {amount} RUB\n"
        "Пожалуйста, подтвердите или отклоните запрос."
    )
    
    await send_message_with_deletion(ADMIN_ID, admin_message, reply_markup=admin_keyboard, state=state, message_key='admin_request_message_id')
    await callback_query.answer()



@router.callback_query(lambda c: c.data.startswith('admin_'))
async def process_admin_confirmation(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id != ADMIN_ID:
        await send_message_with_deletion(callback_query.from_user.id, "Вы не являетесь администратором.", state=state, message_key='admin_error_message_id')
        return

    data = callback_query.data.split('_', 4)
    
    if len(data) < 4:
        await send_message_with_deletion(callback_query.from_user.id, "Неверные данные для обработки запроса.", state=state, message_key='admin_error_message_id')
        return

    action = data[1]
    user_id_str = data[2]
    transfer_method = data[3]
    amount = int(data[4])

    try:
        user_id = int(user_id_str)

        # Получаем данные из состояния для удаления сообщения с реквизитами
        state_data = await state.get_data()
        requisites_message_id = state_data.get('requisites_message_id')

        if action == 'confirm':
            # Подключаемся к базе данных PostgreSQL
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                # Обновляем баланс пользователя
                await update_balance(user_id, amount)

                # Получаем обновленный баланс
                balance = await get_balance(user_id)

                # Создаем клавиатуру с кнопкой "Профиль"
                profile_button = InlineKeyboardButton(text='Профиль', callback_data='view_profile')
                profile_keyboard = InlineKeyboardMarkup(inline_keyboard=[[profile_button]])

                # Отправляем уведомление с кнопкой "Профиль" администратору
                await send_message_with_deletion(callback_query.from_user.id, f"Баланс пользователя успешно пополнен на {amount} RUB.\nТекущий баланс: {balance}", state=state, message_key='admin_confirm_message_id')

                # Отправляем уведомление пользователю с кнопкой "Профиль"
                await bot.send_message(
                    user_id, 
                    f"Ваш баланс был успешно пополнен на {amount} RUB.", 
                    reply_markup=profile_keyboard
                )

                # Удаляем сообщение с реквизитами
                requisites_message_id = state_data.get('requisites_message_id')
                if requisites_message_id:
                    try:
                        await bot.delete_message(chat_id=user_id, message_id=requisites_message_id)
                    except Exception as e:
                        print(f"Ошибка при удалении сообщения: {e}")

            finally:
                await conn.close()

        elif action == 'decline':
            await send_message_with_deletion(callback_query.from_user.id, "Пополнение баланса отклонено.", state=state, message_key='admin_decline_message_id')
            await bot.send_message(user_id, "Ваш запрос на пополнение баланса был отклонен.")

    except Exception as e:
        await send_message_with_deletion(callback_query.from_user.id, f"Ошибка при пополнении баланса: {e}", state=state, message_key='admin_error_message_id')
        print(f"Ошибка при пополнении баланса: {e}")

    finally:
        await state.clear()
        await callback_query.answer()
