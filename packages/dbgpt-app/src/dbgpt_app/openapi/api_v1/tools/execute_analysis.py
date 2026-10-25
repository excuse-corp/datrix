"""execute_analysis tool — quick Excel/CSV analysis via code server."""

import json
from typing import Any, Dict, List

from dbgpt.agent.resource.tool.base import tool


def make_execute_analysis(react_state: Dict[str, Any]):
    @tool(description="Execute quick analysis on uploaded Excel/CSV file.")
    async def execute_analysis() -> str:
        from dbgpt._private.config import Config
        from dbgpt_app.openapi.api_v1.agentic_data_api import get_code_server

        CFG = Config()

        def _is_excel_skill(meta) -> bool:
            name = (meta.name or "").lower()
            desc = (meta.description or "").lower()
            tags = [tag.lower() for tag in (meta.tags or [])]
            return any(
                token in name or token in desc or token in tags
                for token in ["excel", "xlsx", "xls", "spreadsheet"]
            )

        matched = react_state.get("matched")
        file_paths = react_state.get("file_paths") or (
            [react_state["file_path"]] if react_state.get("file_path") else []
        )
        if not file_paths:
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": "No file to analyze"}]},
                ensure_ascii=False,
            )
        if matched and not _is_excel_skill(matched.metadata):
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": "Selected skill is not for Excel analysis",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        code_server = await get_code_server(CFG.SYSTEM_APP)
        analysis_code = """
import json
import os
import pandas as pd

file_paths = {file_paths}
summaries = []
for file_path in file_paths:
    extension = os.path.splitext(file_path)[1].lower()
    if extension in (".xls", ".xlsx"):
        df = pd.read_excel(file_path)
    elif extension == ".tsv":
        df = pd.read_csv(file_path, sep="\\t")
    elif extension == ".csv":
        df = pd.read_csv(file_path)
    else:
        summaries.append({{"file_name": os.path.basename(file_path), "error": "Only Excel, CSV, and TSV files are supported by quick analysis."}})
        continue
    summaries.append({{
        "file_name": os.path.basename(file_path),
        "shape": list(df.shape),
        "columns": list(df.columns),
        "dtypes": {{col: str(dtype) for col, dtype in df.dtypes.items()}},
        "head": df.head(5).to_dict(orient="records"),
    }})
print(json.dumps(summaries, ensure_ascii=False))
""".format(file_paths=json.dumps(file_paths))
        result = await code_server.exec(analysis_code, "python")
        output_text = (
            result.output.decode("utf-8") if isinstance(result.output, bytes) else ""
        )
        chunks: List[Dict[str, Any]] = [
            {"output_type": "code", "content": analysis_code.strip()}
        ]
        if output_text:
            try:
                summaries = json.loads(output_text)
                if isinstance(summaries, dict):
                    summaries = [summaries]
                for summary in summaries:
                    chunks.append({"output_type": "json", "content": summary})
                    head_rows = summary.get("head")
                    columns = summary.get("columns")
                    if isinstance(head_rows, list) and isinstance(columns, list):
                        chunks.append(
                            {
                                "output_type": "table",
                                "content": {
                                    "file_name": summary.get("file_name"),
                                    "columns": [
                                        {"title": col, "dataIndex": col, "key": col}
                                        for col in columns
                                    ],
                                    "rows": head_rows,
                                },
                            }
                        )
                    numeric_columns = [
                        col
                        for col, dtype in (summary.get("dtypes") or {}).items()
                        if "int" in dtype or "float" in dtype
                    ]
                    if numeric_columns and isinstance(head_rows, list):
                        series_col = numeric_columns[0]
                        data = [
                            {"x": idx + 1, "y": row.get(series_col)}
                            for idx, row in enumerate(head_rows)
                            if row.get(series_col) is not None
                        ]
                        if data:
                            chunks.append(
                                {
                                    "output_type": "chart",
                                    "content": {
                                        "title": summary.get("file_name"),
                                        "data": data,
                                        "xField": "x",
                                        "yField": "y",
                                    },
                                }
                            )
            except Exception:
                chunks.append({"output_type": "text", "content": output_text})
        return json.dumps({"chunks": chunks}, ensure_ascii=False)

    return execute_analysis
