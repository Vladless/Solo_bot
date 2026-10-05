from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


_FLOW_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class EdgeConditionSchema(BaseModel):
    model_config = _FLOW_CONFIG

    field: str
    operator: str
    value: Any = None


class EdgeConditionGroupSchema(BaseModel):
    model_config = _FLOW_CONFIG

    logic: Literal["and", "or"] = "and"
    conditions: list[EdgeConditionSchema] = []


class FlowEdgeSchema(BaseModel):
    model_config = _FLOW_CONFIG

    id: str
    source: str
    target: str
    condition: EdgeConditionSchema | None = None
    condition_group: EdgeConditionGroupSchema | None = None
    label: str | None = None
    priority: int | None = None


class FlowNodeActionConfigSchema(BaseModel):
    model_config = _FLOW_CONFIG

    action_type: str
    params: dict[str, Any] = {}


class FlowNodeSchema(BaseModel):
    model_config = _FLOW_CONFIG

    id: str
    type: str
    label: str
    label_en: str | None = None
    enabled: bool = True
    page_slug: str | None = None
    cabinet_tab: str | None = None
    screen_group: str | None = None
    screen_id: str | None = None
    auto_skip_if: EdgeConditionSchema | EdgeConditionGroupSchema | None = None
    action_config: FlowNodeActionConfigSchema | None = None
    config: dict = {}
    position: dict


class FlowResponse(BaseModel):
    model_config = _FLOW_CONFIG

    id: str
    name: str
    nodes: list[FlowNodeSchema]
    edges: list[FlowEdgeSchema]
    entry_node_id: str | None
    version: int


class FlowUpdate(BaseModel):
    model_config = _FLOW_CONFIG

    name: str | None = None
    nodes: list[FlowNodeSchema]
    edges: list[FlowEdgeSchema]
    entry_node_id: str | None = None


class FlowCreate(BaseModel):
    model_config = _FLOW_CONFIG

    id: str
    name: str
    nodes: list[FlowNodeSchema] = []
    edges: list[FlowEdgeSchema] = []
    entry_node_id: str | None = None
