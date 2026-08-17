"""Validate QuerySpec exclusively against an active Snapshot."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..schemas.snapshot import Snapshot
from .capabilities import build_query_capabilities
from .query_spec import QuerySpec


class QuerySpecIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    path: str
    message: str


class QuerySpecValidationError(ValueError):
    def __init__(self, issues: list[QuerySpecIssue]):
        self.issues = issues
        super().__init__(issues[0].message if issues else "QuerySpec validation failed")


class QuerySpecValidator:
    def validate(self, spec: QuerySpec, snapshot: Snapshot) -> None:
        capabilities = build_query_capabilities(snapshot)
        dimensions = {item["key"]: item for item in capabilities.dimensions}
        metrics = {item["key"]: item for item in capabilities.metrics}
        filters = {item["key"]: item for item in capabilities.named_filters}
        issues: list[QuerySpecIssue] = []

        def issue(code: str, path: str, message: str) -> None:
            issues.append(QuerySpecIssue(code=code, path=path, message=message))

        if snapshot.status.value != "active":
            issue("SCENE_NOT_ACTIVE", "snapshot_id", "Snapshot is not active")
        if spec.action != "query":
            issue(
                "QUERY_SPEC_VALIDATION_FAILED",
                "action",
                "Only query action is supported",
            )
        for index, key in enumerate(spec.dimensions):
            if key not in dimensions:
                issue(
                    "DIMENSION_NOT_ALLOWED",
                    f"dimensions[{index}]",
                    f"Unknown dimension: {key}",
                )
        for index, key in enumerate(spec.metrics):
            if key not in metrics:
                issue(
                    "METRIC_NOT_ALLOWED", f"metrics[{index}]", f"Unknown metric: {key}"
                )
        time_config = capabilities.time
        if spec.time_grain and spec.time_grain not in time_config.get(
            "granularities", []
        ):
            issue(
                "TIME_GRAIN_NOT_ALLOWED",
                "time_grain",
                f"Unknown time grain: {spec.time_grain}",
            )
        if time_config.get("required") and spec.time_range is None:
            issue("TIME_RANGE_REQUIRED", "time_range", "A time range is required")
        for index, key in enumerate(spec.named_filters):
            if key not in filters:
                issue(
                    "FILTER_NOT_ALLOWED",
                    f"named_filters[{index}]",
                    f"Unknown named filter: {key}",
                )
        for index, item in enumerate(spec.filters):
            dimension = dimensions.get(item.dimension)
            if not dimension:
                issue(
                    "DIMENSION_NOT_ALLOWED",
                    f"filters[{index}].dimension",
                    f"Unknown dimension: {item.dimension}",
                )
            elif item.operator not in dimension.get("filter_operators", []):
                issue(
                    "FILTER_NOT_ALLOWED",
                    f"filters[{index}].operator",
                    "Filter operator is not allowed",
                )
        selected = set(spec.dimensions) | set(spec.metrics)
        for index, item in enumerate(spec.order_by):
            if item.key not in selected:
                issue(
                    "ORDER_KEY_NOT_ALLOWED",
                    f"order_by[{index}].key",
                    "Order key must be selected",
                )
            if item.direction.lower() not in {"asc", "desc"}:
                issue(
                    "ORDER_DIRECTION_INVALID",
                    f"order_by[{index}].direction",
                    "Direction must be asc or desc",
                )
        max_rows = capabilities.query_limits.get("max_rows", 1000)
        if spec.limit > max_rows:
            issue("LIMIT_EXCEEDED", "limit", f"Limit cannot exceed {max_rows}")
        if issues:
            raise QuerySpecValidationError(issues)


__all__ = ["QuerySpecIssue", "QuerySpecValidationError", "QuerySpecValidator"]
