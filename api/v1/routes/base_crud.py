from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from sqlalchemy import (
    inspect as sa_inspect,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import InstrumentedAttribute

from api.depends import get_session, verify_admin_token
from database.access.resolution import resolve_user_optional
from database.models import Admin
from services.formatting import get_site_gift_link, get_telegram_gift_link


def _apply_user_relationship_loader(model: type, stmt):
    if model.__name__ in ("ManualBan", "BlockedUser", "TemporaryData"):
        return stmt.options(selectinload(model.user))
    return stmt


def cast_identifier_type(field: InstrumentedAttribute, value: int | str):
    column_type = type(field.property.columns[0].type).__name__
    if column_type in ("Integer", "BigInteger"):
        return int(value)
    return value


def normalize_outgoing_object(obj: object) -> None:
    if hasattr(obj, "vless") and obj.vless is None:
        obj.vless = False
    cls_name = type(obj).__name__
    if cls_name == "Gift":
        gift_id = getattr(obj, "gift_id", None)
        if gift_id:
            obj.telegram_gift_link = get_telegram_gift_link(gift_id)
            obj.site_gift_link = get_site_gift_link(gift_id)
    if cls_name in ("ManualBan", "BlockedUser", "TemporaryData"):
        insp = sa_inspect(obj)
        stored = getattr(obj, "tg_id", None)
        if "user" in insp.unloaded:
            obj.tg_id = stored
            return
        rel = getattr(obj, "user", None)
        rel_tg = getattr(rel, "tg_id", None) if rel is not None else None
        obj.tg_id = stored if stored is not None else rel_tg


def to_schema(schema_response: type, obj: object):
    normalize_outgoing_object(obj)
    return schema_response.model_validate(obj, from_attributes=True)


def generate_crud_router(
    *,
    model: type,
    schema_response: type,
    schema_create: type,
    schema_update: type,
    identifier_field: str = "tg_id",
    parameter_name: str = "tg_id",
    extra_get_by_email: bool = False,
    telegram_path_to_user_id: bool = False,
    enabled_methods: list[str] = ("get_all", "get_one", "get_by_email", "create", "update", "delete"),
) -> APIRouter:
    router = APIRouter()

    async def _path_filter(session: AsyncSession, value: int | str):
        if telegram_path_to_user_id:
            u = await resolve_user_optional(session, int(value))
            if u is None:
                return None
            return model.user_id, u.id
        field = getattr(model, identifier_field)
        return field, cast_identifier_type(field, value)

    if "get_all" in enabled_methods:

        @router.get("/", response_model=list[schema_response])
        async def get_all(
            admin: Admin = Depends(verify_admin_token),
            session: AsyncSession = Depends(get_session),
        ):
            result = await session.execute(_apply_user_relationship_loader(model, select(model)))
            items = result.scalars().all()
            for item in items:
                normalize_outgoing_object(item)
            return [schema_response.model_validate(item, from_attributes=True) for item in items]

    if "get_by_email" in enabled_methods and extra_get_by_email:

        @router.get("/by_email", response_model=schema_response)
        async def get_by_email(
            email: str = Query(...),
            admin: Admin = Depends(verify_admin_token),
            session: AsyncSession = Depends(get_session),
        ):
            result = await session.execute(select(model).where(model.email == email))
            obj = result.scalar_one_or_none()
            if not obj:
                raise HTTPException(status_code=404, detail="Not found by email")
            return to_schema(schema_response, obj)

    if "get_one" in enabled_methods:

        @router.get(f"/{{{parameter_name}}}", response_model=schema_response)
        async def get_one(
            value: int | str = Path(..., alias=parameter_name),
            admin: Admin = Depends(verify_admin_token),
            session: AsyncSession = Depends(get_session),
        ):
            resolved = await _path_filter(session, value)
            if resolved is None:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            field, casted = resolved
            result = await session.execute(_apply_user_relationship_loader(model, select(model).where(field == casted)))
            obj = result.scalar_one_or_none()
            if not obj:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            return to_schema(schema_response, obj)

    if "get_all_by_field" in enabled_methods:

        @router.get(f"/all/{{{parameter_name}}}", response_model=list[schema_response])
        async def get_all_by_field(
            value: int | str = Path(..., alias=parameter_name),
            admin: Admin = Depends(verify_admin_token),
            session: AsyncSession = Depends(get_session),
        ):
            resolved = await _path_filter(session, value)
            if resolved is None:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            field, casted = resolved
            result = await session.execute(_apply_user_relationship_loader(model, select(model).where(field == casted)))
            objs = result.scalars().all()
            if not objs:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            for obj in objs:
                normalize_outgoing_object(obj)
            return [schema_response.model_validate(obj, from_attributes=True) for obj in objs]

    if "create" in enabled_methods:

        @router.post("/", response_model=schema_response)
        async def create(
            payload: Any = Body(...),
            admin: Admin = Depends(verify_admin_token),
            session: AsyncSession = Depends(get_session),
        ):
            validated = schema_create.model_validate(payload)
            data = validated.model_dump(exclude_unset=True)
            if "days" in data and data["days"] == 0:
                data["days"] = None
            obj = model(**data)
            session.add(obj)
            await session.refresh(obj)
            return to_schema(schema_response, obj)

    if "update" in enabled_methods:

        @router.patch(f"/{{{parameter_name}}}", response_model=schema_response)
        async def update(
            payload: Any = Body(...),
            value: int | str = Path(..., alias=parameter_name),
            admin: Admin = Depends(verify_admin_token),
            session: AsyncSession = Depends(get_session),
        ):
            resolved = await _path_filter(session, value)
            if resolved is None:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            field, casted = resolved
            result = await session.execute(select(model).where(field == casted))
            obj = result.scalar_one_or_none()
            if not obj:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")

            validated = schema_update.model_validate(payload)
            for k, v in validated.model_dump(exclude_unset=True).items():
                setattr(obj, k, v)

            await session.refresh(obj)
            return to_schema(schema_response, obj)

    if "delete" in enabled_methods:

        @router.delete(f"/{{{parameter_name}}}", response_model=dict)
        async def delete(
            value: int | str = Path(..., alias=parameter_name),
            admin: Admin = Depends(verify_admin_token),
            session: AsyncSession = Depends(get_session),
        ):
            resolved = await _path_filter(session, value)
            if resolved is None:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            field, casted = resolved
            result = await session.execute(select(model).where(field == casted))
            obj = result.scalar_one_or_none()
            if not obj:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")

            await session.delete(obj)
            return {"detail": f"{model.__name__} deleted"}

    return router
