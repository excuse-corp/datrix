"""Business-key-only QuerySpec and SceneResult protocols."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end_exclusive: datetime
    timezone: str

    @model_validator(mode="after")
    def validate_order(self) -> "TimeRange":
        if self.start >= self.end_exclusive:
            raise ValueError("start must be earlier than end_exclusive")
        return self


class QueryFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str = Field(min_length=1)
    operator: str = Field(min_length=1)
    values: list[Any] = Field(min_length=1)


class OrderBy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    direction: str = "asc"


class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = "query"
    dimensions: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(min_length=1)
    time_grain: str | None = None
    time_range: TimeRange | None = None
    named_filters: list[str] = Field(default_factory=list)
    filters: list[QueryFilter] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int = Field(default=1000, ge=1)


class ResultColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    type: str
    label_key: str | None = None
    unit: str | None = None


class SceneResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    scene_id: str
    scene_revision: int | str
    snapshot_id: str
    status: str
    grain: list[str] = Field(default_factory=list)
    time_grain: str | None = None
    time_range: TimeRange | None = None
    columns: list[ResultColumn] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    rag: dict[str, Any] = Field(default_factory=dict)
    query_spec_hash: str
    compiler_version: str
    sql_hash: str
    duration_ms: int = 0
    warnings: list[str] = Field(default_factory=list)
    error: dict[str, Any] | None = None


__all__ = [
    "OrderBy",
    "QueryFilter",
    "QuerySpec",
    "ResultColumn",
    "SceneResult",
    "TimeRange",
]
