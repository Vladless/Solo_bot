from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


async def send_instructions(callback_query: types.CallbackQuery):
    instructions_message = (
        "*📋 Инструкции по использованию вашего ключа:*\n\n"
        "1. **Скачайте приложение для вашего устройства:**\n"
        "   - **Для Android:** [V2Ray](https://play.google.com/store/apps/details?id=com.v2ray.ang&hl=ru&pli=1)\n"
        "   - **Для iPhone:** [Streisand](https://apps.apple.com/ru/app/streisand/id6450534064)\n"
        "   - **Для Windows:** [Hiddify Next](https://github.com/hiddify/hiddify-next/releases/latest/download/Hiddify-Windows-Setup-x64.Msix)\n\n"
        "2. **Скопируйте предоставленный ключ**, который вы получили ранее.\n"
        "3. **Откройте приложение и нажмите на плюсик сверху справа.**\n"
        "4. **Выберите 'Вставить из буфера обмена' для добавления ключа.**\n\n"
        "💬 Если у вас возникнут вопросы, не стесняйтесь обращаться в [поддержку](https://t.me/solonet_sup)."
    )
    back_button = InlineKeyboardButton(text='🔙 Назад', callback_data='back_to_main')
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button]])

    await callback_query.message.edit_text(instructions_message, parse_mode='Markdown', reply_markup=keyboard)
    await callback_query.answer()