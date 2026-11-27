import asyncio

from datetime import UTC, datetime
from html import escape
from pathlib import Path
from secrets import token_hex
from weakref import WeakValueDictionary
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database import news as news_db
from database.news_targets import list_news_cabinet_targets
from filters.admin import get_admin_context
from utils.web_media import MAX_UPLOAD_BYTES, WEB_UPLOAD_DIR, host_telegram_photo

from .panel.keyboard import AdminPanelCallback, build_admin_back_btn


router = Router(name="admin_news")
PAGE_SIZE = 8
_PREVIEW_LOCKS: WeakValueDictionary[tuple[asyncio.AbstractEventLoop, int, object], asyncio.Lock] = WeakValueDictionary()


class NewsCallback(CallbackData, prefix="admin_news"):
    action: str
    news_id: int = 0
    page: int = 1


class AdminNewsState(StatesGroup):
    title = State()
    content = State()
    image = State()
    preview = State()
    saving = State()
    target_menu = State()
    target_url = State()
    target_tab = State()
    target_label = State()


def _button(builder: InlineKeyboardBuilder, text: str, action: str, news_id: int = 0, page: int = 1) -> None:
    """Добавляет кнопку новостного раздела."""
    builder.button(text=text, callback_data=NewsCallback(action=action, news_id=news_id, page=page).pack())


def _cancel_keyboard(page: int) -> InlineKeyboardMarkup:
    """Собирает отмену ввода новости."""
    builder = InlineKeyboardBuilder()
    _button(builder, "Отмена", "list", page=page)
    return builder.as_markup()


async def _allow(event: Message | CallbackQuery, state: FSMContext) -> bool:
    """Проверяет текущие полные права перед каждым шагом."""
    sender = event.from_user
    allowed = False
    if sender is not None:
        is_admin, is_super, _ = await get_admin_context(sender.id, fresh=True)
        allowed = is_admin and is_super
    if allowed:
        return True
    await state.clear()
    if isinstance(event, CallbackQuery):
        await event.answer("Недостаточно прав для управления новостями.", show_alert=True)
    else:
        await event.answer("Недостаточно прав для управления новостями.")
    return False


async def _render(message: Message, text: str, markup: InlineKeyboardMarkup, *, edit: bool = True) -> None:
    """Показывает обычное HTML-сообщение раздела."""
    if edit and message.text:
        try:
            await message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise
    else:
        await message.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def _news_text(title: str, content: str) -> str:
    """Экранирует текст новости для предпросмотра."""
    return f"<b>{escape(title, quote=False)}</b>\n\n{escape(content, quote=False)}"


def _news_date(value: datetime) -> datetime:
    """Восстанавливает часовой пояс сохранённой даты."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _news_status(item) -> tuple[str, str]:
    """Отличает видимую новость от будущей и завершённой."""
    if not item.is_active:
        return "⚪", "Скрыто"
    now = datetime.now(UTC)
    if item.ends_at is not None and _news_date(item.ends_at) <= now:
        return "⏹", "Период завершён"
    if item.starts_at is not None and _news_date(item.starts_at) > now:
        return "🕒", "Запланировано"
    return "🟢", "Опубликовано"


def _news_period_date(value: datetime) -> str:
    """Показывает время публикации по Москве."""
    return _news_date(value).astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M МСК")


async def _show_list(
    message: Message, state: FSMContext, session: AsyncSession, page: int = 1, *, edit: bool = True
) -> None:
    """Показывает новости со страницами и статусами."""
    await state.clear()
    total = await news_db.count_news(session)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, pages))
    items = await news_db.list_news(session, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    builder = InlineKeyboardBuilder()
    _button(builder, "✏️ Написать новость", "create", page=page)
    for item in items:
        status, _ = _news_status(item)
        _button(builder, f"{status} {item.title[:48]}", "view", item.id, page)
    builder.adjust(1)
    navigation = InlineKeyboardBuilder()
    if page > 1:
        _button(navigation, "←", "list", page=page - 1)
    if page < pages:
        _button(navigation, "→", "list", page=page + 1)
    navigation_buttons = list(navigation.buttons)
    if navigation_buttons:
        builder.row(*navigation_buttons)
    builder.row(build_admin_back_btn())
    text = (
        "📰 <b>Новости</b>\n\n"
        "Короткие объявления для новостного баннера сайта и мобильной версии.\n"
        "🟢 Опубликовано · 🕒 Запланировано · ⏹ Период завершён · ⚪ Скрыто\n\n"
    )
    text += f"Страница {page} из {pages}. Всего: {total}." if items else "Пока нет новостей."
    await _render(message, text, builder.as_markup(), edit=edit)


async def _show_item(
    message: Message, state: FSMContext, item, page: int, *, clear_state: bool = True, edit: bool = True
) -> None:
    """Показывает новость и доступные действия."""
    if clear_state:
        await state.clear()
    builder = InlineKeyboardBuilder()
    _button(builder, "✏️ Редактировать", "edit", item.id, page)
    _button(builder, "🖼 Изображение", "image", item.id, page)
    _button(builder, "↗ Куда ведёт новость", "target", item.id, page)
    _button(
        builder,
        "Отключить показ" if item.is_active else "Включить показ",
        "hide" if item.is_active else "publish",
        item.id,
        page,
    )
    _button(builder, "🗑 Удалить", "delete", item.id, page)
    _button(builder, "← К новостям", "list", page=page)
    builder.adjust(1)
    _, status = _news_status(item)
    text = f"📰 <b>Новость · {status}</b>\n\n{_news_text(item.title, item.content)}"
    if item.image_url:
        text += "\n\n🖼 Фон под текстом" if item.image_mode == "background" else "\n\n🖼 Изображение сбоку"
    if item.starts_at is not None or item.ends_at is not None:
        start = _news_period_date(item.starts_at) if item.starts_at is not None else "Сразу"
        end = _news_period_date(item.ends_at) if item.ends_at is not None else "Бессрочно"
        text += f"\n\n🗓 <b>Период показа</b>\nНачало: {start}\nОкончание: {end}"
    if item.link_url:
        text += f"\n\n↗ {escape(item.link_text or 'Подробнее', quote=False)}: <code>{escape(item.link_url, quote=False)}</code>"
    await _render(message, text, builder.as_markup(), edit=edit)


async def _show_target_menu(message: Message, state: FSMContext, item, page: int) -> None:
    """Предлагает действие по нажатию на опубликованную новость."""
    await state.clear()
    await state.set_state(AdminNewsState.target_menu)
    await state.update_data(
        draft_id=token_hex(8),
        news_id=item.id,
        page=page,
        target_message_id=message.message_id,
        target_default_label=item.link_text or "Подробнее",
    )
    builder = InlineKeyboardBuilder()
    for label, action in (
        ("Показать новость", "target_detail"),
        ("Открыть ссылку", "target_link"),
        ("Открыть вкладку кабинета", "target_tabs"),
    ):
        _button(builder, label, action, item.id, page)
    _button(builder, "Отмена", "view", item.id, page)
    builder.adjust(1)
    await _render(
        message, "↗ <b>Действие новости</b>\n\nВыберите, что открыть по нажатию на новость.", builder.as_markup()
    )


async def _ask_target_label(message: Message, state: FSMContext, draft: dict) -> None:
    """Запрашивает подпись перехода, сохраняя актуальность редактора."""
    builder = InlineKeyboardBuilder()
    _button(builder, "Оставить подпись", "target_default", int(draft["news_id"]), int(draft["page"]))
    _button(builder, "Отмена", "view", int(draft["news_id"]), int(draft["page"]))
    builder.adjust(1)
    label = escape(str(draft.get("target_default_label") or "Подробнее"), quote=False)
    prompt = await message.answer(
        f"Пришлите текст кнопки до 80 символов или оставьте подпись: <b>{label}</b>.",
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.HTML,
    )
    await _change_draft(
        state,
        AdminNewsState.target_label.state,
        {"draft_id": draft["draft_id"], "news_id": draft["news_id"]},
        {"target_message_id": prompt.message_id},
        AdminNewsState.target_label.state,
    )


async def _save_target(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    draft: dict,
    label: str | None,
    *,
    expected_state: str,
    edit: bool = True,
) -> bool:
    """Сохраняет только действие актуального редактора новости."""
    claimed = await _change_draft(
        state,
        expected_state,
        {"draft_id": draft["draft_id"], "news_id": draft["news_id"]},
        {"save_claim": token_hex(8)},
        AdminNewsState.saving.state,
    )
    if claimed is None:
        return False
    try:
        item = await news_db.update_news(
            session, int(draft["news_id"]), link_url=draft.get("target_url"), link_text=label
        )
    except (ValueError, SQLAlchemyError):
        await session.rollback()
        await _change_draft(
            state, AdminNewsState.saving.state, {"save_claim": claimed["save_claim"]}, {}, expected_state
        )
        await message.answer("Не удалось сохранить действие. Попробуйте ещё раз.")
        return False
    await _change_draft(state, AdminNewsState.saving.state, {"save_claim": claimed["save_claim"]}, {}, None)
    if item is None:
        await _show_list(message, state, session, int(draft["page"]), edit=edit)
    else:
        await _show_item(message, state, item, int(draft["page"]), clear_state=False, edit=edit)
    return True


async def _start_edit(message: Message, state: FSMContext, page: int, item=None) -> None:
    """Начинает создание или редактирование новости."""
    await state.clear()
    await state.set_state(AdminNewsState.title)
    await state.update_data(
        draft_id=token_hex(8), news_id=item.id if item else 0, page=page, image_url=item.image_url if item else None
    )
    text = "✏️ <b>Заголовок новости</b>\n\nПришлите текст до 120 символов."
    if item is not None:
        text += f"\n\nСейчас: {escape(item.title, quote=False)}"
    await _render(message, text, _cancel_keyboard(page))


async def _ask_image(message: Message, state: FSMContext, *, edit: bool = False) -> None:
    """Предлагает добавить, сохранить или убрать изображение."""
    draft = await state.get_data()
    await state.set_state(AdminNewsState.image)
    builder = InlineKeyboardBuilder()
    news_id = int(draft.get("news_id", 0))
    page = int(draft.get("page", 1))
    _button(
        builder, "Оставить изображение" if draft.get("image_url") else "Без изображения", "image_skip", news_id, page
    )
    if draft.get("image_url"):
        _button(builder, "Убрать изображение", "image_remove", news_id, page)
    _button(builder, "Отмена", "list", page=page)
    builder.adjust(1)
    text = (
        "🖼 <b>Изображение новости</b>\n\n"
        "Пришлите фото или PNG/JPEG/WebP файлом до 10 МБ или воспользуйтесь кнопками ниже.\n"
        "Чтобы сохранить прозрачный фон, отправьте изображение файлом."
    )
    if edit:
        await _render(message, text, builder.as_markup())
        message_id = message.message_id
    else:
        prompt = await message.answer(text, reply_markup=builder.as_markup(), parse_mode=ParseMode.HTML)
        message_id = prompt.message_id
    await state.update_data(image_prompt_message_id=message_id)


async def _show_preview(message: Message, state: FSMContext) -> None:
    """Показывает фото отдельно от полного текста новости."""
    draft = await state.get_data()
    matches = {"news_id": draft.get("news_id", 0), "image_prompt_message_id": draft.get("image_prompt_message_id")}
    if draft.get("draft_id"):
        matches["draft_id"] = draft["draft_id"]
    draft = await _change_draft(
        state, AdminNewsState.image.state, matches, {"preview_message_id": 0}, AdminNewsState.preview.state
    )
    if draft is None:
        return
    photo = draft.get("photo_file_id")
    image_url = draft.get("image_url")
    if not photo and isinstance(image_url, str) and image_url.startswith("/api/web/uploads/"):
        name = image_url.removeprefix("/api/web/uploads/")
        if name and "/" not in name and "\\" not in name and ".." not in name:
            path = WEB_UPLOAD_DIR / name
            if path.is_file():
                photo = FSInputFile(path)
    if photo:
        try:
            await message.answer_photo(photo, caption="Изображение новости")
        except TelegramBadRequest:
            await message.answer("Изображение сохранено, но его предпросмотр недоступен.")
    if await state.get_state() != AdminNewsState.preview.state or (await state.get_data()).get("draft_id") != draft.get(
        "draft_id"
    ):
        return
    builder = InlineKeyboardBuilder()
    news_id = int(draft.get("news_id", 0))
    page = int(draft.get("page", 1))
    _button(builder, "Сохранить" if news_id else "Опубликовать", "save", news_id, page)
    _button(builder, "Отмена", "list", page=page)
    builder.adjust(1)
    text = f"📰 <b>Предпросмотр новости</b>\n\n{_news_text(str(draft.get('title', '')), str(draft.get('content', '')))}"
    if image_url:
        text += "\n\n🖼 С изображением"
    preview = await message.answer(
        text, reply_markup=builder.as_markup(), parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
    preview_matches = {"news_id": news_id, "preview_message_id": 0}
    if draft.get("draft_id"):
        preview_matches["draft_id"] = draft["draft_id"]
    await _change_draft(
        state,
        AdminNewsState.preview.state,
        preview_matches,
        {"preview_message_id": preview.message_id},
        AdminNewsState.preview.state,
    )


async def _change_draft(
    state: FSMContext, expected_state: str, matches: dict, updates: dict, next_state: str | None
) -> dict | None:
    """Атомарно изменяет только актуальный черновик."""
    storage = state.storage
    if isinstance(storage, RedisStorage):
        state_key = storage.key_builder.build(state.key, "state")
        data_key = storage.key_builder.build(state.key, "data")
        script = (
            "if redis.call('GET',KEYS[1]) ~= ARGV[1] then return nil end; "
            "local data=redis.call('GET',KEYS[2]); if not data then return nil end; "
            "local draft=cjson.decode(data); local matches=cjson.decode(ARGV[2]); "
            "for key,value in pairs(matches) do if draft[key] ~= value then return nil end end; "
            "local updates=cjson.decode(ARGV[3]); for key,value in pairs(updates) do draft[key]=value end; "
            "local result=cjson.encode(draft); "
            "if ARGV[4] == '' then redis.call('DEL',KEYS[1],KEYS[2]); "
            "else redis.call('SET',KEYS[2],result,'KEEPTTL'); redis.call('SET',KEYS[1],ARGV[4],'KEEPTTL') end; return result"
        )
        raw = await storage.redis.eval(
            script,
            2,
            state_key,
            data_key,
            expected_state,
            storage.json_dumps(matches),
            storage.json_dumps(updates),
            next_state or "",
        )
        if raw is None:
            return None
        draft = storage.json_loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    elif isinstance(storage, MemoryStorage):
        key = (asyncio.get_running_loop(), id(storage), state.key)
        lock = _PREVIEW_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _PREVIEW_LOCKS[key] = lock
        async with lock:
            draft = await state.get_data()
            if await state.get_state() != expected_state or any(
                draft.get(key) != value for key, value in matches.items()
            ):
                return None
            draft.update(updates)
            if next_state is None:
                await state.clear()
            else:
                await state.set_data(draft)
                await state.set_state(next_state)
    else:
        raise TypeError("Хранилище черновиков новостей не поддерживается")
    return draft if isinstance(draft, dict) else None


async def _take_preview(state: FSMContext, news_id: int, message_id: int) -> dict | None:
    """Помечает предпросмотр сохраняющимся без потери текста."""
    return await _change_draft(
        state,
        AdminNewsState.preview.state,
        {"news_id": news_id, "preview_message_id": message_id},
        {"save_claim": token_hex(8)},
        AdminNewsState.saving.state,
    )


async def _finish_preview(state: FSMContext, draft: dict, *, retry: bool = False) -> None:
    """Завершает сохранение, сохраняя более новый сценарий."""
    await _change_draft(
        state,
        AdminNewsState.saving.state,
        {"save_claim": draft["save_claim"]},
        {},
        AdminNewsState.preview.state if retry else None,
    )


async def _save_preview(callback: CallbackQuery, state: FSMContext, session: AsyncSession, data: NewsCallback) -> None:
    """Сохраняет только подтверждённый актуальный предпросмотр."""
    draft = await _take_preview(state, data.news_id, callback.message.message_id)
    if draft is None:
        await callback.answer("Предпросмотр устарел. Откройте новости заново.", show_alert=True)
        return
    title = str(draft.get("title", "")).strip()
    content = str(draft.get("content", "")).strip()
    if not title or len(title) > 120 or not content or len(content) > 1000:
        await _finish_preview(state, draft)
        await callback.answer("Данные новости устарели. Начните заново.", show_alert=True)
        return
    changes = {"title": title, "content": content}
    if not data.news_id or draft.get("image_changed"):
        changes["image_url"] = draft.get("image_url")
    try:
        if data.news_id:
            item = await news_db.update_news(session, data.news_id, **changes)
        else:
            item = await news_db.create_news(session, **changes)
    except (ValueError, SQLAlchemyError):
        try:
            await session.rollback()
        finally:
            await _finish_preview(state, draft, retry=True)
        await callback.answer(
            "Не удалось сохранить новость. Повторите сохранение через несколько секунд.", show_alert=True
        )
        return
    await _finish_preview(state, draft)
    if item is None:
        await _show_list(callback.message, state, session, data.page)
        await callback.answer("Новость уже удалена.", show_alert=True)
        return
    await _show_item(callback.message, state, item, data.page, clear_state=False)
    await callback.answer("Новость сохранена." if data.news_id else "Новость опубликована.")


@router.callback_query(AdminPanelCallback.filter(F.action == "news"))
async def open_news(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Открывает новости из главного меню администратора."""
    if not await _allow(callback, state):
        return
    if isinstance(callback.message, Message):
        await _show_list(callback.message, state, session)
    await callback.answer()


@router.callback_query(NewsCallback.filter())
async def news_action(
    callback: CallbackQuery, callback_data: NewsCallback, state: FSMContext, session: AsyncSession
) -> None:
    """Обрабатывает действия с новостями."""
    if not await _allow(callback, state):
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    action = callback_data.action
    page = max(1, callback_data.page)
    if action == "list":
        await _show_list(callback.message, state, session, page)
    elif action == "create":
        await _start_edit(callback.message, state, page)
    elif action == "save":
        await _save_preview(callback, state, session, callback_data)
        return
    elif action.startswith("target_"):
        draft = await state.get_data()
        current = await state.get_state()
        expected = (
            AdminNewsState.target_label.state
            if action == "target_default"
            else AdminNewsState.target_tab.state
            if action.startswith("target_pick_")
            else AdminNewsState.target_menu.state
        )
        if (
            current != expected
            or draft.get("news_id") != callback_data.news_id
            or draft.get("target_message_id") != callback.message.message_id
        ):
            await callback.answer("Этот шаг устарел. Откройте действие новости заново.", show_alert=True)
            return
        if action == "target_detail":
            draft["target_url"] = None
            await _save_target(callback.message, state, session, draft, None, expected_state=expected)
        elif action == "target_link":
            await _change_draft(state, expected, {"draft_id": draft["draft_id"]}, {}, AdminNewsState.target_url.state)
            builder = InlineKeyboardBuilder()
            _button(builder, "Отмена", "view", callback_data.news_id, page)
            await _render(
                callback.message,
                "Пришлите ссылку http://, https:// или внутренний путь сайта, например <code>/tariffs</code>.",
                builder.as_markup(),
            )
        elif action == "target_tabs":
            targets = await list_news_cabinet_targets(session)
            updated = await _change_draft(
                state,
                expected,
                {"draft_id": draft["draft_id"]},
                {"target_choices": targets},
                AdminNewsState.target_tab.state,
            )
            if updated is None:
                await callback.answer("Этот шаг устарел.", show_alert=True)
                return
            builder = InlineKeyboardBuilder()
            for index, target in enumerate(targets):
                _button(builder, target["label"][:60], f"target_pick_{index}", callback_data.news_id, page)
            _button(builder, "Отмена", "view", callback_data.news_id, page)
            builder.adjust(1)
            await _render(
                callback.message,
                "Выберите вкладку кабинета."
                if targets
                else "Вкладки пока не настроены. Настройте меню кабинета в оформлении сайта.",
                builder.as_markup(),
            )
        elif action.startswith("target_pick_"):
            raw_index = action.removeprefix("target_pick_")
            choices = draft.get("target_choices") or []
            index = int(raw_index) if raw_index.isdigit() else -1
            if not 0 <= index < len(choices):
                await callback.answer("Вкладка недоступна. Откройте действие заново.", show_alert=True)
                return
            target = choices[index]
            updated = await _change_draft(
                state,
                expected,
                {"draft_id": draft["draft_id"]},
                {"target_url": target["href"], "target_default_label": target["label"][:80]},
                AdminNewsState.target_label.state,
            )
            if updated is not None:
                await _ask_target_label(callback.message, state, updated)
        elif action == "target_default":
            await _save_target(
                callback.message,
                state,
                session,
                draft,
                str(draft.get("target_default_label") or "Подробнее"),
                expected_state=expected,
            )
        else:
            await callback.answer("Неизвестное действие.", show_alert=True)
            return
    elif action in ("image_skip", "image_remove"):
        draft = await state.get_data()
        if (
            await state.get_state() != AdminNewsState.image.state
            or draft.get("news_id") != callback_data.news_id
            or draft.get("image_prompt_message_id") != callback.message.message_id
        ):
            await callback.answer("Этот шаг устарел. Откройте новости заново.", show_alert=True)
            return
        if action == "image_remove":
            await state.update_data(image_url=None, photo_file_id=None, image_changed=True)
        await _show_preview(callback.message, state)
    elif callback_data.news_id > 0 and action in (
        "view",
        "edit",
        "image",
        "target",
        "hide",
        "publish",
        "delete",
        "delete_confirm",
    ):
        item = await news_db.get_news(session, callback_data.news_id)
        if item is None:
            await _show_list(callback.message, state, session, page)
            await callback.answer("Новость уже удалена.", show_alert=True)
            return
        if action == "edit":
            await _start_edit(callback.message, state, page, item)
        elif action == "target":
            await _show_target_menu(callback.message, state, item, page)
        elif action == "image":
            await state.clear()
            await state.update_data(
                draft_id=token_hex(8),
                news_id=item.id,
                page=page,
                title=item.title,
                content=item.content,
                image_url=item.image_url,
            )
            await _ask_image(callback.message, state, edit=True)
        elif action in ("hide", "publish"):
            item = await news_db.update_news(session, item.id, is_active=action == "publish")
            if item is None:
                await _show_list(callback.message, state, session, page)
            else:
                await _show_item(callback.message, state, item, page)
        elif action == "delete":
            await state.clear()
            builder = InlineKeyboardBuilder()
            _button(builder, "Да, удалить", "delete_confirm", item.id, page)
            _button(builder, "Отмена", "view", item.id, page)
            builder.adjust(1)
            text = f"🗑 <b>Удалить новость?</b>\n\n{_news_text(item.title, item.content)}"
            await _render(callback.message, text, builder.as_markup())
        elif action == "delete_confirm":
            await news_db.delete_news(session, item.id)
            await _show_list(callback.message, state, session, page)
        else:
            await _show_item(callback.message, state, item, page)
    else:
        await callback.answer("Неизвестное действие. Откройте новости заново.", show_alert=True)
        return
    await callback.answer()


@router.message(StateFilter(AdminNewsState))
async def news_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Принимает короткий текст и готовит подтверждение публикации."""
    if not await _allow(message, state):
        return
    draft = await state.get_data()
    page = max(1, int(draft.get("page", 1)))
    text = (message.text or "").strip()
    if text == "/cancel":
        await _show_list(message, state, session, page, edit=False)
        return
    current = await state.get_state()
    if current in (AdminNewsState.target_menu.state, AdminNewsState.target_tab.state):
        await message.answer("Выберите действие кнопками в предыдущем сообщении или отправьте /cancel.")
        return
    if current == AdminNewsState.target_url.state:
        try:
            href = news_db.validate_news_link_url(text)
            if not href:
                raise ValueError("Пришлите непустую ссылку")
        except ValueError as error:
            await message.answer(str(error))
            return
        updated = await _change_draft(
            state,
            current,
            {"draft_id": draft["draft_id"], "news_id": draft["news_id"]},
            {"target_url": href},
            AdminNewsState.target_label.state,
        )
        if updated is not None:
            await _ask_target_label(message, state, updated)
        return
    if current == AdminNewsState.target_label.state:
        try:
            label = news_db.validate_news_link_text(text)
            if not label:
                raise ValueError("Пришлите непустую подпись до 80 символов")
        except ValueError as error:
            await message.answer(str(error))
            return
        await _save_target(message, state, session, draft, label, expected_state=current, edit=False)
        return
    if current == AdminNewsState.preview.state:
        await message.answer("Проверьте предпросмотр и нажмите кнопку сохранения или отмены.")
        return
    if current == AdminNewsState.saving.state:
        await message.answer("Новость сохраняется. Дождитесь результата.")
        return
    if current == AdminNewsState.image.state:
        document = message.document
        if document:
            allowed_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
            extension = Path(document.file_name or "").suffix.lower()
            if (
                extension not in allowed_types
                or document.mime_type != allowed_types[extension]
                or (document.file_size or 0) > MAX_UPLOAD_BYTES
            ):
                await message.answer("Пришлите PNG/JPEG/WebP файлом до 10 МБ или изображение как фото.")
                return
            media = document
        elif message.photo:
            media = message.photo[-1]
        else:
            await message.answer(
                "Пришлите фото или PNG/JPEG/WebP файлом или нажмите кнопку ниже предыдущего сообщения."
            )
            return
        url = (
            await host_telegram_photo(message.bot, media.file_id, file_name=document.file_name)
            if document
            else await host_telegram_photo(message.bot, media.file_id)
        )
        matches = {"news_id": draft.get("news_id", 0), "image_prompt_message_id": draft.get("image_prompt_message_id")}
        if draft.get("draft_id"):
            matches["draft_id"] = draft["draft_id"]
        updated = await _change_draft(
            state,
            AdminNewsState.image.state,
            matches,
            {"image_url": url, "photo_file_id": media.file_id if not document else None, "image_changed": True}
            if url
            else {},
            AdminNewsState.image.state,
        )
        if updated is None:
            return
        if url is None:
            await message.answer(
                "Не удалось загрузить фото. Пришлите другое изображение до 10 МБ или пропустите этот шаг."
            )
            return
        await _show_preview(message, state)
        return
    maximum = 120 if current == AdminNewsState.title.state else 1000
    if not text or len(text) > maximum:
        await message.answer(f"Пришлите непустой текст до {maximum} символов.", reply_markup=_cancel_keyboard(page))
        return
    if current == AdminNewsState.title.state:
        await state.update_data(title=text)
        await state.set_state(AdminNewsState.content)
        await message.answer(
            "Пришлите текст новости до 1000 символов. Он будет показан в баннере как обычный текст.",
            reply_markup=_cancel_keyboard(page),
        )
        return
    await state.update_data(content=text)
    await _ask_image(message, state)
