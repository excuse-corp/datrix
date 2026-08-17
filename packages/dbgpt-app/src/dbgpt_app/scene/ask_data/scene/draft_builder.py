"""Build an explicitly untrusted semantic Markdown draft from a view schema."""

from __future__ import annotations

import json

from ..schemas.schema import ViewSchema


class SemanticDraftBuilder:
    """Generate a reviewable skeleton; never marks it ready or active."""

    def build(self, scene_id: str, name: str, schema: ViewSchema) -> str:
        def quote(value: str) -> str:
            return json.dumps(str(value), ensure_ascii=False)

        lines = [
            "---",
            'schema_version: "1"',
            f"scene_id: {quote(scene_id)}",
            f"name: {quote(name)}",
            'description: "TODO: confirm business scope"',
            "keywords: []",
            f"data_source: {quote(schema.data_source)}",
            f"view: {quote(schema.view)}",
            "agent:",
            '  role: "TODO: confirm analyst role"',
            "  capabilities: []",
            "  cannot_do: []",
            "grain:",
            '  description: "TODO: confirm row grain"',
            "  key_fields: []",
            "dimensions: []",
            "metrics: []",
            "named_filters: []",
            "# Candidate fields from the inspected view (all require confirmation):",
        ]
        for column in schema.columns:
            lines.append(f"# - {column.name} ({column.normalized_type})")
        lines.extend(
            [
                "query:",
                "  max_rows: 1000",
                "  timeout_seconds: 30",
                "  allow_detail: false",
                "  max_spec_attempts: 2",
                "rag:",
                "  required_on_activate: true",
                "  top_k: 4",
                "  include_documents: [semantic_markdown]",
                "---",
                "",
                "# TODO: confirm semantic definitions",
            ]
        )
        return "\n".join(lines) + "\n"
