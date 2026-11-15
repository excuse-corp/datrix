"""Runtime query capabilities derived from a Scene Snapshot.

Scene Snapshot V2 no longer persists generated dimensions/metrics.  Query
execution builds this contract from the bound schema and, when present, explicit
query hints in the Scene semantic Markdown.  V1 snapshots can still fall back to
their legacy stored query model during the compatibility window.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..scene.markdown import SemanticMarkdownParser, split_scene_documents
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
        declared.get("dimensions") or legacy.get("dimensions") or schema_dimensions
    )
    metrics = declared.get("metrics") or legacy.get("metrics") or schema_metrics
    time = declared.get("time") or legacy.get("time") or {}
    dictionary = _data_dictionary_metadata(runtime)

    return QueryCapabilities(
        dimensions=_enrich_with_dictionary(_dict_items(dimensions), dictionary),
        metrics=_enrich_with_dictionary(_dict_items(metrics), dictionary),
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
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if key != "allow_detail"}


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
    semantic_md = documents.get("semantic_md") if isinstance(documents, dict) else None
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


def _data_dictionary_metadata(runtime: dict[str, Any]) -> dict[str, dict[str, str]]:
    documents = runtime.get("documents", {})
    if not isinstance(documents, dict):
        return {}
    markdown = documents.get("data_dictionary_md")
    if not isinstance(markdown, str) or not markdown.strip():
        semantic_md = documents.get("semantic_md")
        if not isinstance(semantic_md, str) or not semantic_md.strip():
            return {}
        try:
            body = SemanticMarkdownParser().parse(semantic_md).body_markdown
        except Exception:
            return {}
        markdown = split_scene_documents(body).get("data_dictionary_md", "")
    if not isinstance(markdown, str) or not markdown.strip():
        return {}

    def normalize(value: str) -> str:
        return re.sub(r"[\s_（）()：:.-]", "", value).lower()

    def split_row(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    aliases = {
        "field": {
            "field",
            "字段",
            "字段名",
            "目标字段",
            "物理字段",
            "column",
            "columnname",
        },
        "semantic_key": {"semantickey", "语义键", "指标语义键"},
        "name": {"name", "名称", "业务名称", "中文名", "中文字段", "含义", "业务含义"},
        "unit": {"unit", "单位", "计量单位"},
        "aggregation": {"aggregation", "聚合", "聚合方式", "计算口径"},
        "grain": {"grain", "粒度", "统计粒度"},
        "time_basis": {"timebasis", "时间依据", "统计时间依据"},
    }
    normalized_aliases = {
        key: {normalize(alias) for alias in values} for key, values in aliases.items()
    }
    lines = [line.strip() for line in markdown.replace("\r\n", "\n").split("\n")]
    result: dict[str, dict[str, str]] = {}
    index = 0
    while index + 1 < len(lines):
        if not lines[index].startswith("|") or not lines[index + 1].startswith("|"):
            index += 1
            continue
        headers = [normalize(cell) for cell in split_row(lines[index])]
        divider = split_row(lines[index + 1])
        if not headers or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in divider):
            index += 1
            continue
        mapped_headers: dict[str, int] = {}
        for column, header in enumerate(headers):
            for key, values in normalized_aliases.items():
                if header in values:
                    mapped_headers[key] = column
                    break
        field_index = mapped_headers.get("field")
        if field_index is None:
            index += 2
            continue
        row_index = index + 2
        while row_index < len(lines) and lines[row_index].startswith("|"):
            cells = split_row(lines[row_index])
            if len(cells) == len(headers):
                field_name = cells[field_index].strip("` ")
                if field_name:
                    result[field_name] = {
                        key: cells[column].strip("` ")
                        for key, column in mapped_headers.items()
                        if column < len(cells) and cells[column].strip()
                    }
            row_index += 1
        index = row_index
    return result


def _enrich_with_dictionary(
    items: list[dict[str, Any]], dictionary: dict[str, dict[str, str]]
) -> list[dict[str, Any]]:
    enriched = []
    for item in items:
        value = dict(item)
        field_name = str(value.get("field") or value.get("key") or "")
        metadata = dictionary.get(field_name, {})
        if metadata:
            if metadata.get("semantic_key") and not value.get("semantic_key"):
                value["semantic_key"] = metadata["semantic_key"]
            if metadata.get("name") and (
                not value.get("name") or value.get("name") == value.get("key")
            ):
                value["name"] = metadata["name"]
            for key in ("unit", "aggregation", "grain", "time_basis"):
                if metadata.get(key) and not value.get(key):
                    value[key] = metadata[key]
        enriched.append(value)
    return enriched


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


def _schema_version(
    snapshot: Snapshot | dict[str, Any], runtime: dict[str, Any]
) -> str:
    if isinstance(snapshot, Snapshot):
        return str(snapshot.schema_version or runtime.get("schema_version") or "")
    if isinstance(snapshot, dict):
        return str(
            snapshot.get("schema_version") or runtime.get("schema_version") or ""
        )
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
