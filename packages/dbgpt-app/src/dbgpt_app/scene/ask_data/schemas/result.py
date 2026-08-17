"""Frontend-safe ResultBundle contract."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .chart import ChartSpec
from .combine import CombinedResult


class SceneResultSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    scene_id: str
    status: str
    snapshot_id: str
    row_count: int
    truncated: bool
    warnings: list[str] = Field(default_factory=list)


class ResultBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    answer: str | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)
    scene_summaries: list[SceneResultSummary] = Field(default_factory=list)
    combined: CombinedResult | None = None
    charts: list[ChartSpec] = Field(default_factory=list)
    warnings: list[dict[str, str]] = Field(default_factory=list)
    rag_references: list[str] = Field(default_factory=list)


__all__ = ["ResultBundle", "SceneResultSummary"]
