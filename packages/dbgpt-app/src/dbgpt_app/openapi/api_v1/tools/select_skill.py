"""select_skill tool — matches a skill from registry based on user query."""

import json
from pathlib import Path
from typing import Any, Dict

from dbgpt.agent.resource.tool.base import tool
from dbgpt.configs.model_config import SKILLS_DIR
from dbgpt_app.openapi.api_v1.skill_state import skill_enabled


def make_select_skill(react_state: Dict[str, Any], registry: Any):
    """Return a ``select_skill`` FunctionTool bound to the given react_state."""

    @tool(
        description="Select the most relevant skill based on user query from the "
        "available skills list in system prompt."
    )
    def select_skill(query: str) -> str:
        def _is_excel_skill(meta) -> bool:
            name = (meta.name or "").lower()
            desc = (meta.description or "").lower()
            tags = [tag.lower() for tag in (meta.tags or [])]
            return any(
                token in name or token in desc or token in tags
                for token in ["excel", "xlsx", "xls", "spreadsheet"]
            )

        def _mentions_excel(text: str) -> bool:
            t = (text or "").lower()
            return any(
                kw in t for kw in ["excel", "xlsx", "xls", "spreadsheet", "表格"]
            )

        match_input = query or ""
        if react_state.get("file_path"):
            match_input = f"{match_input} excel xlsx spreadsheet file"
        matched = registry.match_skill(match_input)
        if (
            matched
            and _is_excel_skill(matched.metadata)
            and not (_mentions_excel(query) or react_state.get("file_path"))
        ):
            matched = None
        if matched:
            file_path = getattr(matched.metadata, "file_path", None)
            try:
                path = Path(file_path).expanduser() if file_path else None
                if path and not path.is_absolute():
                    path = Path(SKILLS_DIR).expanduser().resolve() / path
                if path and not path.is_file():
                    matched = None
                elif file_path and not skill_enabled(file_path):
                    matched = None
            except Exception:
                pass
        react_state["matched"] = matched
        if matched:
            detail = (
                f"Matched: {matched.metadata.name} - {matched.metadata.description}"
            )
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": detail}]},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "chunks": [
                    {
                        "output_type": "text",
                        "content": "No skill matched; proceed without skill",
                    }
                ]
            },
            ensure_ascii=False,
        )

    return select_skill
