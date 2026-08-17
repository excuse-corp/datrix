"""QueryRun and AgentRun audit contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL_SUCCEEDED = "partial_succeeded"
    FAILED = "failed"
    CLARIFICATION_REQUIRED = "clarification_required"
    REJECTED = "rejected"


class QueryRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    conversation_id: str | None = None
    user_id: str = Field(min_length=1)
    entry_type: str = Field(default="main_agent", pattern="^(main_agent|scene_api)$")
    question: str = Field(min_length=1)
    status: RunStatus = RunStatus.PENDING
    plan_json: dict[str, Any] | None = None
    result_json: dict[str, Any] | None = None
    combine_mode: str | None = None
    # The immutable global business context used to create this plan.  Keeping
    # it outside plan_json makes operational tracing and retention queries safe.
    ontology_snapshot_id: str | None = None
    snapshot_ids: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    clarification: dict[str, Any] | None = None
    clarification_round: int = Field(default=0, ge=0, le=2)
    clarification_expires_at: datetime | None = None
    idempotency_key: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def clarification_expired(self) -> bool:
        if self.clarification_expires_at is None:
            return False
        expires_at = self.clarification_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= expires_at


class AgentRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_run_id: str = Field(min_length=1)
    query_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    knowledge_space_name: str | None = None
    query_spec_hash: str | None = None
    compiler_version: str | None = None
    sql_hash: str | None = None
    status: RunStatus = RunStatus.PENDING
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


__all__ = ["AgentRun", "QueryRun", "RunStatus"]
