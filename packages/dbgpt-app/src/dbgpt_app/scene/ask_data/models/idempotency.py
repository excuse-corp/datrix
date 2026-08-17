"""Durable idempotency records for AskData management operations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dbgpt.storage.metadata import DatabaseManager


class IdempotencyRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: str = Field(min_length=1)
    key: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    response: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(hours=24)
    )


class IdempotencyConflictError(RuntimeError):
    """Raised when one idempotency key is reused with a different payload."""


class InMemoryIdempotencyRepository:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], IdempotencyRecord] = {}

    def get(self, scope: str, key: str) -> IdempotencyRecord | None:
        record = self._records.get((scope, key))
        if record and _as_utc(record.expires_at) <= datetime.now(timezone.utc):
            self._records.pop((scope, key), None)
            return None
        return record

    def save(self, record: IdempotencyRecord) -> IdempotencyRecord:
        existing = self.get(record.scope, record.key)
        if existing and existing.fingerprint != record.fingerprint:
            raise IdempotencyConflictError("IDEMPOTENCY_KEY_CONFLICT")
        self._records[(record.scope, record.key)] = record
        return record


class SqlIdempotencyRepository:
    def __init__(self, db_manager: DatabaseManager):
        from .dao import AskDataIdempotencyDao

        self.dao = AskDataIdempotencyDao(db_manager)

    def get(self, scope: str, key: str) -> IdempotencyRecord | None:
        return self.dao.get(scope, key)

    def save(self, record: IdempotencyRecord) -> IdempotencyRecord:
        return self.dao.save(record)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "IdempotencyConflictError",
    "IdempotencyRecord",
    "InMemoryIdempotencyRepository",
    "SqlIdempotencyRepository",
]
