from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import InstrumentedAttribute

from api.depends import get_session, verify_identity_admin
from api.v1.routes.base_crud import (
    _apply_user_relationship_loader,
    cast_identifier_type,
    normalize_outgoing_object,
    to_schema,
)
from database.access.resolution import resolve_user_optional, user_id_from_legacy_ref


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
    legacy_user_ref: bool = False,
    enabled_methods: list[str] = ("get_all", "get_one", "get_by_email", "create", "update", "delete"),
) -> APIRouter:
    router = APIRouter()

    async def _path_filter(session: AsyncSession, value: int | str):
        if telegram_path_to_user_id:
            u = await resolve_user_optional(session, int(value))
            if u is None:
                return None
            return model.user_id, u.id
        if legacy_user_ref:
            uid = await user_id_from_legacy_ref(session, int(value))
            return (model.id, uid) if uid is not None else None
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
            identity=Depends(verify_identity_admin),
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

        @router.post("", response_model=schema_response)
        async def create(
            payload: Any = Body(...),
            identity=Depends(verify_identity_admin),
            session: AsyncSession = Depends(get_session),
        ):
            validated = schema_create.model_validate(payload)
            data = validated.model_dump(exclude_unset=True)
            if "days" in data and data["days"] == 0:
                data["days"] = None
            obj = model(**data)
            session.add(obj)
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
            resolved = await _path_filter(session, value)
            if resolved is None:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            field, casted = resolved
            result = await session.execute(select(model).where(field == casted))
            obj = result.scalar_one_or_none()
            if not obj:
                raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
            await session.delete(obj)
            await session.commit()
            return {"detail": f"{model.__name__} deleted"}

    return router


__all__ = ("generate_crud_router", "to_schema", "normalize_outgoing_object", "cast_identifier_type")
