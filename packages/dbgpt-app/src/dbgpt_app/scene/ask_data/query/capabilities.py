"""Runtime query capabilities derived from a Scene Snapshot.

Scene Snapshot V2 no longer persists generated dimensions/metrics.  Query
execution builds this contract from the bound schema and, when present, explicit
query hints in the Scene semantic Markdown.  V1 snapshots can still fall back to
their legacy stored query model during the compatibility window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..scene.markdown import SemanticMarkdownParser
from ..schemas.snapshot import Snapshot


@dataclass(frozen=True)
class QueryCapabilities:
    dimensions: list[dict[str, Any]] = field(default_factory=list)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    time: dict[str, Any] = field(default_factory=dict)
    named_filters: list[dict[str, Any]] = field(default_factory=list)
    value_mapping: dict[str, Any] = field(default_factory=dict)
    query_limits: dict[str, Any] = field(default_factory=dict)
    schema: dict[str, Any] = field(default_factory=dict)

    def query_model(self) -> dict[str, Any]:
        return {
            "dimensions": self.dimensions,
            "metrics": self.metrics,
            "time": self.time,
        }


def runtime_dict(snapshot: Snapshot | dict[str, Any]) -> dict[str, Any]:
    if isinstance(snapshot, Snapshot):
        return snapshot.runtime_config
    if isinstance(snapshot, dict) and isinstance(snapshot.get("runtime_config"), dict):
        return snapshot["runtime_config"]
    return snapshot if isinstance(snapshot, dict) else {}


def build_query_capabilities(snapshot: Snapshot | dict[str, Any]) -> QueryCapabilities:
    runtime = runtime_dict(snapshot)
    schema = schema_config(snapshot)
    declared = _declared_from_semantic(runtime)
    legacy = _legacy_query_model(snapshot, runtime)
    schema_dimensions, schema_metrics = _schema_capabilities(schema)

    dimensions = (
        declared.get("dimensions")
        or legacy.get("dimensions")
        or schema_dimensions
    )
    metrics = declared.get("metrics") or legacy.get("metrics") or schema_metrics
    time = declared.get("time") or legacy.get("time") or {}

    return QueryCapabilities(
        dimensions=_dict_items(dimensions),
        metrics=_dict_items(metrics),
        time=time if isinstance(time, dict) else {},
        named_filters=named_filters(snapshot),
        value_mapping=_dict_value(runtime.get("value_mapping", {})),
        query_limits=query_limits(snapshot),
        schema=schema,
    )


def query_model(snapshot: Snapshot | dict[str, Any]) -> dict[str, Any]:
    return build_query_capabilities(snapshot).query_model()


def query_dimensions(snapshot: Snapshot | dict[str, Any]) -> list[dict[str, Any]]:
    return build_query_capabilities(snapshot).dimensions


def query_metrics(snapshot: Snapshot | dict[str, Any]) -> list[dict[str, Any]]:
    return build_query_capabilities(snapshot).metrics


def query_time(snapshot: Snapshot | dict[str, Any]) -> dict[str, Any]:
    return build_query_capabilities(snapshot).time


def named_filters(snapshot: Snapshot | dict[str, Any]) -> list[dict[str, Any]]:
    value = runtime_dict(snapshot).get("named_filters", [])
    return _dict_items(value)


def query_limits(snapshot: Snapshot | dict[str, Any]) -> dict[str, Any]:
    value = runtime_dict(snapshot).get("query_limits", {})
    return value if isinstance(value, dict) else {}


def schema_config(snapshot: Snapshot | dict[str, Any]) -> dict[str, Any]:
    value = runtime_dict(snapshot).get("schema", {})
    return value if isinstance(value, dict) else {}


def metric_definition(
    snapshot: Snapshot | dict[str, Any], key: str
) -> dict[str, Any] | None:
    for item in query_metrics(snapshot):
        if item.get("key") == key:
            return item
    return None


def metric_keys(snapshot: Snapshot | dict[str, Any]) -> set[str]:
    return {str(item["key"]) for item in query_metrics(snapshot) if item.get("key")}


def semantic_keys(snapshot: Snapshot | dict[str, Any]) -> set[str]:
    return {
        str(item["key"])
        for item in [*query_metrics(snapshot), *query_dimensions(snapshot)]
        if item.get("key")
    }


def _declared_from_semantic(runtime: dict[str, Any]) -> dict[str, Any]:
    documents = runtime.get("documents", {})
    semantic_md = (
        documents.get("semantic_md") if isinstance(documents, dict) else None
    )
    if not isinstance(semantic_md, str) or not semantic_md.strip():
        return {}
    try:
        config = SemanticMarkdownParser().parse(semantic_md).config
    except Exception:
        return {}
    return {
        "dimensions": [item.model_dump(mode="json") for item in config.dimensions],
        "metrics": [item.model_dump(mode="json") for item in config.metrics],
        "time": config.time.model_dump(mode="json"),
    }


def _legacy_query_model(
    snapshot: Snapshot | dict[str, Any], runtime: dict[str, Any]
) -> dict[str, Any]:
    if _schema_version(snapshot, runtime) not in {"", "1"}:
        return {}
    model = runtime.get("query_model")
    if isinstance(model, dict):
        return model
    return {
        "dimensions": runtime.get("dimensions", []),
        "metrics": runtime.get("metrics", []),
        "time": runtime.get("time", {}),
    }


def _schema_version(snapshot: Snapshot | dict[str, Any], runtime: dict[str, Any]) -> str:
    if isinstance(snapshot, Snapshot):
        return str(snapshot.schema_version or runtime.get("schema_version") or "")
    if isinstance(snapshot, dict):
        return str(snapshot.get("schema_version") or runtime.get("schema_version") or "")
    return str(runtime.get("schema_version") or "")


def _schema_capabilities(
    schema: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    dimensions: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = [
        {
            "key": "count_rows",
            "name": "记录数",
            "aggregation": "count",
        }
    ]
    columns = schema.get("columns", [])
    if not isinstance(columns, list):
        return dimensions, metrics
    for column in columns:
        if not isinstance(column, dict) or not column.get("name"):
            continue
        normalized_type = str(column.get("normalized_type") or "").lower()
        if normalized_type == "string":
            operators = ["eq", "neq", "in", "like"]
        elif normalized_type in {"number", "date", "datetime"}:
            operators = ["eq", "neq", "in", "gt", "gte", "lt", "lte"]
        else:
            operators = ["eq", "neq", "in"]
        name = str(column["name"])
        label = column.get("comment") or name
        dimensions.append(
            {
                "key": name,
                "field": name,
                "name": label,
                "aliases": [],
                "groupable": True,
                "filter_operators": operators,
            }
        )
        metrics.append(
            {
                "key": f"count_distinct_{name}",
                "name": f"{label}去重数量",
                "field": name,
                "aggregation": "count_distinct",
            }
        )
        if normalized_type == "number":
            for aggregation, suffix in (
                ("sum", "合计"),
                ("avg", "平均值"),
                ("min", "最小值"),
                ("max", "最大值"),
            ):
                metrics.append(
                    {
                        "key": f"{aggregation}_{name}",
                        "name": f"{label}{suffix}",
                        "field": name,
                        "aggregation": aggregation,
                    }
                )
    return dimensions, metrics


def _dict_items(value: Any) -> list[dict[str, Any]]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = [
    "QueryCapabilities",
    "build_query_capabilities",
    "metric_definition",
    "metric_keys",
    "named_filters",
    "query_dimensions",
    "query_limits",
    "query_metrics",
    "query_model",
    "query_time",
    "runtime_dict",
    "schema_config",
    "semantic_keys",
]
