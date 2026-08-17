"""Single-attempt, business-field-only QuerySpec repair."""

from __future__ import annotations

from typing import Any

from .query_spec import QuerySpec


class QuerySpecRepairError(ValueError):
    pass


class QuerySpecRepair:
    allowed_fields = frozenset(
        {
            "dimensions",
            "metrics",
            "time_grain",
            "time_range",
            "named_filters",
            "filters",
            "order_by",
            "limit",
        }
    )
    forbidden_fields = frozenset(
        {
            "sql",
            "ast",
            "view",
            "table",
            "schema",
            "formula",
            "parameters",
            "data_source",
            "permissions",
        }
    )

    def repair(
        self, spec: QuerySpec, patch: dict[str, Any], *, attempt: int
    ) -> QuerySpec:
        if attempt >= 1:
            raise QuerySpecRepairError("QUERY_SPEC_REPAIR_LIMIT")
        unknown = set(patch) - self.allowed_fields
        forbidden = set(patch) & self.forbidden_fields
        if forbidden:
            raise QuerySpecRepairError("QUERY_SPEC_REPAIR_FORBIDDEN_FIELD")
        if unknown:
            raise QuerySpecRepairError("QUERY_SPEC_REPAIR_UNKNOWN_FIELD")
        try:
            return QuerySpec.model_validate({**spec.model_dump(mode="json"), **patch})
        except ValueError as exc:
            raise QuerySpecRepairError("QUERY_SPEC_REPAIR_INVALID") from exc


__all__ = ["QuerySpecRepair", "QuerySpecRepairError"]
