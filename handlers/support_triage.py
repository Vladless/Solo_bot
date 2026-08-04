from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.utils import edit_or_send_message
from settings.buttons import BACK, MAIN_MENU, MY_SUBS, NOT_HELPED, SUPPORT
from settings.config import SUPPORT_CHAT_URL
from settings.texts import TRIAGE_FAIL_TEXT, TRIAGE_ITEMS, TRIAGE_ROOT_TEXT


router = Router(name="support_triage")


class TriageCallback(CallbackData, prefix="triage"):
    action: str
    node: str = ""


def build_triage_root_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in TRIAGE_ITEMS:
        builder.row(
            InlineKeyboardButton(
                text=item["label"],
                callback_data=TriageCallback(action="cat", node=item["id"]).pack(),
            )
        )
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
    return builder.as_markup()


def _build_category_kb(node_id: str, show_subs: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if show_subs:
        builder.row(InlineKeyboardButton(text=MY_SUBS, callback_data="view_keys"))
    builder.row(InlineKeyboardButton(text=NOT_HELPED, callback_data=TriageCallback(action="fail", node=node_id).pack()))
    builder.row(InlineKeyboardButton(text=BACK, callback_data=TriageCallback(action="root").pack()))
    return builder.as_markup()


def _build_support_kb(url: str = SUPPORT_CHAT_URL) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    cleaned_url = (url or "").strip()
    if cleaned_url.startswith(("http://", "https://", "tg://")):
        builder.row(InlineKeyboardButton(text=SUPPORT, url=cleaned_url))
    builder.row(InlineKeyboardButton(text=BACK, callback_data=TriageCallback(action="root").pack()))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
    return builder.as_markup()


@router.callback_query(TriageCallback.filter(F.action == "root"))
async def triage_root(callback: CallbackQuery) -> None:
    await edit_or_send_message(
        target_message=callback.message,
        text=TRIAGE_ROOT_TEXT,
        reply_markup=build_triage_root_kb(),
    )
    await callback.answer()


@router.callback_query(TriageCallback.filter(F.action == "cat"), flags={"popup": True})
async def triage_category(callback: CallbackQuery, callback_data: TriageCallback) -> None:
    item = next((i for i in TRIAGE_ITEMS if i["id"] == callback_data.node), None)
    if item is None:
        await callback.answer("Раздел не найден", show_alert=True)
        return
    await edit_or_send_message(
        target_message=callback.message,
        text=item["text"],
        reply_markup=_build_category_kb(item["id"], bool(item.get("show_subs", False))),
    )
    await callback.answer()


@router.callback_query(TriageCallback.filter(F.action == "fail"))
async def triage_fail(callback: CallbackQuery, callback_data: TriageCallback) -> None:
    try:
        await callback.answer()
    except Exception:
        pass
    kb = None
    from core.settings.modes_config import MODES_CONFIG

    if MODES_CONFIG.get("SUPPORT_TICKETS_ENABLED"):
        from support_bot import support_deeplink

        try:
            url = await support_deeplink(callback_data.node or "")
        except Exception:
            url = None
        if url:
            kb = _build_support_kb(url)
    await edit_or_send_message(
        target_message=callback.message,
        text=TRIAGE_FAIL_TEXT,
        reply_markup=kb or _build_support_kb(),
    )
