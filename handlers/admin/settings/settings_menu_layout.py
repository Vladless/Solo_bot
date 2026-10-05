from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MENU_LAYOUT, update_menu_layout
from core.defaults import DEFAULT_MENU_LAYOUT
from filters.admin import IsAdminFilter
from handlers.menu_layout import BUTTON_TITLES, MENU_TITLES, menu_rows
from settings.buttons import BACK

from ..panel.headers import menu_text, quote
from ..panel.keyboard import AdminPanelCallback


router = Router(name="admin_settings_menu_layout")
router.callback_query.filter(IsAdminFilter())

MAX_ROW_BUTTONS = 3


class MenuLayoutCallback(CallbackData, prefix="menu_layout"):
    menu: str
    action: str
    index: int = 0


def _flat(menu: str) -> list[tuple[str, bool]]:
    """Раскладка списком: кнопка и признак «стоит в одном ряду с предыдущей»."""
    flat: list[tuple[str, bool]] = []
    for row in menu_rows(menu):
        for position, button in enumerate(row):
            flat.append((button, position > 0))
    return flat


def _to_rows(flat: list[tuple[str, bool]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for button, joined in flat:
        if joined and rows and len(rows[-1]) < MAX_ROW_BUTTONS:
            rows[-1].append(button)
        else:
            rows.append([button])
    return rows


def _detach_follower(flat: list[tuple[str, bool]], index: int) -> None:
    """Кнопка уходит из ряда — стоявшая за ней не должна прилипнуть к новому соседу."""
    follower = index + 1
    if follower < len(flat) and flat[follower][1]:
        flat[follower] = (flat[follower][0], False)


def build_menu_layout_kb(menu: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    titles = BUTTON_TITLES.get(menu, {})

    for index, (button, joined) in enumerate(_flat(menu)):
        title = titles.get(button, button)
        builder.row(
            InlineKeyboardButton(
                text="⬆️",
                callback_data=MenuLayoutCallback(menu=menu, action="up", index=index).pack(),
            ),
            InlineKeyboardButton(
                text=f"{'↳ ' if joined else ''}{title}",
                callback_data=MenuLayoutCallback(menu=menu, action="join", index=index).pack(),
            ),
            InlineKeyboardButton(
                text="⬇️",
                callback_data=MenuLayoutCallback(menu=menu, action="down", index=index).pack(),
            ),
        )

    builder.row(
        InlineKeyboardButton(
            text="🔄 Вернуть по умолчанию",
            callback_data=MenuLayoutCallback(menu=menu, action="reset").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_buttons").pack())
    )
    return builder.as_markup()


def build_menu_picker_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for menu, title in MENU_TITLES.items():
        builder.row(
            InlineKeyboardButton(
                text=title,
                callback_data=MenuLayoutCallback(menu=menu, action="open").pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_buttons").pack())
    )
    return builder.as_markup()


def _layout_text(menu: str) -> str:
    titles = BUTTON_TITLES.get(menu, {})
    rows = [" + ".join(titles.get(button, button) for button in row) for row in menu_rows(menu)]
    return menu_text(
        MENU_TITLES.get(menu, menu),
        quote("\n".join(f"{index}. {row}" for index, row in enumerate(rows, 1)), wide=True),
        quote(
            "⬆️ и ⬇️ двигают кнопку по списку.\n"
            "Нажатие на название ставит кнопку в один ряд с предыдущей или возвращает в свой ряд.\n"
            "Выключенные кнопки и те, что не подходят клиенту по условиям, просто не показываются.",
            wide=True,
        ),
        wide=True,
    )


async def _save(session: AsyncSession, menu: str, flat: list[tuple[str, bool]]) -> None:
    layout = {name: [row.copy() for row in rows] for name, rows in MENU_LAYOUT.items()}
    layout[menu] = _to_rows(flat)
    await update_menu_layout(session, layout)


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_menu_layout"))
async def open_menu_picker(callback: CallbackQuery, session: AsyncSession) -> None:
    text = menu_text(
        "Порядок кнопок",
        "Выберите меню, в котором хотите переставить кнопки.",
    )
    await callback.message.edit_text(text=text, reply_markup=build_menu_picker_kb())
    await callback.answer()


@router.callback_query(MenuLayoutCallback.filter(F.action == "open"))
async def open_menu_layout(callback: CallbackQuery, callback_data: MenuLayoutCallback, session: AsyncSession) -> None:
    menu = callback_data.menu
    if menu not in MENU_TITLES:
        await callback.answer("Неизвестное меню", show_alert=True)
        return
    await callback.message.edit_text(text=_layout_text(menu), reply_markup=build_menu_layout_kb(menu))
    await callback.answer()


@router.callback_query(MenuLayoutCallback.filter(F.action.in_({"up", "down", "join", "reset"})), flags={"popup": True})
async def edit_menu_layout(callback: CallbackQuery, callback_data: MenuLayoutCallback, session: AsyncSession) -> None:
    menu = callback_data.menu
    if menu not in MENU_TITLES:
        await callback.answer("Неизвестное меню", show_alert=True)
        return

    if callback_data.action == "reset":
        layout = {name: [row.copy() for row in rows] for name, rows in MENU_LAYOUT.items()}
        layout[menu] = [row.copy() for row in DEFAULT_MENU_LAYOUT.get(menu, [])]
        await update_menu_layout(session, layout)
        notice = "Порядок возвращён к исходному"
    else:
        flat = _flat(menu)
        index = callback_data.index
        if not 0 <= index < len(flat):
            await callback.answer("Кнопка не найдена", show_alert=True)
            return

        if callback_data.action == "join":
            button, joined = flat[index]
            if index == 0:
                await callback.answer("Первая кнопка не может стоять в ряду с предыдущей")
                return
            flat[index] = (button, not joined)
            notice = "Кнопка в своём ряду" if joined else "Кнопка встала в один ряд"
        elif callback_data.action == "up":
            if index == 0:
                await callback.answer("Уже первая")
                return
            _detach_follower(flat, index)
            flat[index - 1], flat[index] = flat[index], flat[index - 1]
            notice = "Перемещено выше"
        else:
            if index >= len(flat) - 1:
                await callback.answer("Уже последняя")
                return
            _detach_follower(flat, index)
            flat[index], flat[index + 1] = flat[index + 1], flat[index]
            notice = "Перемещено ниже"

        if flat and flat[0][1]:
            flat[0] = (flat[0][0], False)

        await _save(session, menu, flat)

    await callback.message.edit_text(text=_layout_text(menu), reply_markup=build_menu_layout_kb(menu))
    await callback.answer(notice)
