from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from filters.admin import get_admin_context
from logger import logger
from services.payments.yookassa import refunds
from settings.buttons import BACK, CANCEL

from ..panel.headers import menu_text, quote
from ..panel.keyboard import AdminPanelCallback


YOOKASSA_REFUNDS_TITLE = "Возвраты ЮKassa"

YOOKASSA_REFUNDS_HINT = (
    "Деньги возвращаются на исходное платёжное средство. "
    "С баланса списывается соответствующая часть зачисления, баланс может стать отрицательным. "
    "Подпиской администратор управляет отдельно."
)

YOOKASSA_REFUND_SEARCH_PROMPT = "Пришлите ID платежа ЮKassa, номер платежа, ID клиента или Telegram ID."

YOOKASSA_REFUND_EMPTY = "Подходящих платежей ЮKassa нет."

YOOKASSA_REFUND_LIST_ITEM = "№{id} · клиент {user_id} · {amount_gross} ₽ · {status}"

YOOKASSA_REFUND_PAYMENT = (
    "Платёж №<b>{id}</b>\nЮKassa: <code>{payment_id}</code>\n"
    "Клиент: <code>{user_id}</code> · Telegram: <code>{tg_id}</code>\n"
    "Способ: {provider}\nСтатус: {status}\n\n"
    "Оплачено: <b>{amount_gross} ₽</b>\nЗачислено: {amount_net} ₽\n"
    "Возвращено: {refunded_gross} ₽\nОстаток для возврата: <b>{remaining_gross} ₽</b>"
)

YOOKASSA_REFUND_AMOUNT_PROMPT = (
    "Пришлите сумму частичного возврата в рублях. Минимум — 1 ₽; после возврата должен остаться 0 ₽ или не менее 1 ₽."
)

YOOKASSA_REFUND_EMAIL_PROMPT = "Пришлите e-mail для чека возврата. Он нужен для этого частичного возврата."

YOOKASSA_REFUND_PREVIEW = (
    "Платёж №<b>{payment_id}</b> · клиент <code>{user_id}</code>\n\n"
    "Вернуть клиенту: <b>{amount} ₽</b>\nСписать с баланса: <b>{balance_debit} ₽</b>\n\n"
    "Подписка продолжит действовать. Подтвердите отправку возврата."
)

YOOKASSA_REFUND_OPERATION = (
    "Операция: <code>{operation_id}</code>\nВозврат ЮKassa: <code>{refund_id}</code>\n"
    "Сумма: <b>{amount} ₽</b>\nСтатус: <b>{status}</b>"
)

YOOKASSA_REFUND_HISTORY_TITLE = "История возвратов"

YOOKASSA_REFUND_HISTORY_ITEM = "{created_at} · {amount} ₽ · {status} · {refund_id}"

YOOKASSA_REFUND_HISTORY_EMPTY = "Возвратов по этому платежу ещё нет."

YOOKASSA_REFUND_STATUSES = {
    "draft": "Ожидает подтверждения",
    "prepared": "Подготовлен к отправке",
    "unknown": "Результат уточняется",
    "pending": "Обрабатывается ЮKassa",
    "succeeded": "Возвращён",
    "canceled": "Отменён ЮKassa",
    "failed": "Отклонён",
    "manual_review": "Требуется сверка с ЮKassa",
    "success": "Оплачен",
    "refunded": "Полностью возвращён",
}

YOOKASSA_REFUND_ERROR = "Не удалось обработать возврат. Обновите статус и попробуйте ещё раз."

YOOKASSA_REFUND_SENDING = "Отправляем возврат. Результат появится в истории операций."

YOOKASSA_REFUND_RECEIPT_STATUS = "Регистрация чека: {status}"

YOOKASSA_REFUND_RECEIPT_STATUSES = {
    "succeeded": "Зарегистрирован",
    "pending": "Ожидает регистрации",
    "canceled": "Ошибка регистрации",
}

YOOKASSA_REFUND_SEARCH = "🔍 Найти платёж"
YOOKASSA_REFUND_FULL = "↩️ Вернуть весь остаток"
YOOKASSA_REFUND_PARTIAL = "↩️ Частичный возврат"
YOOKASSA_REFUND_CONFIRM = "✅ Отправить возврат"
YOOKASSA_REFUND_REFRESH = "🔄 Обновить статус"
YOOKASSA_REFUND_HISTORY = "📋 История возвратов"


class RefundCallback(CallbackData, prefix="ykr"):
    action: str
    key: str = ""
    page: int = 0


class RefundState(StatesGroup):
    search = State()
    amount = State()
    email = State()


async def _has_full_admin(event: CallbackQuery | Message) -> bool:
    """Проверяет полные права перед каждым действием."""
    if not event.from_user:
        return False
    _, is_super, _ = await get_admin_context(event.from_user.id, fresh=True)
    return is_super


router = Router(name="admin_settings_yookassa_refunds")
router.callback_query.filter(_has_full_admin)
router.message.filter(_has_full_admin)
_PAGE_SIZE = 5


async def _edit(message: Message, text: str, **kwargs: object) -> None:
    """Обновляет экран, включая неизменившийся статус."""
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def _button(label: str, action: str, key: str | int = "", page: int = 0) -> InlineKeyboardButton:
    """Создаёт короткую кнопку раздела возвратов."""
    return InlineKeyboardButton(text=label, callback_data=RefundCallback(action=action, key=str(key), page=page).pack())


def _status(value: object) -> str:
    """Возвращает подпись состояния операции."""
    return YOOKASSA_REFUND_STATUSES.get(str(value), str(value or "—"))


def _safe_fields(values: dict) -> dict[str, str]:
    """Экранирует значения карточки для Telegram."""
    return {key: escape(str(value if value is not None else "—")) for key, value in values.items()}


def _back_keyboard(payment_id: str | int | None = None) -> InlineKeyboardMarkup:
    """Возвращает кнопку к платежу или списку."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[_button(BACK, "payment" if payment_id else "list", payment_id or "")]]
    )


async def _show_list(
    message: Message, state: FSMContext, *, page: int = 0, search: str | None = None, edit: bool = True
) -> None:
    """Показывает страницу платежей выбранного поиска."""
    data = await state.get_data()
    query = search if search is not None else data.get("refund_search", "")
    await state.set_state(None)
    await state.update_data(refund_search=query)
    page = max(0, page)
    result = await refunds.list_payments(search=query, offset=page * _PAGE_SIZE, limit=_PAGE_SIZE)
    rows = []
    for item in result["items"]:
        label = YOOKASSA_REFUND_LIST_ITEM.format(**{**item, "status": _status(item.get("status"))})
        rows.append([_button(label, "payment", item["id"])])
    navigation = []
    if page:
        navigation.append(_button("‹", "list", page=page - 1))
    if (page + 1) * _PAGE_SIZE < result["total"]:
        navigation.append(_button("›", "list", page=page + 1))
    if navigation:
        rows.append(navigation)
    rows.append([_button(YOOKASSA_REFUND_SEARCH, "search")])
    rows.append([InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_yookassa").pack())])
    body = quote(YOOKASSA_REFUNDS_HINT)
    if not result["items"]:
        body += "\n\n" + YOOKASSA_REFUND_EMPTY
    send = (lambda *args, **kwargs: _edit(message, *args, **kwargs)) if edit else message.answer
    await send(menu_text(YOOKASSA_REFUNDS_TITLE, body), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _show_payment(message: Message, payment_id: int, *, refresh: bool = False) -> None:
    """Показывает остаток платежа и доступные действия."""
    item = await refunds.get_payment(payment_id, refresh=refresh)
    rendered = _safe_fields({**item, "status": _status(item.get("status"))})
    body = YOOKASSA_REFUND_PAYMENT.format(**rendered)
    if item.get("error"):
        body += "\n\n" + escape(str(item["error"]))
    rows = []
    if item.get("refundable"):
        rows.extend([
            [_button(YOOKASSA_REFUND_FULL, "full", payment_id)],
            [_button(YOOKASSA_REFUND_PARTIAL, "partial", payment_id)],
        ])
    rows.extend([
        [_button(YOOKASSA_REFUND_REFRESH, "refresh", payment_id)],
        [_button(YOOKASSA_REFUND_HISTORY, "history", payment_id)],
        [_button(BACK, "list")],
    ])
    await _edit(
        message, menu_text(YOOKASSA_REFUNDS_TITLE, body), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


async def _show_preview(message: Message, state: FSMContext, *, actor_id: int, edit: bool = True) -> None:
    """Сохраняет черновик и показывает точную сумму подтверждения."""
    data = await state.get_data()
    preview = await refunds.preview_refund(
        int(data["refund_payment_id"]),
        amount=data.get("refund_amount"),
        actor_id=actor_id,
        receipt_email=data.get("refund_email", ""),
    )
    await state.set_state(None)
    payment = preview["payment"]
    body = YOOKASSA_REFUND_PREVIEW.format(
        **_safe_fields({
            **preview,
            "payment_id": payment["id"],
            "user_id": payment.get("user_id"),
        })
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(YOOKASSA_REFUND_CONFIRM, "confirm", preview["operation_id"])],
            [_button(CANCEL, "payment", payment["id"])],
        ]
    )
    send = (lambda *args, **kwargs: _edit(message, *args, **kwargs)) if edit else message.answer
    await send(menu_text(YOOKASSA_REFUNDS_TITLE, body), reply_markup=keyboard)


async def _show_operation(message: Message, operation: dict, *, edit: bool = True) -> None:
    """Показывает результат без повторной отправки возврата."""
    values = {
        **operation,
        "refund_id": operation.get("refund_id") or operation.get("provider_refund_id") or "—",
        "status": _status(operation.get("status")),
    }
    body = YOOKASSA_REFUND_OPERATION.format(**_safe_fields(values))
    if operation.get("error"):
        body += "\n\n" + escape(str(operation["error"]))
    if operation.get("receipt_registration"):
        receipt_status = str(operation["receipt_registration"])
        body += "\n" + YOOKASSA_REFUND_RECEIPT_STATUS.format(
            status=escape(YOOKASSA_REFUND_RECEIPT_STATUSES.get(receipt_status, receipt_status))
        )
    payment_id = operation.get("payment_record_id")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(YOOKASSA_REFUND_REFRESH, "operation", operation["operation_id"])],
            [_button(BACK, "payment" if payment_id else "list", payment_id or "")],
        ]
    )
    send = (lambda *args, **kwargs: _edit(message, *args, **kwargs)) if edit else message.answer
    await send(menu_text(YOOKASSA_REFUNDS_TITLE, body), reply_markup=keyboard)


async def _handle_error(event: CallbackQuery | Message, state: FSMContext, exc: Exception) -> None:
    """Показывает безопасную ошибку и запрос контакта для чека."""
    message = event.message if isinstance(event, CallbackQuery) else event
    if getattr(exc, "code", "") == "receipt_email_required":
        await state.set_state(RefundState.email)
        data = await state.get_data()
        await message.answer(YOOKASSA_REFUND_EMAIL_PROMPT, reply_markup=_back_keyboard(data.get("refund_payment_id")))
    else:
        if isinstance(exc, ValueError):
            error = str(exc)
        else:
            logger.error("[YooKassa:Refunds] Ошибка меню: {}", type(exc).__name__)
            error = YOOKASSA_REFUND_ERROR
        if isinstance(event, CallbackQuery):
            await event.answer(error[:200], show_alert=True)
            return
        await message.answer(escape(error))
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_yoo_refunds"))
async def open_refunds(callback: CallbackQuery, state: FSMContext) -> None:
    """Открывает возвраты из настроек кассы."""
    await state.clear()
    try:
        await _show_list(callback.message, state)
    except Exception as exc:
        await _handle_error(callback, state, exc)
        return
    await callback.answer()


@router.callback_query(RefundCallback.filter())
async def refund_action(callback: CallbackQuery, callback_data: RefundCallback, state: FSMContext) -> None:
    """Обрабатывает навигацию и подтверждение возврата."""
    action, key = callback_data.action, callback_data.key
    try:
        if action == "list":
            await _show_list(callback.message, state, page=callback_data.page)
        elif action == "search":
            await state.set_state(RefundState.search)
            await callback.message.edit_text(YOOKASSA_REFUND_SEARCH_PROMPT, reply_markup=_back_keyboard())
        elif action in {"payment", "refresh"}:
            await state.set_state(None)
            await _show_payment(callback.message, int(key), refresh=action == "refresh")
        elif action in {"full", "partial"}:
            await state.update_data(refund_payment_id=int(key), refund_amount=None, refund_email="")
            if action == "full":
                await _show_preview(callback.message, state, actor_id=callback.from_user.id)
            else:
                await state.set_state(RefundState.amount)
                await callback.message.edit_text(YOOKASSA_REFUND_AMOUNT_PROMPT, reply_markup=_back_keyboard(key))
        elif action == "confirm":
            await callback.answer(YOOKASSA_REFUND_SENDING)
            operation = await refunds.execute_refund(key, actor_id=callback.from_user.id)
            await _show_operation(callback.message, operation)
            return
        elif action == "operation":
            operation = await refunds.get_refund(key, refresh=True)
            await _show_operation(callback.message, operation)
        elif action == "history":
            history = await refunds.list_refunds(int(key))
            rows = []
            lines = []
            page = max(0, callback_data.page)
            for item in history[page * _PAGE_SIZE : (page + 1) * _PAGE_SIZE]:
                refund_id = item.get("refund_id") or item.get("provider_refund_id") or "—"
                lines.append(
                    YOOKASSA_REFUND_HISTORY_ITEM.format(
                        **_safe_fields({
                            **item,
                            "created_at": str(item.get("created_at") or "")[:19],
                            "status": _status(item.get("status")),
                            "refund_id": refund_id,
                        })
                    )
                )
                if item.get("operation_id") and item.get("status") != "draft":
                    rows.append([
                        _button(
                            f"{item['amount']} ₽ · {_status(item.get('status'))}", "operation", item["operation_id"]
                        )
                    ])
            navigation = []
            if page:
                navigation.append(_button("‹", "history", key, page - 1))
            if (page + 1) * _PAGE_SIZE < len(history):
                navigation.append(_button("›", "history", key, page + 1))
            if navigation:
                rows.append(navigation)
            rows.append([_button(BACK, "payment", key)])
            await _edit(
                callback.message,
                menu_text(YOOKASSA_REFUND_HISTORY_TITLE, "\n\n".join(lines) or YOOKASSA_REFUND_HISTORY_EMPTY),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
        else:
            raise ValueError("Некорректное действие.")
    except Exception as exc:
        await _handle_error(callback, state, exc)
        return
    await callback.answer()


@router.message(RefundState.search)
async def search_payment(message: Message, state: FSMContext) -> None:
    """Ищет платёж по идентификаторам клиента и кассы."""
    try:
        await _show_list(message, state, search=(message.text or "").strip()[:128], edit=False)
    except Exception as exc:
        await _handle_error(message, state, exc)


@router.message(RefundState.amount)
async def enter_amount(message: Message, state: FSMContext) -> None:
    """Проверяет сумму и формирует подтверждение."""
    await state.update_data(refund_amount=(message.text or "").strip().replace(",", "."))
    try:
        await _show_preview(message, state, actor_id=message.from_user.id, edit=False)
    except Exception as exc:
        await _handle_error(message, state, exc)


@router.message(RefundState.email)
async def enter_receipt_email(message: Message, state: FSMContext) -> None:
    """Добавляет контакт покупателя к чеку возврата."""
    await state.update_data(refund_email=(message.text or "").strip()[:254])
    try:
        await _show_preview(message, state, actor_id=message.from_user.id, edit=False)
    except Exception as exc:
        await _handle_error(message, state, exc)
