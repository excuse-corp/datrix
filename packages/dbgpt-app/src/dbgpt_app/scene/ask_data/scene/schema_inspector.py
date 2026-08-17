"""Read and normalize one bound table/view without exposing database credentials."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import inspect

from ..schemas.schema import ViewColumn, ViewSchema
from .schema_types import normalize_type


class SceneSchemaInspector:
    """SQLAlchemy-backed inspector for a single configured table or view.

    DB-GPT's ConnectorManager remains responsible for resolving a data source to
    an engine.  This class accepts that engine explicitly so it cannot enumerate
    arbitrary data sources or retain connection configuration.
    """

    inspector_version = "1"

    def inspect_engine(self, engine: Any, data_source: str, view: str) -> ViewSchema:
        if not data_source or not view:
            raise ValueError("data_source and view are required")
        schema, view_name = self._split_view(view)
        db_inspector = inspect(engine)
        available_views = set(db_inspector.get_view_names(schema=schema))
        available_tables = set(db_inspector.get_table_names(schema=schema))
        if view_name not in available_views and view_name not in available_tables:
            raise ValueError(f"Configured object is not a table or view: {view}")

        columns: list[ViewColumn] = []
        for column in db_inspector.get_columns(view_name, schema=schema):
            data_type = str(column.get("type", "unknown"))
            columns.append(
                ViewColumn(
                    name=str(column["name"]),
                    data_type=data_type,
                    normalized_type=normalize_type(data_type),
                    nullable=bool(column.get("nullable", True)),
                    comment=column.get("comment"),
                )
            )
        if not columns:
            raise ValueError(f"View has no readable columns: {view}")

        columns.sort(key=lambda item: item.name)
        canonical = [column.model_dump(mode="json") for column in columns]
        digest = hashlib.sha256(
            json.dumps(
                canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        return ViewSchema(
            dialect=engine.dialect.name,
            data_source=data_source,
            view=view,
            columns=columns,
            schema_hash=f"sha256:{digest}",
            inspected_at=datetime.now(timezone.utc),
            inspector_version=self.inspector_version,
        )

    @staticmethod
    def _split_view(view: str) -> tuple[str | None, str]:
        parts = [part for part in view.split(".") if part]
        if len(parts) == 1:
            return None, parts[0]
        if len(parts) == 2:
            return parts[0], parts[1]
        raise ValueError("View must be formatted as view or schema.view")
