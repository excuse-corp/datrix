"""Frontend-safe chart specification protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChartOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_key: str | None = None
    value_keys: list[str] = Field(default_factory=list)
    unit: str | None = None


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["metric", "line", "bar", "grouped_bar", "donut", "table"]
    title: str
    option: ChartOption
    data: list[dict[str, Any]] = Field(default_factory=list)


__all__ = ["ChartOption", "ChartSpec"]
