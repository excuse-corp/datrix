"""Semantic configuration protocol for ask-data scenes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _SemanticModel(BaseModel):
    # Extra keys are rejected so an accidentally misspelled safety setting cannot
    # silently become part of a Snapshot.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AgentConfig(_SemanticModel):
    role: str = ""
    capabilities: list[str] = Field(default_factory=list)
    cannot_do: list[str] = Field(default_factory=list)


class GrainConfig(_SemanticModel):
    description: str = ""
    key_fields: list[str] = Field(default_factory=list)


class TimeConfig(_SemanticModel):
    field: str | None = None
    name: str | None = None
    timezone: str = "UTC"
    required: bool = False
    granularities: list[str] = Field(default_factory=list)


class DimensionConfig(_SemanticModel):
    key: str = Field(min_length=1)
    field: str = Field(min_length=1)
    label_field: str | None = None
    name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    groupable: bool = True
    filter_operators: list[str] = Field(default_factory=lambda: ["eq"])
    value_mapping: dict[str, Any] = Field(default_factory=dict)


class MetricConfig(_SemanticModel):
    key: str = Field(min_length=1)
    name: str | None = None
    field: str | None = None
    aggregation: str = "sum"
    unit: str | None = None
    # The public document uses ``additivity``.  ``additive`` is retained as a
    # compatible input spelling for early drafts.
    additivity: str | None = None
    additive: bool | None = None


class QueryLimits(_SemanticModel):
    max_rows: int = Field(default=1000, ge=1, le=100_000)
    max_columns: int = Field(default=100, ge=1, le=500)
    max_cell_bytes: int = Field(default=16_384, ge=256, le=1_048_576)
    max_result_bytes: int = Field(default=4 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    allow_detail: bool = False
    max_spec_attempts: int = Field(default=2, ge=1, le=5)

    @property
    def timeout_ms(self) -> int:
        return self.timeout_seconds * 1000


class NamedFilter(_SemanticModel):
    key: str = Field(min_length=1)
    name: str | None = None
    conditions: list[dict[str, Any]] = Field(default_factory=list)


class RagConfig(_SemanticModel):
    required_on_activate: bool = False
    fixed_sections: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=0, le=50)
    include_documents: list[str] = Field(default_factory=list)


class SemanticConfig(_SemanticModel):
    """Validated YAML front-matter for one scene revision."""

    schema_version: str = "1"
    scene_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    data_source: str = Field(min_length=1)
    view: str = Field(min_length=1)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    grain: GrainConfig = Field(default_factory=GrainConfig)
    time: TimeConfig = Field(default_factory=TimeConfig)
    dimensions: list[DimensionConfig] = Field(default_factory=list)
    metrics: list[MetricConfig] = Field(default_factory=list)
    named_filters: list[NamedFilter] = Field(default_factory=list)
    value_mapping: dict[str, Any] = Field(default_factory=dict)
    common_keys: list[str] = Field(default_factory=list)
    derived_metrics: dict[str, Any] | list[Any] = Field(default_factory=list)
    query_limits: QueryLimits = Field(default_factory=QueryLimits, alias="query")
    guidance: dict[str, Any] = Field(default_factory=dict)
    rag: RagConfig = Field(default_factory=RagConfig)


class SemanticMarkdownError(ValueError):
    """A stable, user-facing semantic document error."""

    def __init__(self, code: str, message: str, path: str | None = None):
        self.code = code
        self.path = path
        self.message = message
        super().__init__(message)


class ParsedSemanticDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: SemanticConfig
    body_markdown: str
    semantic_hash: str


__all__ = [
    "AgentConfig",
    "DimensionConfig",
    "GrainConfig",
    "MetricConfig",
    "NamedFilter",
    "ParsedSemanticDocument",
    "MetricConfig",
    "QueryLimits",
    "RagConfig",
    "SemanticConfig",
    "SemanticMarkdownError",
    "TimeConfig",
]
