from html import escape as html_escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from filters.admin import IsAdminFilter
from logger import logger
from services.subscription_keys import (
    HOSTS_PER_PAGE,
    fetch_user_links,
    host_label,
    resolve_remnawave_server_ref,
)
from services.users_utils import resolve_admin_key
from settings.buttons import BACK

from .keyboard import AdminUserEditorCallback


router = Router()


TITLE_HOSTS = "<b>🔑 Ключи подписки</b>"

TEXT_HOSTS_INFO = (
    "<blockquote>"
    "📧 <b>Username:</b> <code>{username}</code>\n"
    "🖥️ <b>Всего ключей:</b> {total}\n"
    "📄 <b>Страница:</b> {page}/{total_pages}"
    "</blockquote>\n\n"
    "Выберите ключ, чтобы получить ссылку для подключения."
)

TEXT_KEY_FOR_HOST = "🌐 <b>Ключ:</b> {label}\n<blockquote><code>{link}</code></blockquote>"

ERR_KEY_NOT_FOUND = "🚫 Подписка не найдена."
ERR_NO_USERNAME = "🚫 У подписки не указан Email."
ERR_API_FAIL = "⚠️ Не удалось получить данные с панели Remnawave. Попробуйте позже."
ERR_NO_HOSTS = "ℹ️ У клиента нет доступных ключей."
ERR_HOST_NOT_FOUND = "🚫 Ключ не найден (возможно, список изменился). Откройте список заново."
ERR_NOT_REMNAWAVE = "ℹ️ Список ключей доступен только для Remnawave-подписок."
ERR_BAD_REQUEST = "🚫 Некорректный запрос."


def _back_to_key_edit_kb(tg_id: int, key_ref: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminUserEditorCallback(action="users_key_edit", tg_id=tg_id, data=key_ref).pack(),
        )
    )
    return builder.as_markup()


def _back_to_list_kb(tg_id: int, key_ref: str, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminUserEditorCallback(
                action="users_keys_list", tg_id=tg_id, data=f"{key_ref}|{page}"
            ).pack(),
        )
    )
    return builder.as_markup()


def _build_hosts_kb(tg_id: int, key_ref: str, links: list[str], page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total = len(links)
    total_pages = max(1, (total + HOSTS_PER_PAGE - 1) // HOSTS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * HOSTS_PER_PAGE
    end = start + HOSTS_PER_PAGE

    row_buttons: list[InlineKeyboardButton] = []
    for offset, link in enumerate(links[start:end]):
        idx = start + offset
        label = host_label(link, idx)
        row_buttons.append(
            InlineKeyboardButton(
                text=label,
                callback_data=AdminUserEditorCallback(
                    action="users_keys_show", tg_id=tg_id, data=f"{key_ref}|{page}|{idx}"
                ).pack(),
            )
        )
        if len(row_buttons) == 2:
            builder.row(*row_buttons)
            row_buttons = []
    if row_buttons:
        builder.row(*row_buttons)

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=AdminUserEditorCallback(
                        action="users_keys_list", tg_id=tg_id, data=f"{key_ref}|{page - 1}"
                    ).pack(),
                )
            )
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=AdminUserEditorCallback(
                        action="users_keys_list", tg_id=tg_id, data=f"{key_ref}|{page + 1}"
                    ).pack(),
                )
            )
        builder.row(*nav)

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminUserEditorCallback(action="users_key_edit", tg_id=tg_id, data=key_ref).pack(),
        )
    )
    return builder.as_markup()


async def _safe_edit(callback_query: CallbackQuery, text: str, kb: InlineKeyboardMarkup) -> None:
    try:
        await callback_query.message.edit_text(text=text, reply_markup=kb, disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logger.warning(f"[subscription_keys] edit_text не удался: {e}")


async def _safe_answer(callback_query: CallbackQuery) -> None:
    try:
        await callback_query.answer()
    except TelegramBadRequest:
        pass


def _parse_data(raw: str | int | None) -> tuple[str, int, int]:
    parts = str(raw or "").split("|")
    key_ref = parts[0]
    try:
        page = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        page = 0
    try:
        idx = int(parts[2]) if len(parts) > 2 else -1
    except ValueError:
        idx = -1
    return key_ref, page, idx


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_keys_list"),
    IsAdminFilter(),
)
async def handle_hosts_list(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    key_ref, page, _ = _parse_data(callback_data.data)

    key_obj = await resolve_admin_key(session, tg_id, key_ref)
    if not key_obj:
        logger.warning(f"[subscription_keys] resolve_admin_key вернул None: tg_id={tg_id}, key_ref='{key_ref}'")
        await _safe_edit(callback_query, ERR_KEY_NOT_FOUND, _back_to_key_edit_kb(tg_id, key_ref))
        return

    if not key_obj.email:
        await _safe_edit(callback_query, ERR_NO_USERNAME, _back_to_key_edit_kb(tg_id, key_ref))
        return

    server_ref = await resolve_remnawave_server_ref(session, key_obj.server_id or "")
    if not server_ref:
        await _safe_edit(callback_query, ERR_NOT_REMNAWAVE, _back_to_key_edit_kb(tg_id, key_ref))
        return

    await _safe_answer(callback_query)

    links = await fetch_user_links(session, key_obj.server_id or "", key_obj.email)
    if links is None:
        await _safe_edit(callback_query, ERR_API_FAIL, _back_to_key_edit_kb(tg_id, key_ref))
        return

    if not links:
        await _safe_edit(callback_query, ERR_NO_HOSTS, _back_to_key_edit_kb(tg_id, key_ref))
        return

    total = len(links)
    total_pages = max(1, (total + HOSTS_PER_PAGE - 1) // HOSTS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    text = f"{TITLE_HOSTS}\n" + TEXT_HOSTS_INFO.format(
        username=html_escape(key_obj.email),
        total=total,
        page=page + 1,
        total_pages=total_pages,
    )

    await _safe_edit(callback_query, text, _build_hosts_kb(tg_id, key_ref, links, page))


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_keys_show"),
    IsAdminFilter(),
)
async def handle_host_show(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    key_ref, page, idx = _parse_data(callback_data.data)

    if idx < 0:
        await _safe_edit(callback_query, ERR_BAD_REQUEST, _back_to_list_kb(tg_id, key_ref, page))
        return

    key_obj = await resolve_admin_key(session, tg_id, key_ref)
    if not key_obj or not key_obj.email:
        await _safe_edit(callback_query, ERR_KEY_NOT_FOUND, _back_to_list_kb(tg_id, key_ref, page))
        return

    await _safe_answer(callback_query)

    links = await fetch_user_links(session, key_obj.server_id or "", key_obj.email)
    if links is None:
        await _safe_edit(callback_query, ERR_API_FAIL, _back_to_list_kb(tg_id, key_ref, page))
        return

    if idx >= len(links):
        await _safe_edit(callback_query, ERR_HOST_NOT_FOUND, _back_to_list_kb(tg_id, key_ref, page))
        return

    link = links[idx]
    label = host_label(link, idx)

    text = TEXT_KEY_FOR_HOST.format(
        label=html_escape(label),
        link=html_escape(link),
    )

    await _safe_edit(callback_query, text, _back_to_list_kb(tg_id, key_ref, page))
