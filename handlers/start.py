from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import ADMIN_ID, CHANNEL_URL
from bot import bot  # Убедитесь, что bot и dp импортируются из правильного места

router = Router()

class FeedbackState(StatesGroup):
    waiting_for_feedback = State()

async def start_command(message: types.Message):
    welcome_text = (
        "*Добро пожаловать в наш сервис!*\n\n"
        "Вы можете воспользоваться следующими функциями:\n\n"
        "🔑 *Создать ключ для подключения VPN* - Получите уникальный ключ для доступа к VPN.\n"
        "📅 *Узнать дату окончания ключа* - Проверьте, когда истекает срок действия вашего ключа.\n"
        "👤 *Просмотреть ваш профиль* - Получите информацию о вашем аккаунте и балансе.\n\n"
        "Нажмите на соответствующую кнопку ниже, чтобы начать!"
    )

    button_view_profile = InlineKeyboardButton(text='👤 Мой профиль', callback_data='view_profile')
    button_about_vpn = InlineKeyboardButton(text='🔒 О VPN', callback_data='about_vpn')
    button_feedback = InlineKeyboardButton(text='📝 Обратная связь', callback_data='feedback')
    button_channel = InlineKeyboardButton(text='📢 Наш канал', url=CHANNEL_URL)

    inline_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [button_view_profile],
        [button_about_vpn],
        [button_feedback],
        [button_channel]
    ])

    reply_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Меню")],
        #    [KeyboardButton(text="Мой профиль")]
        ],
        resize_keyboard=True
    )

    await message.bot.send_message(
        chat_id=message.chat.id,
        text=welcome_text,
        parse_mode='Markdown',
        reply_markup=inline_keyboard
    )
    
    await message.bot.send_message(
        chat_id=message.chat.id,
        text="Выберите действие:",
        reply_markup=reply_keyboard
    )

@router.callback_query(lambda c: c.data == 'about_vpn')
async def handle_about_vpn(callback_query: CallbackQuery):
    info_message = (
        "*О VPN*\n\n"
        "Мы используем VLESS для обеспечения безопасного и надежного подключения к интернету. "
        "Каждому пользователю предоставляется индивидуальный ключ для подключения. "
        "Этот ключ необходим для использования нашего VPN-сервиса."
    )
    
    # Кнопка "Назад"
    button_back = InlineKeyboardButton(text='⬅️ Назад', callback_data='back_to_menu')

    # Обновлённая клавиатура с кнопкой "Назад"
    inline_keyboard_back = InlineKeyboardMarkup(inline_keyboard=[
        [button_back]
    ])

    await callback_query.message.edit_text(
        info_message,
        parse_mode='Markdown',
        reply_markup=inline_keyboard_back  # Добавление клавиатуры с кнопкой "Назад"
    )
    await callback_query.answer()

@router.callback_query(lambda c: c.data == 'back_to_menu')
async def handle_back_to_menu(callback_query: CallbackQuery):
    welcome_text = (
        "*Добро пожаловать в наш сервис!*\n\n"
        "Вы можете воспользоваться следующими функциями:\n\n"
        "🔑 *Создать ключ для подключения VPN* - Получите уникальный ключ для доступа к VPN.\n"
        "📅 *Узнать дату окончания ключа* - Проверьте, когда истекает срок действия вашего ключа.\n"
        "👤 *Просмотреть ваш профиль* - Получите информацию о вашем аккаунте и балансе.\n\n"
        "Нажмите на соответствующую кнопку ниже, чтобы начать!"
    )

    button_view_profile = InlineKeyboardButton(text='👤 Мой профиль', callback_data='view_profile')
    button_about_vpn = InlineKeyboardButton(text='🔒 О VPN', callback_data='about_vpn')
    button_feedback = InlineKeyboardButton(text='📝 Обратная связь', callback_data='feedback')
    button_channel = InlineKeyboardButton(text='📢 Наш канал', url=CHANNEL_URL)

    inline_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [button_view_profile],
        [button_about_vpn],
        [button_feedback],
        [button_channel]
    ])

    # Редактирование сообщения на главное меню
    await callback_query.message.edit_text(
        welcome_text,
        parse_mode='Markdown',
        reply_markup=inline_keyboard
    )
    await callback_query.answer()

@router.callback_query(lambda c: c.data == 'feedback')
async def handle_feedback(callback_query: CallbackQuery, state: FSMContext):
    feedback_text = "Напишите нам, если у вас возникли трудности с подключением, есть отзыв или предложение. @pocomacho"

    # Кнопка "Назад"
    button_back = InlineKeyboardButton(text='⬅️ Назад', callback_data='back_to_menu')

    # Обновлённая клавиатура с кнопкой "Назад"
    inline_keyboard_back = InlineKeyboardMarkup(inline_keyboard=[
        [button_back]
    ])

    # Редактирование сообщения с кнопкой "Назад"
    await callback_query.message.edit_text(
        feedback_text,
        parse_mode='Markdown',
        reply_markup=inline_keyboard_back  # Клавиатура с кнопкой "Назад"
    )

    await state.set_state(FeedbackState.waiting_for_feedback)
    await state.update_data(user_id=callback_query.from_user.id)
    await callback_query.answer()

@router.message(FeedbackState.waiting_for_feedback)
async def receive_feedback(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    user_id = user_data.get('user_id')

    feedback_message = (
        f"Обратная связь от пользователя {user_id}:\n\n"
        f"{message.text}"
    )
    
    try:
        await bot.send_message(ADMIN_ID, feedback_message)
        await message.answer("Спасибо за ваше сообщение! Мы свяжемся с вами, если это будет необходимо.")
    except Exception as e:
        await message.answer("Произошла ошибка при отправке вашего сообщения.")
        print(f"Ошибка при отправке обратной связи: {e}")  # Логирование ошибок
    
    await state.clear()

# Пример обработки команды старт
@router.message(Command('start'))
async def cmd_start(message: Message):
    await start_command(message)