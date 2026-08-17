"""Controlled combined-result protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CombinedColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    name: str | None = None
    type: Literal["dimension", "metric", "derived_metric"]
    unit: str | None = None
    source_scene_id: str | None = None


class CombinedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["separate", "compare", "derived"]
    status: Literal["succeeded", "partial_succeeded", "separate"]
    keys: list[str] = Field(default_factory=list)
    time_grain: str | None = None
    columns: list[CombinedColumn] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    source_scene_ids: list[str] = Field(default_factory=list)
    warnings: list[dict[str, str]] = Field(default_factory=list)


__all__ = ["CombinedColumn", "CombinedResult"]
