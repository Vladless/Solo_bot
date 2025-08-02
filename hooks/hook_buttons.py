from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def insert_hook_buttons(builder: InlineKeyboardBuilder, buttons: list) -> InlineKeyboardBuilder:
    """
    Вставляет кнопки из хуков в существующий builder (вставка после указанной кнопки через `after`)
    """
    markup = builder.as_markup()
    new_rows = markup.inline_keyboard.copy()

    for module in buttons:
        if isinstance(module, dict) and "after" in module and "button" in module:
            after = module["after"]
            button = module["button"]

            insert_pos = -1
            for i, row in enumerate(new_rows):
                if any(btn.callback_data == after for btn in row):
                    insert_pos = i + 1
                    break

            if 0 <= insert_pos <= len(new_rows):
                new_rows.insert(insert_pos, [button])
            else:
                new_rows.append([button])
        else:
            button = module.get("button") if isinstance(module, dict) else module
            new_rows.append([button])

    return InlineKeyboardBuilder.from_markup(InlineKeyboardMarkup(inline_keyboard=new_rows))
