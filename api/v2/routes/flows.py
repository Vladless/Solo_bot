from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_designer
from api.v2.schemas.flows import FlowCreate, FlowResponse, FlowUpdate
from database.models import WebFlow
from database.site_revision import bump_site_revision


router = APIRouter()


def _flow_to_response(flow: WebFlow) -> FlowResponse:
    return FlowResponse(
        id=flow.id,
        name=flow.name,
        nodes=flow.nodes or [],
        edges=flow.edges or [],
        entry_node_id=flow.entry_node_id,
        version=flow.version,
    )


@router.get("/flows/{flow_id}", response_model=FlowResponse, response_model_by_alias=True)
async def get_flow_public(flow_id: str, session: AsyncSession = Depends(get_session)):
    flow = await session.get(WebFlow, flow_id)
    if not flow:
        raise HTTPException(404, "Flow not found")
    return _flow_to_response(flow)


@router.get("/admin/flows", response_model=list[FlowResponse], response_model_by_alias=True)
async def list_flows(
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    result = await session.execute(select(WebFlow))
    return [_flow_to_response(f) for f in result.scalars().all()]


@router.get("/admin/flows/{flow_id}", response_model=FlowResponse, response_model_by_alias=True)
async def get_flow_admin(
    flow_id: str,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    flow = await session.get(WebFlow, flow_id)
    if not flow:
        raise HTTPException(404, "Flow not found")
    return _flow_to_response(flow)


@router.post("/admin/flows", response_model=FlowResponse, status_code=201, response_model_by_alias=True)
async def create_flow(
    body: FlowCreate,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    existing = await session.get(WebFlow, body.id)
    if existing:
        raise HTTPException(409, "Flow with this ID already exists")

    flow = WebFlow(
        id=body.id,
        name=body.name,
        nodes=[n.model_dump() for n in body.nodes],
        edges=[e.model_dump() for e in body.edges],
        entry_node_id=body.entry_node_id,
        version=1,
        updated_at=datetime.now(UTC),
    )
    session.add(flow)
    await bump_site_revision(session)
    await session.refresh(flow)
    return _flow_to_response(flow)


@router.put("/admin/flows/{flow_id}", response_model=FlowResponse, response_model_by_alias=True)
async def update_flow(
    flow_id: str,
    body: FlowUpdate,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    flow = await session.get(WebFlow, flow_id)
    if not flow:
        raise HTTPException(404, "Flow not found")

    if body.name is not None:
        flow.name = body.name
    flow.nodes = [n.model_dump() for n in body.nodes]
    flow.edges = [e.model_dump() for e in body.edges]
    flow.entry_node_id = body.entry_node_id
    flow.version = flow.version + 1
    flow.updated_at = datetime.now(UTC)

    await bump_site_revision(session)
    await session.refresh(flow)
    return _flow_to_response(flow)


@router.delete("/admin/flows/{flow_id}", status_code=204)
async def delete_flow(
    flow_id: str,
    session: AsyncSession = Depends(get_session),
    _identity=Depends(verify_identity_designer),
):
    flow = await session.get(WebFlow, flow_id)
    if not flow:
        raise HTTPException(404, "Flow not found")
    await session.delete(flow)
    await bump_site_revision(session)
