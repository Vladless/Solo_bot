from aiogram.types import InlineKeyboardButton

from core.settings.menu_layout_config import MENU_LAYOUT


PROFILE_MENU = "profile"
KEY_MENU = "key"

MENU_TITLES: dict[str, str] = {
    PROFILE_MENU: "Личный кабинет",
    KEY_MENU: "Меню подписки",
}

BUTTON_TITLES: dict[str, dict[str, str]] = {
    PROFILE_MENU: {
        "bind_email": "Почта",
        "web_cabinet": "Веб-кабинет",
        "subscription": "Подписка",
        "balance": "Баланс",
        "gifts": "Подарки",
        "invite": "Пригласить",
        "instructions": "Инструкции",
        "modules": "Модули",
        "admin": "Админка",
        "back": "Назад",
    },
    KEY_MENU: {
        "connect": "Подключить",
        "tv": "Телевизор",
        "renew": "Продлить",
        "addons": "Опции",
        "devices": "Устройства",
        "qr": "QR-код",
        "delete": "Удалить",
        "location": "Локация",
        "main_menu": "Главное меню",
        "modules": "Модули",
    },
}


def split_hook_buttons(items) -> tuple[list[InlineKeyboardButton], list]:
    """Делит ответы хуков: простые кнопки идут в позицию раскладки, остальное — прежним механизмом.

    Модуль, указавший «after», «insert_at» или удаление, сам знает, куда встать, — его не трогаем.
    """
    plain: list[InlineKeyboardButton] = []
    directives: list = []

    for item in items or []:
        if isinstance(item, list | tuple):
            nested_plain, nested_directives = split_hook_buttons(item)
            plain.extend(nested_plain)
            directives.extend(nested_directives)
            continue
        if isinstance(item, InlineKeyboardButton):
            plain.append(item)
            continue
        if isinstance(item, dict) and set(item) == {"button"} and isinstance(item["button"], InlineKeyboardButton):
            plain.append(item["button"])
            continue
        if item:
            directives.append(item)

    return plain, directives


def menu_button_ids(menu: str) -> list[str]:
    """Возвращает все известные кнопки меню в порядке реестра."""
    return list(BUTTON_TITLES.get(menu, {}))


def menu_rows(menu: str) -> list[list[str]]:
    """Возвращает сохранённую раскладку меню, дописывая кнопки, которых в ней ещё нет."""
    rows = [row.copy() for row in MENU_LAYOUT.get(menu, [])]
    placed = {button for row in rows for button in row}
    rows.extend([button] for button in menu_button_ids(menu) if button not in placed)
    return rows


def arrange_menu(menu: str, buttons: dict) -> list[list[InlineKeyboardButton]]:
    """Раскладывает готовые кнопки по рядам из настроек; отсутствующие пропускает.

    Значением может быть готовый блок рядов — он встаёт на место своей позиции целиком.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for row in menu_rows(menu):
        line: list[InlineKeyboardButton] = []
        for button_id in row:
            value = buttons.get(button_id)
            if value is None:
                continue
            if isinstance(value, list):
                if line:
                    rows.append(line)
                    line = []
                rows.extend(block for block in value if block)
                continue
            line.append(value)
        if line:
            rows.append(line)
    return rows
