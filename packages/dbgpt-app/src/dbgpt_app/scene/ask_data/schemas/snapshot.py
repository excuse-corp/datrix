"""Immutable Agent Snapshot protocol models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SnapshotStatus(StrEnum):
    BUILDING = "building"
    READY = "ready"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    INVALID = "invalid"


class SnapshotSourceHashes(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_hash: str = Field(min_length=1)
    schema_hash: str = Field(min_length=1)
    knowledge_hash: str = Field(min_length=1)


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    status: SnapshotStatus
    content_hash: str = Field(min_length=1)
    source_hashes: SnapshotSourceHashes
    routing_projection: dict[str, Any]
    runtime_config: dict[str, Any]
    schema_version: str = "1"
    query_spec_version: str = "1"
    compiler_version: str = "1"
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


__all__ = ["Snapshot", "SnapshotSourceHashes", "SnapshotStatus"]
