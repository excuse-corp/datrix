"""Private prompt context for one SceneQueryAgent invocation."""

from __future__ import annotations

import json

from ..query.capabilities import build_query_capabilities
from ..schemas.snapshot import Snapshot

_SQL_PREDICATE_MATCHING_GUIDANCE = [
    "## Predicate Matching Guidance",
    "Before writing WHERE predicates, classify each user condition by field "
    "semantics instead of copying the whole question into an equality check.",
    "- Use exact equality, enum matching, or ranges for structured identifiers, "
    "codes, statuses, types, booleans, dates, amounts, quantities, and other "
    "non-text facts.",
    "- For human-entered names, titles, subjects, descriptions, notes, content, "
    "or other natural-language text fields, do not default to equality unless "
    "the user explicitly asks for an exact value or provides a unique identifier. "
    "Prefer keyword or contains matching such as LIKE, ILIKE, or the dialect's "
    "appropriate case-insensitive equivalent.",
    "- Extract the core business keywords from the question. Do not include "
    "query-intent words, metric words, generic category words, or conversational "
    "filler as the matched text value.",
    "- If an exact text predicate is likely too narrow because the user used a "
    "partial name, alias, shorthand, or descriptive phrase, broaden to keyword "
    "matching or a candidate-record query scoped by those keywords.",
    "- Keep broad matching only on suitable text fields; never apply it blindly "
    "to IDs, codes, statuses, dates, numbers, enums, or other structured fields.",
]


def build_scene_agent_context(snapshot: Snapshot, question: str) -> str:
    """Build a scene-private context; this must never enter the main agent prompt."""
    documents = snapshot.runtime_config.get("documents", {})
    dictionary = str(documents.get("data_dictionary_md", "")).strip()
    semantics = str(documents.get("business_semantics_md", "")).strip()
    capabilities = build_query_capabilities(snapshot)
    constraints = {
        "query_model": capabilities.query_model(),
        "named_filters": capabilities.named_filters,
        "value_mapping": capabilities.value_mapping,
        "query_limits": capabilities.query_limits,
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
            *(_SQL_PREDICATE_MATCHING_GUIDANCE),
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
