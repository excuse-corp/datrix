"""Bounded read-only execution for compiled scene queries."""

from __future__ import annotations

from time import monotonic
from typing import Any

from sqlalchemy import text

from ..schemas.snapshot import Snapshot
from .capabilities import query_limits
from .ast_validator import QueryAstValidator
from .compiler import CompiledQuery


class SafeSceneQueryExecutor:
    def __init__(self, ast_validator: QueryAstValidator | None = None):
        self.ast_validator = ast_validator or QueryAstValidator()

    def execute(
        self,
        engine: Any,
        compiled: CompiledQuery,
        snapshot: Snapshot,
        *,
        enforce_row_limit: bool = True,
    ) -> tuple[list[str], list[dict[str, Any]], bool, int]:
        dialect_name = engine.dialect.name.lower()
        self.ast_validator.validate(compiled.sql, snapshot, dialect=dialect_name)
        started = monotonic()
        limits = query_limits(snapshot)
        raw_max_rows = limits.get("max_rows", 1000) if enforce_row_limit else None
        max_rows = (
            int(raw_max_rows)
            if isinstance(raw_max_rows, (int, float)) and raw_max_rows > 0
            else None
        )
        max_columns = limits.get("max_columns", 100)
        max_cell_bytes = limits.get("max_cell_bytes", 16384)
        max_result_bytes = limits.get("max_result_bytes", 4 * 1024 * 1024)
        timeout_seconds = limits.get("timeout_seconds")
        if dialect_name not in {"mssql", "sqlite", "postgres", "postgresql"}:
            raise ValueError("UNSUPPORTED_SQL_DIALECT")
        with engine.connect() as connection:
            options = {"stream_results": True}
            if timeout_seconds:
                options["timeout"] = timeout_seconds
            result = connection.execution_options(**options).execute(
                text(compiled.sql), compiled.parameters
            )
            all_keys = list(result.keys())
            keys = all_keys[:max_columns]
            column_truncated = len(all_keys) > max_columns
            rows = []
            result_bytes = 0
            cell_truncated = False
            row_truncated = False
            byte_truncated = False
            for row in result.mappings():
                current = {}
                for key in keys:
                    value, was_truncated = self._bound_value(
                        row.get(key), max_cell_bytes
                    )
                    current[key] = value
                    cell_truncated = cell_truncated or was_truncated
                result_bytes += sum(
                    len(str(value).encode("utf-8")) for value in current.values()
                )
                if max_rows is not None and len(rows) >= max_rows:
                    row_truncated = True
                    break
                if result_bytes > max_result_bytes:
                    byte_truncated = True
                    break
                rows.append(current)
        truncated = (
            column_truncated
            or cell_truncated
            or row_truncated
            or byte_truncated
        )
        if truncated and max_rows is not None:
            rows = rows[:max_rows]
        return keys, rows, truncated, int((monotonic() - started) * 1000)

    @staticmethod
    def _bound_value(value: Any, max_cell_bytes: int) -> tuple[Any, bool]:
        if value is None:
            return None, False
        if isinstance(value, str):
            encoded = value.encode("utf-8")
            if len(encoded) > max_cell_bytes:
                return (
                    encoded[:max_cell_bytes].decode("utf-8", errors="ignore"),
                    True,
                )
        return value, False


__all__ = ["SafeSceneQueryExecutor"]
