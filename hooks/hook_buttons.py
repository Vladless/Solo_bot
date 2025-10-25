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
    - {"remove_url": str | list[str]} — удалить кнопки с указанным URL
    - {"remove_url_prefix": str} — удалить кнопки, у которых URL начинается с префикса
    - {"replace_keyboard": InlineKeyboardBuilder} — полностью заменить клавиатуру
    """
    markup = builder.as_markup()
    new_rows = markup.inline_keyboard.copy()

    buttons = buttons or []
    flat_buttons = []
    for item in buttons:
        if isinstance(item, list | tuple):
            flat_buttons.extend(item)
        else:
            flat_buttons.append(item)

    replace_operations = [b for b in flat_buttons if isinstance(b, dict) and "replace_keyboard" in b]
    if replace_operations:
        replace_data = replace_operations[0]["replace_keyboard"]
        if isinstance(replace_data, InlineKeyboardBuilder):
            return replace_data
        else:
            return builder

    remove_operations = [
        b
        for b in flat_buttons
        if isinstance(b, dict)
        and ("remove" in b or "remove_prefix" in b or "remove_url" in b or "remove_url_prefix" in b)
    ]
    for module in remove_operations:
        removes = module.get("remove")
        if isinstance(removes, str):
            removes = [removes]
        removes = set(removes or [])
        prefix = module.get("remove_prefix")

        remove_urls = module.get("remove_url")
        if isinstance(remove_urls, str):
            remove_urls = [remove_urls]
        remove_urls = set(remove_urls or [])
        url_prefix = module.get("remove_url_prefix")

        filtered_rows = []
        for row in new_rows:
            filtered_row = []
            for btn in row:
                cdata = getattr(btn, "callback_data", None)
                url = getattr(btn, "url", None)
                webapp_url = (
                    getattr(getattr(btn, "web_app", None), "url", None) if getattr(btn, "web_app", None) else None
                )

                should_remove_callback = cdata and (cdata in removes or (prefix and cdata.startswith(prefix)))

                should_remove_url = (
                    url and (url in remove_urls or (url_prefix is not None and url.startswith(url_prefix)))
                ) or (
                    webapp_url
                    and (webapp_url in remove_urls or (url_prefix is not None and webapp_url.startswith(url_prefix)))
                )

                if should_remove_callback or should_remove_url:
                    continue
                filtered_row.append(btn)
            if filtered_row:
                filtered_rows.append(filtered_row)
        new_rows = filtered_rows

    insert_operations = [b for b in flat_buttons if isinstance(b, dict) and "insert_at" in b and "button" in b]
    for module in insert_operations:
        insert_at = module["insert_at"]
        button = module["button"]

        if 0 <= insert_at <= len(new_rows):
            new_rows.insert(insert_at, [button])
        else:
            new_rows.append([button])

    after_operations = [b for b in flat_buttons if isinstance(b, dict) and "after" in b and "button" in b]
    for module in after_operations:
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

    for module in flat_buttons:
        if isinstance(module, dict) and "button" in module and "insert_at" not in module and "after" not in module:
            button = module["button"]
            new_rows.append([button])
        elif module and not isinstance(module, dict):
            new_rows.append([module])

    return InlineKeyboardBuilder.from_markup(InlineKeyboardMarkup(inline_keyboard=new_rows))
