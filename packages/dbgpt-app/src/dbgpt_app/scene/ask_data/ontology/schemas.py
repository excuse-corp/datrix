"""Typed, non-executable Ontology graph contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class OntologyRevisionStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class OntologyNode(BaseModel):
    """One domain concept. IDs are stable and never contain physical SQL names."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    type: Literal[
        "entity",
        "metric",
        "scene",
        "rule",
        "analysis_dimension",
        "analysis_path",
        "analysis_rule",
    ]
    name: str = Field(min_length=1, max_length=255)
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    provenance: list[dict[str, str]] = Field(default_factory=list)


class OntologyEdge(BaseModel):
    """A typed business relation, never an executable join or SQL expression."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)
    type: Literal[
        "relation",
        "belongs_to",
        "provided_by",
        "scene_binding",
        "cross_scene_join",
        "clarification_rule",
        "metric_relation",
    ]
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    provenance: list[dict[str, str]] = Field(default_factory=list)


class OntologyGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[OntologyNode] = Field(default_factory=list)
    edges: list[OntologyEdge] = Field(default_factory=list)


class OntologyValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    line: int | None = None
    path: str | None = None


class OntologyCompilation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph: OntologyGraph
    issues: list[OntologyValidationIssue] = Field(default_factory=list)
    prompt_projection: dict[str, Any] = Field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.issues


class OntologyRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ontology_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    status: OntologyRevisionStatus = OntologyRevisionStatus.DRAFT
    ontology_md: str = Field(min_length=1)
    graph_json: dict[str, Any] | None = None
    prompt_projection_json: dict[str, Any] | None = None
    source_scene_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)
    content_hash: str | None = None
    created_by: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OntologySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str = Field(min_length=1, max_length=128)
    ontology_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    status: Literal["ready", "active"] = "ready"
    content_hash: str = Field(min_length=1)
    graph_json: dict[str, Any]
    prompt_projection_json: dict[str, Any]
    source_scene_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    compiler_version: str = "1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    activated_at: datetime | None = None


__all__ = [
    "OntologyCompilation",
    "OntologyEdge",
    "OntologyGraph",
    "OntologyNode",
    "OntologyRevision",
    "OntologyRevisionStatus",
    "OntologySnapshot",
    "OntologyValidationIssue",
]
