"""Database type normalization used by semantic validation."""

from __future__ import annotations


def normalize_type(data_type: str) -> str:
    value = data_type.strip().lower()
    if any(token in value for token in ("char", "text", "clob", "json", "xml")):
        return "string"
    if any(token in value for token in ("bool", "bit")):
        return "boolean"
    if any(token in value for token in ("date", "time", "timestamp")):
        return "datetime" if "time" in value or "timestamp" in value else "date"
    if any(
        token in value
        for token in (
            "int",
            "decimal",
            "numeric",
            "number",
            "real",
            "float",
            "double",
            "money",
        )
    ):
        return "number"
    return "unknown"


def aggregation_compatible(aggregation: str, normalized_type: str) -> bool:
    aggregation = aggregation.lower()
    if aggregation in {"count", "count_distinct", "distinct_count"}:
        return True
    if aggregation in {"sum", "avg", "average"}:
        return normalized_type == "number"
    if aggregation in {"min", "max"}:
        return normalized_type in {"number", "date", "datetime", "string"}
    return False
