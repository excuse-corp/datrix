"""Private prompt context for one SceneQueryAgent invocation."""

from __future__ import annotations

import json

from ..schemas.snapshot import Snapshot


def build_scene_agent_context(snapshot: Snapshot, question: str) -> str:
    """Build a scene-private context; this must never enter the main agent prompt."""
    documents = snapshot.runtime_config.get("documents", {})
    dictionary = str(documents.get("data_dictionary_md", "")).strip()
    semantics = str(documents.get("business_semantics_md", "")).strip()
    constraints = {
        key: snapshot.runtime_config.get(key)
        for key in (
            "dimensions",
            "metrics",
            "time",
            "grain",
            "named_filters",
            "value_mapping",
            "query_limits",
        )
    }
    return "\n".join(
        [
            "## SceneQueryAgent Task",
            question.strip(),
            "",
            "## Bound Runtime Constraints",
            json.dumps(constraints, ensure_ascii=False, separators=(",", ":")),
            "",
            "## Full View Data Dictionary",
            dictionary or "（未提供）",
            "",
            "## Full Business Semantic Document",
            semantics or "（未提供）",
            "",
            "The documents are business context only. "
            "Output QuerySpec JSON; never output SQL.",
        ]
    )


def build_scene_sql_context(
    snapshot: Snapshot, question: str, *, dialect: str | None = None
) -> str:
    """Build the private text-to-SQL context for one bound Scene only."""
    runtime = snapshot.runtime_config
    documents = runtime.get("documents", {})
    dictionary = str(documents.get("data_dictionary_md", "")).strip()
    semantics = str(documents.get("business_semantics_md", "")).strip()
    schema = runtime.get("schema", {})
    columns = schema.get("columns") if isinstance(schema, dict) else None
    if not isinstance(columns, list):
        columns = []
    column_lines = []
    for column in columns:
        if not isinstance(column, dict):
            continue
        comment = column.get("comment") or ""
        nullable = "nullable" if column.get("nullable", True) else "not null"
        column_lines.append(
            f"- {column.get('name')} ({column.get('data_type')}, {nullable})"
            + (f": {comment}" if comment else "")
        )
    return "\n".join(
        [
            "## Scene SQL Agent Task",
            question.strip(),
            "",
            "## SQL Contract",
            "Return one read-only SELECT statement for this single Scene.",
            "The SQL must query only the bound table or view below.",
            "Do not add an artificial LIMIT unless the user explicitly asks for one.",
            "Do not return Markdown, explanation, comments, or multiple statements.",
            "",
            "## Bound Object",
            str(runtime.get("view", "")).strip(),
            "",
            "## Dialect",
            (dialect or str(schema.get("dialect") or "") or "sql").strip(),
            "",
            "## Columns",
            "\n".join(column_lines) or "（未提供）",
            "",
            "## Full View Data Dictionary",
            dictionary or "（未提供）",
            "",
            "## Full Business Semantic Document",
            semantics or "（未提供）",
        ]
    )


__all__ = ["build_scene_agent_context", "build_scene_sql_context"]
