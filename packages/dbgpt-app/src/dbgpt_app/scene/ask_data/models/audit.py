"""Audit event contracts for AskData administrative and execution actions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from dbgpt.storage.metadata import DatabaseManager


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(default_factory=lambda: f"aud_{uuid4().hex}")
    action: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    error_code: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> AuditEvent:
        self._events.append(event)
        return event

    def list_events(self, *, resource_id: str | None = None) -> list[AuditEvent]:
        if resource_id is None:
            return list(self._events)
        return [event for event in self._events if event.resource_id == resource_id]


class SqlAuditRepository:
    def __init__(self, db_manager: DatabaseManager):
        from .dao import AskDataAuditDao

        self.dao = AskDataAuditDao(db_manager)

    def append(self, event: AuditEvent) -> AuditEvent:
        return self.dao.save_event(event)


__all__ = ["AuditEvent", "InMemoryAuditRepository", "SqlAuditRepository"]
