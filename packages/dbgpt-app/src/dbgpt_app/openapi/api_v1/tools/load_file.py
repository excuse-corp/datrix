"""load_file tool — returns the uploaded file path from react_state."""

import json
from typing import Any, Dict

from dbgpt.agent.resource.tool.base import tool


def make_load_file(react_state: Dict[str, Any]):
    @tool(description="Load uploaded file info if provided.")
    def load_file() -> str:
        file_paths = react_state.get("file_paths") or (
            [react_state["file_path"]] if react_state.get("file_path") else []
        )
        if not file_paths:
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": "No file uploaded"}]},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "chunks": [
                    {"output_type": "text", "content": path}
                    for path in file_paths
                ]
                + [
                    {
                        "output_type": "text",
                        "content": "File paths provided by user upload",
                    },
                ]
            },
            ensure_ascii=False,
        )

    return load_file
