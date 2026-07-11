from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import SUPPORT_CHAT_URL
from handlers.buttons import BACK, MAIN_MENU, MY_SUBS, NOT_HELPED, SUPPORT
from handlers.texts import TRIAGE_FAIL_TEXT, TRIAGE_ITEMS, TRIAGE_ROOT_TEXT


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


def _build_support_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL))
    builder.row(InlineKeyboardButton(text=BACK, callback_data=TriageCallback(action="root").pack()))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
    return builder.as_markup()


@router.callback_query(TriageCallback.filter(F.action == "root"))
async def triage_root(callback: CallbackQuery) -> None:
    await callback.message.edit_text(TRIAGE_ROOT_TEXT, reply_markup=build_triage_root_kb())
    await callback.answer()


@router.callback_query(TriageCallback.filter(F.action == "cat"), flags={"popup": True})
async def triage_category(callback: CallbackQuery, callback_data: TriageCallback) -> None:
    item = next((i for i in TRIAGE_ITEMS if i["id"] == callback_data.node), None)
    if item is None:
        await callback.answer("Раздел не найден", show_alert=True)
        return
    await callback.message.edit_text(
        item["text"],
        reply_markup=_build_category_kb(item["id"], bool(item.get("show_subs", False))),
    )
    await callback.answer()


@router.callback_query(TriageCallback.filter(F.action == "fail"))
async def triage_fail(callback: CallbackQuery) -> None:
    await callback.message.edit_text(TRIAGE_FAIL_TEXT, reply_markup=_build_support_kb())
    await callback.answer()
