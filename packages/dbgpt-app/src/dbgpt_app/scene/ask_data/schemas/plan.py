"""Structured main-agent planning protocol."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..query.query_spec import QueryFilter, TimeRange


class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    field: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)


class PlanTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    metrics: list[str] = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[QueryFilter] = Field(default_factory=list)
    time_range: TimeRange | None = None
    time_grain: str | None = None
    named_filters: list[str] = Field(default_factory=list)
    order_by: list[dict[str, str]] = Field(default_factory=list)
    limit: int = 1000
    ontology_refs: list[str] = Field(default_factory=list)


class CombinePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["separate", "compare", "derived"] = "separate"
    keys: list[str] = Field(default_factory=list)
    derived_metrics: list[str] = Field(default_factory=list)
    relation_path_id: str | None = None


class MainAgentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["execute", "clarify", "reject"]
    clarification: Clarification | None = None
    tasks: list[PlanTask] = Field(default_factory=list)
    combine: CombinePlan | None = None
    reason_code: str | None = None
    message: str | None = None
    ontology_snapshot_id: str | None = None
    ontology_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_action_shape(self) -> "MainAgentPlan":
        if self.action == "execute" and (not self.tasks or self.combine is None):
            raise ValueError("execute requires tasks and combine")
        if self.action == "clarify" and self.clarification is None:
            raise ValueError("clarify requires clarification")
        if self.action == "reject" and not self.reason_code:
            raise ValueError("reject requires reason_code")
        return self


__all__ = ["Clarification", "CombinePlan", "MainAgentPlan", "PlanTask"]
