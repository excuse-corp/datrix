"""Normalized database view schema models.

These models deliberately do not contain a connector or connection details.  A
schema inspector can populate them from DB-GPT's ConnectorManager while keeping
credentials and unrelated database objects outside the ask-data protocol.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class ViewColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    normalized_type: str = Field(min_length=1)
    nullable: bool = True
    comment: str | None = None


class ViewSchema(BaseModel):
    """Stable, redacted description of one bound view."""

    model_config = ConfigDict(extra="forbid")

    dialect: str = Field(min_length=1)
    data_source: str = Field(min_length=1)
    view: str = Field(min_length=1)
    columns: list[ViewColumn] = Field(min_length=1)
    schema_hash: str = Field(min_length=1)
    inspected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    inspector_version: str = "1"
