import re

from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import News


def _validate_text(value: str, label: str, limit: int) -> str:
    """Проверяет и очищает короткий текст новости."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} не может быть пустым")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{label}: максимум {limit} символов")
    return value


def _optional_text(value: object, label: str, limit: int) -> str | None:
    """Очищает необязательное поле новости."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label}: ожидается строка")
    value = value.strip()
    if not value:
        return None
    if len(value) > limit:
        raise ValueError(f"{label}: максимум {limit} символов")
    return value


def _safe_url(value: str, *, internal: bool = False) -> str:
    """Разрешает безопасную веб-ссылку без обхода пути."""
    if "\\" in value or any(ord(character) <= 32 or ord(character) == 127 for character in value):
        raise ValueError("Недопустимый адрес ссылки")
    try:
        parsed = urlsplit(value)
        path = parsed.path
        for _ in range(3):
            decoded = unquote(path)
            if decoded == path:
                break
            path = decoded
        if "\\" in path or any(segment in (".", "..") for segment in path.split("/")):
            raise ValueError("Недопустимый путь ссылки")
        if internal and value.startswith("/") and not value.startswith("//"):
            if parsed.netloc or parsed.scheme or path.startswith("//"):
                raise ValueError("Недопустимый адрес ссылки")
            return value
        if (
            parsed.scheme.lower() not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Укажите ссылку http:// или https://")
        _ = parsed.port
    except ValueError as error:
        raise ValueError("Недопустимый адрес ссылки") from error
    return value


def validate_news_image_url(value: object) -> str | None:
    """Разрешает загруженную картинку или внешний веб-адрес."""
    value = _optional_text(value, "Адрес изображения", 2048)
    if value is None:
        return None
    if value.startswith("/"):
        if not re.fullmatch(r"/api/web/uploads/[A-Za-z0-9][A-Za-z0-9._-]*", value) or ".." in value:
            raise ValueError("Недопустимый адрес изображения")
        return value
    return _safe_url(value)


def validate_news_link_url(value: object) -> str | None:
    """Разрешает веб-ссылку или внутренний путь сайта."""
    value = _optional_text(value, "Адрес кнопки", 2048)
    return _safe_url(value, internal=True) if value is not None else None


def validate_news_link_text(value: object) -> str | None:
    """Ограничивает подпись кнопки новости."""
    return _optional_text(value, "Текст кнопки", 80)


def validate_news_image_mode(value: object) -> str:
    """Проверяет способ размещения изображения."""
    if not isinstance(value, str) or value not in ("icon", "background"):
        raise ValueError("Выберите изображение сбоку или фоном")
    return value


def validate_news_datetime(value: object) -> datetime | None:
    """Приводит дату с часовым поясом к UTC."""
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Укажите дату и время с часовым поясом")
    try:
        return value.astimezone(UTC)
    except (OverflowError, ValueError) as error:
        raise ValueError("Недопустимая дата показа новости") from error


def _stored_datetime(value: datetime) -> datetime:
    """Восстанавливает UTC для дат из хранилища."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def validate_news_period(starts_at: datetime | None, ends_at: datetime | None) -> None:
    """Проверяет границы периода публикации."""
    if starts_at is not None and ends_at is not None and _stored_datetime(ends_at) <= _stored_datetime(starts_at):
        raise ValueError("Окончание показа должно быть позже начала")


def _publication_conditions():
    """Ограничивает выдачу текущим периодом показа."""
    now = datetime.now(UTC)
    return (
        News.is_active.is_(True),
        or_(News.starts_at.is_(None), News.starts_at <= now),
        or_(News.ends_at.is_(None), News.ends_at > now),
    )


async def list_news(
    session: AsyncSession, *, active_only: bool = False, limit: int = 50, offset: int = 0
) -> list[News]:
    """Возвращает новости от новых к старым."""
    statement = select(News)
    if active_only:
        statement = statement.where(*_publication_conditions())
    statement = statement.order_by(News.created_at.desc(), News.id.desc()).limit(limit).offset(offset)
    return list((await session.scalars(statement)).all())


async def count_news(session: AsyncSession, *, active_only: bool = False) -> int:
    """Считает все или опубликованные новости."""
    statement = select(func.count()).select_from(News)
    if active_only:
        statement = statement.where(*_publication_conditions())
    return int(await session.scalar(statement) or 0)


async def get_news(session: AsyncSession, news_id: int) -> News | None:
    """Находит новость по номеру."""
    return await session.get(News, news_id)


async def create_news(
    session: AsyncSession,
    *,
    title: str,
    content: str,
    is_active: bool = True,
    image_url: str | None = None,
    image_mode: str = "icon",
    link_url: str | None = None,
    link_text: str | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> News:
    """Сохраняет новую новость."""
    title = _validate_text(title, "Заголовок", 120)
    content = _validate_text(content, "Текст новости", 1000)
    if not isinstance(is_active, bool):
        raise ValueError("Статус публикации должен быть логическим значением")
    starts_at = validate_news_datetime(starts_at)
    ends_at = validate_news_datetime(ends_at)
    validate_news_period(starts_at, ends_at)
    item = News(
        title=title,
        content=content,
        is_active=is_active,
        image_url=validate_news_image_url(image_url),
        image_mode=validate_news_image_mode(image_mode),
        link_url=validate_news_link_url(link_url),
        link_text=validate_news_link_text(link_text),
        starts_at=starts_at,
        ends_at=ends_at,
    )
    session.add(item)
    await session.flush()
    await session.commit()
    return item


async def update_news(session: AsyncSession, news_id: int, **changes: object) -> News | None:
    """Обновляет текст или видимость новости."""
    if not changes or changes.keys() - {
        "title",
        "content",
        "is_active",
        "image_url",
        "image_mode",
        "link_url",
        "link_text",
        "starts_at",
        "ends_at",
    }:
        raise ValueError("Укажите поля новости для изменения")
    if "title" in changes:
        changes["title"] = _validate_text(changes["title"], "Заголовок", 120)
    if "content" in changes:
        changes["content"] = _validate_text(changes["content"], "Текст новости", 1000)
    if "is_active" in changes and not isinstance(changes["is_active"], bool):
        raise ValueError("Статус публикации должен быть логическим значением")
    for field, validate in (
        ("image_url", validate_news_image_url),
        ("image_mode", validate_news_image_mode),
        ("link_url", validate_news_link_url),
        ("link_text", validate_news_link_text),
        ("starts_at", validate_news_datetime),
        ("ends_at", validate_news_datetime),
    ):
        if field in changes:
            changes[field] = validate(changes[field])
    item = await get_news(session, news_id)
    if item is None:
        return None
    validate_news_period(changes.get("starts_at", item.starts_at), changes.get("ends_at", item.ends_at))
    for field, value in changes.items():
        setattr(item, field, value)
    item.updated_at = datetime.now(UTC)
    await session.flush()
    await session.commit()
    return item


async def delete_news(session: AsyncSession, news_id: int) -> bool:
    """Удаляет новость из общего списка."""
    result = await session.execute(delete(News).where(News.id == news_id))
    await session.commit()
    return bool(result.rowcount)
