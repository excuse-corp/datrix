"""Persistent-domain contracts for Scene and SceneRevision."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SceneStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"
    INVALID = "invalid"


class RevisionStatus(StrEnum):
    DRAFT = "draft"
    VALIDATING = "validating"
    READY = "ready"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class Scene(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scene_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    status: SceneStatus = SceneStatus.DRAFT
    latest_revision: int = Field(default=0, ge=0)
    active_revision: int | None = Field(default=None, ge=1)
    current_snapshot_id: str | None = None
    created_by: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    soft_deleted_at: datetime | None = None


class SceneRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scene_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    status: RevisionStatus = RevisionStatus.DRAFT
    data_source_name: str = Field(min_length=1)
    view_name: str = Field(min_length=1)
    semantic_md: str = Field(min_length=1)
    parsed_config_json: dict[str, Any] | None = None
    view_schema_json: dict[str, Any] | None = None
    knowledge_space_name: str | None = None
    semantic_hash: str | None = None
    schema_hash: str | None = None
    knowledge_hash: str | None = None
    last_validated_at: datetime | None = None
    created_by: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    error_code: str | None = None
    error_message: str | None = None


__all__ = ["RevisionStatus", "Scene", "SceneRevision", "SceneStatus"]
