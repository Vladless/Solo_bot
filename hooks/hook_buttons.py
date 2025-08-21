from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def insert_hook_buttons(builder: InlineKeyboardBuilder, buttons: list) -> InlineKeyboardBuilder:
    """
    Вставляет кнопки из хуков в существующий builder.

    Поддерживает:
    - {"button": InlineKeyboardButton} — добавить в конец
    - {"after": callback_data, "button": InlineKeyboardButton} — вставить после заданной кнопки
    - {"insert_at": int, "button": InlineKeyboardButton} — вставить по индексу (0 = начало)
    - {"remove": str | list[str]} — удалить кнопки с указанным callback_data
    - {"remove_prefix": str} — удалить кнопки, у которых callback_data начинается с префикса
    """
    markup = builder.as_markup()
    new_rows = markup.inline_keyboard.copy()

    buttons = buttons or []
    flat_buttons = []
    for item in buttons:
        if isinstance(item, (list, tuple)):
            flat_buttons.extend(item)
        else:
            flat_buttons.append(item)

    for module in flat_buttons:
        if isinstance(module, dict) and ("remove" in module or "remove_prefix" in module):
            removes = module.get("remove")
            if isinstance(removes, str):
                removes = [removes]
            removes = set(removes or [])
            prefix = module.get("remove_prefix")

            filtered_rows = []
            for row in new_rows:
                filtered_row = []
                for btn in row:
                    cdata = getattr(btn, "callback_data", None)
                    if cdata and (cdata in removes or (prefix and cdata.startswith(prefix))):
                        continue
                    filtered_row.append(btn)
                if filtered_row:
                    filtered_rows.append(filtered_row)
            new_rows = filtered_rows

    for module in flat_buttons:
        if isinstance(module, dict) and "after" in module and "button" in module:
            after = module["after"]
            button = module["button"]

            insert_pos = -1
            for i, row in enumerate(new_rows):
                if any(getattr(btn, "callback_data", None) == after for btn in row):
                    insert_pos = i + 1
                    break

            if 0 <= insert_pos <= len(new_rows):
                new_rows.insert(insert_pos, [button])
            else:
                new_rows.append([button])
        elif isinstance(module, dict) and "insert_at" in module and "button" in module:
            insert_at = module["insert_at"]
            button = module["button"]
            
            if 0 <= insert_at <= len(new_rows):
                new_rows.insert(insert_at, [button])
            else:
                new_rows.append([button])
        else:
            if isinstance(module, dict) and "button" in module:
                button = module["button"]
                new_rows.append([button])
            elif module and not isinstance(module, dict):
                new_rows.append([module])

    return InlineKeyboardBuilder.from_markup(InlineKeyboardMarkup(inline_keyboard=new_rows))
