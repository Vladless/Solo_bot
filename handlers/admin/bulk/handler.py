from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_clusters, get_tariff_group_codes, get_tariffs
from filters.admin import HasPermission
from filters.permissions import PERM_BULK

from ..panel.headers import menu_text, quote
from ..panel.keyboard import AdminPanelCallback
from .keyboard import (
    BulkCallback,
    build_actions_kb,
    build_clusters_kb,
    build_confirm_kb,
    build_created_dir_kb,
    build_done_kb,
    build_expiry_kind_kb,
    build_filters_kb,
    build_tariff_groups_kb,
    build_tariffs_kb,
)
from .operations import (
    bulk_add_days,
    bulk_add_gb,
    bulk_delete,
    bulk_freeze,
    bulk_reissue,
    bulk_reissue_link,
    bulk_unfreeze,
)
from .query import fetch_matching_keys
from .states import BulkStates


router = Router()

ACTION_LABELS = {
    "days": "добавить дни",
    "gb": "добавить трафик (ГБ)",
    "freeze": "заморозить",
    "unfreeze": "разморозить",
    "reissue": "перевыпустить",
    "reissue_link": "перевыпустить со сменой ссылки",
    "delete": "удалить",
}


async def _respond(target: Message, text: str, kb, edit: bool) -> None:
    if edit:
        try:
            await target.edit_text(text, reply_markup=kb)
            return
        except TelegramBadRequest:
            return
    await target.answer(text, reply_markup=kb)


def _describe(data: dict) -> str:
    action = data.get("action")
    label = ACTION_LABELS.get(action, action)
    if action == "days":
        label = f"добавить {data.get('action_value')} дн."
    elif action == "gb":
        label = f"добавить {data.get('action_value')} ГБ"

    ftype = data.get("filter_type")
    if ftype == "tariff":
        flt = f"тариф #{data.get('tariff_id')}"
    elif ftype == "cluster":
        flt = f"кластер {data.get('cluster_name')}"
    elif ftype == "created":
        direction = "старше" if data.get("created_dir") == "older" else "моложе"
        flt = f"созданы {direction} {data.get('created_days')} дн."
    elif ftype == "expiry":
        kind = data.get("expiry_kind")
        flt = {
            "expired": "уже истекли",
            "active": "ещё активны",
            "soon": f"истекают в течение {data.get('expiry_days')} дн.",
        }.get(kind, kind)
    else:
        flt = "—"

    return f"Действие: {label}\nФильтр: {flt}"


async def _after_filter_set(target: Message, edit: bool, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    action = data.get("action")
    if action == "days":
        await state.set_state(BulkStates.action_days)
        await _respond(target, menu_text("Дни", "Сколько дней добавить?"), None, edit)
    elif action == "gb":
        await state.set_state(BulkStates.action_gb)
        await _respond(target, menu_text("Трафик", "Сколько ГБ добавить?"), None, edit)
    else:
        await _show_preview(target, edit, state, session)


async def _show_preview(target: Message, edit: bool, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    keys = await fetch_matching_keys(session, data)
    if not keys:
        await _respond(
            target,
            menu_text("Массовое действие", "По фильтру ничего не найдено.", quote(_describe(data))),
            build_done_kb(),
            edit,
        )
        return

    action = data.get("action")
    warning = ""
    if action == "reissue":
        warning = (
            "⚠️ Подписки пересоздадутся на серверах, ссылка у клиентов останется прежней. "
            "Операция тяжёлая и необратимая."
        )
    elif action == "reissue_link":
        warning = (
            "⚠️ Remnawave-ключи получат новую ссылку — старая перестанет работать, клиент "
            "будет уведомлён. Ключи 3x-ui просто пересоздадутся с той же ссылкой. "
            "Операция необратимая."
        )
    await _respond(
        target,
        menu_text(
            "Массовое действие",
            "Подтвердить выполнение?",
            quote(_describe(data)),
            quote(f"Найдено подписок: <b>{len(keys)}</b>"),
            quote(warning) if warning else "",
        ),
        build_confirm_kb(),
        edit,
    )


def _bulk_menu_text() -> str:
    return menu_text(
        "Массовые действия",
        "Операция сразу над группой подписок.",
        quote("Сначала действие, затем признак отбора — бот покажет, сколько подписок попадёт под него."),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "bulk"), HasPermission(PERM_BULK))
async def bulk_entry(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _respond(callback.message, _bulk_menu_text(), build_actions_kb(), True)


@router.callback_query(BulkCallback.filter(F.step == "back_actions"), HasPermission(PERM_BULK))
async def back_actions(callback: CallbackQuery, state: FSMContext):
    await state.set_state(None)
    await _respond(callback.message, _bulk_menu_text(), build_actions_kb(), True)


@router.callback_query(BulkCallback.filter(F.step == "action"), HasPermission(PERM_BULK))
async def choose_action(callback: CallbackQuery, callback_data: BulkCallback, state: FSMContext):
    await state.update_data(action=callback_data.value)
    await _respond(callback.message, menu_text("Отбор", "По какому признаку берём подписки?"), build_filters_kb(), True)


@router.callback_query(BulkCallback.filter(F.step == "back_filters"), HasPermission(PERM_BULK))
async def back_filters(callback: CallbackQuery, state: FSMContext):
    await state.set_state(None)
    await _respond(callback.message, menu_text("Отбор", "По какому признаку берём подписки?"), build_filters_kb(), True)


@router.callback_query(BulkCallback.filter(F.step == "filter"), HasPermission(PERM_BULK))
async def choose_filter(callback: CallbackQuery, callback_data: BulkCallback, state: FSMContext, session: AsyncSession):
    ftype = callback_data.value
    await state.update_data(filter_type=ftype)
    if ftype == "tariff":
        groups = await get_tariff_group_codes(session)
        await _respond(
            callback.message, menu_text("Группа тарифов", "Выберите группу."), build_tariff_groups_kb(groups), True
        )
    elif ftype == "cluster":
        clusters = await get_clusters(session)
        await _respond(callback.message, menu_text("Кластер", "Выберите кластер."), build_clusters_kb(clusters), True)
    elif ftype == "created":
        await _respond(
            callback.message, menu_text("Дата создания", "Старше или моложе срока?"), build_created_dir_kb(), True
        )
    elif ftype == "expiry":
        await _respond(
            callback.message, menu_text("Срок истечения", "Какие подписки берём?"), build_expiry_kind_kb(), True
        )


@router.callback_query(BulkCallback.filter(F.step == "back_tgroups"), HasPermission(PERM_BULK))
async def back_tgroups(callback: CallbackQuery, session: AsyncSession):
    groups = await get_tariff_group_codes(session)
    await _respond(
        callback.message, menu_text("Группа тарифов", "Выберите группу."), build_tariff_groups_kb(groups), True
    )


@router.callback_query(BulkCallback.filter(F.step == "tgroup"), HasPermission(PERM_BULK))
async def choose_tariff_group(callback: CallbackQuery, callback_data: BulkCallback, session: AsyncSession):
    tariffs = await get_tariffs(session, group_code=callback_data.value)
    if not tariffs:
        await _respond(
            callback.message,
            menu_text("Группа тарифов", "В этой группе нет тарифов."),
            build_tariff_groups_kb(await get_tariff_group_codes(session)),
            True,
        )
        return
    await _respond(
        callback.message,
        menu_text("Массовые действия", f"Группа «{callback_data.value}». Выберите тариф:"),
        build_tariffs_kb(tariffs),
        True,
    )


@router.callback_query(BulkCallback.filter(F.step == "tariff"), HasPermission(PERM_BULK))
async def choose_tariff(callback: CallbackQuery, callback_data: BulkCallback, state: FSMContext, session: AsyncSession):
    await state.update_data(tariff_id=callback_data.value)
    await _after_filter_set(callback.message, True, state, session)


@router.callback_query(BulkCallback.filter(F.step == "cluster"), HasPermission(PERM_BULK))
async def choose_cluster(
    callback: CallbackQuery, callback_data: BulkCallback, state: FSMContext, session: AsyncSession
):
    await state.update_data(cluster_name=callback_data.value)
    await _after_filter_set(callback.message, True, state, session)


@router.callback_query(BulkCallback.filter(F.step == "created"), HasPermission(PERM_BULK))
async def choose_created(callback: CallbackQuery, callback_data: BulkCallback, state: FSMContext):
    await state.update_data(created_dir=callback_data.value)
    await state.set_state(BulkStates.created_days)
    await _respond(callback.message, menu_text("Массовые действия", "Введите число дней:"), None, True)


@router.message(BulkStates.created_days, HasPermission(PERM_BULK))
async def input_created_days(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text or not message.text.strip().isdigit():
        await message.answer(menu_text("Массовые действия", "❌ Нужно число."))
        return
    await state.update_data(created_days=int(message.text.strip()))
    await state.set_state(None)
    await _after_filter_set(message, False, state, session)


@router.callback_query(BulkCallback.filter(F.step == "expiry"), HasPermission(PERM_BULK))
async def choose_expiry(callback: CallbackQuery, callback_data: BulkCallback, state: FSMContext, session: AsyncSession):
    kind = callback_data.value
    await state.update_data(expiry_kind=kind)
    if kind == "soon":
        await state.set_state(BulkStates.expiry_days)
        await _respond(callback.message, menu_text("Массовые действия", "Введите число дней:"), None, True)
    else:
        await _after_filter_set(callback.message, True, state, session)


@router.message(BulkStates.expiry_days, HasPermission(PERM_BULK))
async def input_expiry_days(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text or not message.text.strip().isdigit():
        await message.answer(menu_text("Массовые действия", "❌ Нужно число."))
        return
    await state.update_data(expiry_days=int(message.text.strip()))
    await state.set_state(None)
    await _after_filter_set(message, False, state, session)


@router.message(BulkStates.action_days, HasPermission(PERM_BULK))
async def input_action_days(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text or not message.text.strip().isdigit() or int(message.text.strip()) <= 0:
        await message.answer(menu_text("Массовые действия", "❌ Нужно число дней больше нуля."))
        return
    await state.update_data(action_value=int(message.text.strip()))
    await state.set_state(None)
    await _show_preview(message, False, state, session)


@router.message(BulkStates.action_gb, HasPermission(PERM_BULK))
async def input_action_gb(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text or not message.text.strip().isdigit() or int(message.text.strip()) <= 0:
        await message.answer(menu_text("Массовые действия", "❌ Нужно число ГБ больше нуля."))
        return
    await state.update_data(action_value=int(message.text.strip()))
    await state.set_state(None)
    await _show_preview(message, False, state, session)


@router.callback_query(BulkCallback.filter(F.step == "confirm"), HasPermission(PERM_BULK))
async def do_confirm(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    action = data.get("action")
    keys = await fetch_matching_keys(session, data)
    if not keys:
        await state.clear()
        await _respond(
            callback.message, menu_text("Массовые действия", "🚫 По фильтру ничего не найдено."), build_done_kb(), True
        )
        return

    await _respond(
        callback.message, menu_text("Массовые действия", f"⏳ Выполняю для {len(keys)} подписок…"), None, True
    )

    notified = 0
    if action == "days":
        ok, fail, skipped = await bulk_add_days(session, keys, int(data["action_value"]))
    elif action == "gb":
        ok, fail, skipped = await bulk_add_gb(session, keys, int(data["action_value"]))
    elif action == "delete":
        ok, fail, skipped = await bulk_delete(session, keys)
    elif action == "freeze":
        ok, fail, skipped = await bulk_freeze(session, keys)
    elif action == "unfreeze":
        ok, fail, skipped = await bulk_unfreeze(session, keys)
    elif action == "reissue":
        ok, fail, skipped = await bulk_reissue(session, keys)
    elif action == "reissue_link":
        ok, fail, skipped, notified = await bulk_reissue_link(session, keys, callback.bot)
    else:
        ok, fail, skipped = 0, 0, 0

    await state.clear()
    extra: list[str] = []
    summary = menu_text("Массовые действия", "✅ Готово.", quote(_describe(data)), quote(f"Обработано: <b>{ok}</b>"))
    if skipped:
        note = (
            " (безлимитные)"
            if action == "gb"
            else " (без Telegram ID)"
            if action in ("reissue", "reissue_link")
            else ""
        )
        extra.append(f"Пропущено: <b>{skipped}</b>{note}")
    if action == "reissue_link" and notified:
        extra.append(f"Уведомлено клиентов: <b>{notified}</b>")
    if fail:
        extra.append(f"Ошибок: <b>{fail}</b>")
    if extra:
        summary += quote("\n".join(extra))
    await callback.message.answer(summary, reply_markup=build_done_kb())
