from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin_permissions import require_full_admin
from api.depends import get_session, verify_identity_admin
from database import news as news_db
from database.models import Identity
from database.news_targets import list_news_cabinet_targets


router = APIRouter()
admin_router = APIRouter()
DbSession = Annotated[AsyncSession, Depends(get_session)]

NewsTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
NewsContent = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
NewsImageUrl = Annotated[str | None, AfterValidator(news_db.validate_news_image_url)]
NewsLinkUrl = Annotated[str | None, AfterValidator(news_db.validate_news_link_url)]
NewsLinkText = Annotated[str | None, AfterValidator(news_db.validate_news_link_text)]
NewsImageMode = Literal["icon", "background"]
NewsDateTime = Annotated[AwareDatetime | None, AfterValidator(news_db.validate_news_datetime)]


class NewsCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: NewsTitle
    content: NewsContent
    is_active: bool = Field(True, strict=True)
    image_url: NewsImageUrl = None
    image_mode: NewsImageMode = "icon"
    link_url: NewsLinkUrl = None
    link_text: NewsLinkText = None
    starts_at: NewsDateTime = None
    ends_at: NewsDateTime = None

    @model_validator(mode="after")
    def valid_period(self) -> "NewsCreate":
        """Проверяет полный период новой публикации."""
        news_db.validate_news_period(self.starts_at, self.ends_at)
        return self


class NewsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: NewsTitle | None = None
    content: NewsContent | None = None
    is_active: bool | None = Field(None, strict=True)
    image_url: NewsImageUrl = None
    image_mode: NewsImageMode | None = None
    link_url: NewsLinkUrl = None
    link_text: NewsLinkText = None
    starts_at: NewsDateTime = None
    ends_at: NewsDateTime = None

    @field_validator("title", "content", "is_active", "image_mode")
    @classmethod
    def reject_null(cls, value: object) -> object:
        """Запрещает сбрасывать обязательные поля."""
        if value is None:
            raise ValueError("Поле не может быть пустым")
        return value

    @model_validator(mode="after")
    def require_changes(self) -> "NewsUpdate":
        """Требует хотя бы одно изменяемое поле."""
        if not self.model_fields_set:
            raise ValueError("Укажите поля для изменения")
        news_db.validate_news_period(self.starts_at, self.ends_at)
        return self


class NewsItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    content: str
    is_active: bool
    image_url: str | None
    image_mode: NewsImageMode
    link_url: str | None
    link_text: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", "starts_at", "ends_at")
    def utc_timestamp(self, value: datetime | None) -> str | None:
        """Передаёт время новости с явным часовым поясом."""
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class PublicNewsResponse(BaseModel):
    items: list[NewsItem]


class AdminNewsResponse(PublicNewsResponse):
    total: int


class NewsCabinetTarget(BaseModel):
    id: str
    label: str
    href: str


class NewsCabinetTargetsResponse(BaseModel):
    items: list[NewsCabinetTarget]


async def _news_admin(
    identity: Annotated[Identity, Depends(verify_identity_admin)],
    session: DbSession,
) -> Identity:
    """Проверяет актуальные полные права администратора."""
    await require_full_admin(session, identity)
    return identity


AdminIdentity = Annotated[Identity, Depends(_news_admin)]


@admin_router.get("/cabinet-targets", response_model=NewsCabinetTargetsResponse)
async def cabinet_targets(response: Response, session: DbSession, _identity: AdminIdentity):
    """Возвращает реальные вкладки для выбора действия новости."""
    response.headers["Cache-Control"] = "private, no-store"
    return {"items": await list_news_cabinet_targets(session)}


@router.get("", response_model=PublicNewsResponse)
async def public_news(
    response: Response,
    session: DbSession,
    limit: int = Query(10, ge=1, le=20),
):
    """Возвращает опубликованные новости для баннера."""
    response.headers["Cache-Control"] = "no-store"
    return {"items": await news_db.list_news(session, active_only=True, limit=limit)}


@admin_router.get("", response_model=AdminNewsResponse)
async def admin_news(
    session: DbSession,
    _identity: AdminIdentity,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Возвращает новости для управления публикациями."""
    return {
        "items": await news_db.list_news(session, limit=limit, offset=offset),
        "total": await news_db.count_news(session),
    }


@admin_router.post("", response_model=NewsItem)
async def create_news(
    body: NewsCreate,
    session: DbSession,
    _identity: AdminIdentity,
):
    """Публикует новость из веб-админки."""
    return await news_db.create_news(session, **body.model_dump())


@admin_router.patch("/{news_id}", response_model=NewsItem)
async def update_news(
    body: NewsUpdate,
    news_id: Annotated[int, Path(gt=0)],
    session: DbSession,
    _identity: AdminIdentity,
):
    """Меняет содержимое или статус публикации."""
    try:
        item = await news_db.update_news(session, news_id, **body.model_dump(exclude_unset=True))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if item is None:
        raise HTTPException(status_code=404, detail="Новость не найдена")
    return item


@admin_router.delete("/{news_id}")
async def delete_news(
    news_id: Annotated[int, Path(gt=0)],
    session: DbSession,
    _identity: AdminIdentity,
):
    """Удаляет новость из веб-админки."""
    if not await news_db.delete_news(session, news_id):
        raise HTTPException(status_code=404, detail="Новость не найдена")
    return {"ok": True}
