from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import InstrumentedAttribute

from api.admin_permissions import require_admin_crud_permissions
from api.depends import get_session, verify_identity_admin
from api.v1.routes.base_crud import (
    _apply_user_relationship_loader,
    cast_identifier_type,
    normalize_outgoing_object,
    to_schema,
)
from database.access.identity_cache import invalidate_identity_cache
from database.access.resolution import TelegramId, resolve_user_optional


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
            u = await resolve_user_optional(session, TelegramId(value))
            if u is None:
                return None
            return getattr(model, identifier_field), u.id
        field = getattr(model, identifier_field)
        return field, cast_identifier_type(field, value)

    if "get_all" in enabled_methods:

        @router.get("", response_model=list[schema_response])
        async def get_all(
            limit: int | None = Query(None, ge=1, le=1000),
            offset: int = Query(0, ge=0),
            identity=Depends(verify_identity_admin),
            session: AsyncSession = Depends(get_session),
        ):
            await require_admin_crud_permissions(session, identity, model, "read")
            stmt = _apply_user_relationship_loader(model, select(model))
            if limit is not None:
                pk = model.__mapper__.primary_key[0]
                stmt = stmt.order_by(pk.desc()).limit(limit).offset(offset)
            result = await session.execute(stmt)
            items = result.scalars().all()
            for item in items:
                normalize_outgoing_object(item)
            return [schema_response.model_validate(item, from_attributes=True) for item in items]

    if "get_by_email" in enabled_methods and extra_get_by_email:

        @router.get("/by_email", response_model=schema_response)
        async def get_by_email(
            email: str = Query(...),
            identity=Depends(verify_identity_admin),
            session: AsyncSession = Depends(get_session),
        ):
            await require_admin_crud_permissions(session, identity, model, "read")
            result = await session.execute(select(model).where(model.email == email))
            obj = result.scalar_one_or_none()
            if not obj:
                raise HTTPException(status_code=404, detail="Not found by email")
            return to_schema(schema_response, obj)

    if "get_one" in enabled_methods:

        @router.get(f"/{{{parameter_name}}}", response_model=schema_response)
        async def get_one(
            value: int | str = Path(..., alias=parameter_name),
            identity=Depends(verify_identity_admin),
            session: AsyncSession = Depends(get_session),
        ):
            await require_admin_crud_permissions(session, identity, model, "read")
            resolved = await _path_filter(session, value)
            if resolved is None:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            field, casted = resolved
            result = await session.execute(_apply_user_relationship_loader(model, select(model).where(field == casted)))
            try:
                obj = result.scalar_one_or_none()
            except MultipleResultsFound as exc:
                raise HTTPException(
                    status_code=409,
                    detail="Найдено несколько записей. Укажите полный ключ записи или воспользуйтесь списком.",
                ) from exc
            if not obj:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            return to_schema(schema_response, obj)

    if "get_all_by_field" in enabled_methods:

        @router.get(f"/all/{{{parameter_name}}}", response_model=list[schema_response])
        async def get_all_by_field(
            value: int | str = Path(..., alias=parameter_name),
            identity=Depends(verify_identity_admin),
            session: AsyncSession = Depends(get_session),
        ):
            await require_admin_crud_permissions(session, identity, model, "read")
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

        @router.post("", response_model=schema_response)
        async def create(
            payload: Any = Body(...),
            identity=Depends(verify_identity_admin),
            session: AsyncSession = Depends(get_session),
        ):
            await require_admin_crud_permissions(
                session, identity, model, "create", schema_create.model_validate(payload).model_dump(exclude_unset=True)
            )
            validated = schema_create.model_validate(payload)
            data = validated.model_dump(exclude_unset=True)
            if "days" in data and data["days"] == 0:
                data["days"] = None
            obj = model(**data)
            session.add(obj)
            await session.flush()
            if model.__name__ == "User":
                await invalidate_identity_cache(session, user_ids=(obj.id,))
            await session.commit()
            await session.refresh(obj)
            return to_schema(schema_response, obj)

    if "update" in enabled_methods:

        @router.patch(f"/{{{parameter_name}}}", response_model=schema_response)
        async def update(
            payload: Any = Body(...),
            value: int | str = Path(..., alias=parameter_name),
            identity=Depends(verify_identity_admin),
            session: AsyncSession = Depends(get_session),
        ):
            await require_admin_crud_permissions(
                session, identity, model, "update", schema_update.model_validate(payload).model_dump(exclude_unset=True)
            )
            resolved = await _path_filter(session, value)
            if resolved is None:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            field, casted = resolved
            result = await session.execute(select(model).where(field == casted))
            try:
                obj = result.scalar_one_or_none()
            except MultipleResultsFound as exc:
                raise HTTPException(
                    status_code=409,
                    detail="Найдено несколько записей. Укажите полный ключ записи или воспользуйтесь списком.",
                ) from exc
            if not obj:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            validated = schema_update.model_validate(payload)
            for k, v in validated.model_dump(exclude_unset=True).items():
                setattr(obj, k, v)
            await session.flush()
            if model.__name__ == "User":
                await invalidate_identity_cache(session, user_ids=(obj.id,))
            await session.commit()
            await session.refresh(obj)
            return to_schema(schema_response, obj)

    if "delete" in enabled_methods:

        @router.delete(f"/{{{parameter_name}}}", response_model=dict)
        async def delete(
            value: int | str = Path(..., alias=parameter_name),
            identity=Depends(verify_identity_admin),
            session: AsyncSession = Depends(get_session),
        ):
            await require_admin_crud_permissions(session, identity, model, "delete")
            resolved = await _path_filter(session, value)
            if resolved is None:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            field, casted = resolved
            result = await session.execute(select(model).where(field == casted))
            try:
                obj = result.scalar_one_or_none()
            except MultipleResultsFound as exc:
                raise HTTPException(
                    status_code=409,
                    detail="Найдено несколько записей. Укажите полный ключ записи или воспользуйтесь списком.",
                ) from exc
            if not obj:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            if model.__name__ == "ManualBan":
                await invalidate_identity_cache(session, user_ids=(obj.user_id,))
            await session.delete(obj)
            await session.commit()
            return {"detail": f"{model.__name__} deleted"}

    return router


__all__ = ("generate_crud_router", "to_schema", "normalize_outgoing_object", "cast_identifier_type")
