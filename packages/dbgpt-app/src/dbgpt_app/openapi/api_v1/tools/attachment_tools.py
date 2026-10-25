"""ReAct tools for structured attachment parsing and retrieval."""

from __future__ import annotations

import json
import csv
import io
from pathlib import Path
from typing import Any, Dict, Iterable, List

from dbgpt.agent.resource.tool.base import tool
from dbgpt_app.attachment import (
    AttachmentParseResult,
    AttachmentParseService,
    get_attachment_chunk_store,
)
from dbgpt_app.attachment.service import AttachmentPathError


def make_attachment_tools(react_state: Dict[str, Any]) -> List[Any]:
    service = AttachmentParseService(
        cache_namespace=str(react_state.get("user_id") or "default")
    )

    def _paths() -> List[str]:
        paths = react_state.get("file_paths") or []
        if not paths and react_state.get("file_path"):
            paths = [react_state["file_path"]]
        return [str(path) for path in paths if path]

    def _records() -> List[Dict[str, Any]]:
        records = []
        for item in react_state.get("session_state", {}).get("attachments", []):
            if isinstance(item, dict):
                records.append(item)
        for item in react_state.get("attachments", []) or []:
            if isinstance(item, dict) and item not in records:
                records.append(item)
        return records

    def _allowed_roots(paths: Iterable[str]) -> List[Path]:
        user_id = str(react_state.get("user_id") or "")
        if not user_id:
            raise AttachmentPathError("Authenticated user scope is unavailable")
        from dbgpt_app.openapi.api_v1.python_upload_api import _user_upload_dir

        return [Path(_user_upload_dir(user_id)).resolve()]

    def _result_payload(result: AttachmentParseResult, *, include_text: bool = False):
        data = result.to_dict(include_text=include_text)
        if not include_text:
            data.pop("blocks", None)
            data["block_count"] = len(result.blocks)
            data["text_chars"] = len(result.plain_text)
        return {"chunks": [{"output_type": "json", "content": data}]}

    def _record_attachment_id(path: str) -> str | None:
        requested_path = Path(path).expanduser().resolve(strict=False)
        for item in _records():
            record_path = (
                item.get("storage_path")
                or item.get("absolute_path")
                or item.get("path")
            )
            if not record_path:
                continue
            try:
                if Path(str(record_path)).expanduser().resolve(strict=False) == requested_path:
                    return str(item.get("attachment_id") or "") or None
            except (OSError, ValueError):
                continue
        return None

    def _parse_path(path: str) -> AttachmentParseResult:
        result = service.parse_file(
            path,
            attachment_id=_record_attachment_id(path),
            allowed_roots=_allowed_roots([path]),
        )
        try:
            get_attachment_chunk_store().replace_result(
                str(react_state.get("user_id") or "default"), result
            )
        except Exception as exc:
            if result.status in {"completed", "partial"}:
                result.warnings.append(
                    f"Search index could not be updated: {type(exc).__name__}"
                )
        return result

    def _select_paths(file_path: str = "", attachment_id: str = "") -> List[str]:
        available = _paths()
        if attachment_id:
            for item in _records():
                if item.get("attachment_id") == attachment_id:
                    requested_path = item.get("storage_path") or item.get("absolute_path")
                    return [str(requested_path)] if requested_path else []
            return []
        if not file_path:
            return available
        requested = str(Path(file_path).expanduser())
        requested_resolved = Path(requested).resolve(strict=False)
        selected = []
        for candidate in available:
            if (
                candidate == file_path
                or Path(candidate).resolve(strict=False) == requested_resolved
            ):
                selected.append(candidate)
        return selected

    @tool(
        description=(
            "List current uploaded attachments and parse status. Parameters: none."
        )
    )
    def list_attachments() -> str:
        items = []
        state_items = [
            item
            for item in react_state.get("session_state", {}).get("attachments", [])
            if isinstance(item, dict)
        ]
        for path in _paths():
            try:
                _allowed_roots([path])
            except AttachmentPathError:
                continue
            state_item = next(
                (
                    item
                    for item in state_items
                    if item.get("absolute_path") == path
                    or item.get("path") == path
                    or item.get("filename") == Path(path).name
                ),
                {},
            )
            items.append(
                {
                    "attachment_id": state_item.get("attachment_id"),
                    "filename": Path(path).name,
                    "exists": Path(path).is_file(),
                    "format": state_item.get("format"),
                    "parse_status": state_item.get("parse_status"),
                    "parser": state_item.get("parser"),
                    "warnings": state_item.get("warnings", []),
                }
            )
        return json.dumps(
            {"chunks": [{"output_type": "json", "content": items}]},
            ensure_ascii=False,
        )

    @tool(
        description=(
            "Parse an uploaded document into structured blocks. Optional parameter: "
            '{"attachment_id": "stable ID", "file_path": "legacy uploaded path"}.'
        )
    )
    def parse_attachment(file_path: str = "", attachment_id: str = "") -> str:
        selected = _select_paths(file_path, attachment_id)
        if not selected:
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": "No attachment found"}]},
                ensure_ascii=False,
            )
        payloads = []
        for path in selected:
            try:
                payloads.append(
                    _result_payload(_parse_path(path))["chunks"][0]["content"]
                )
            except AttachmentPathError as exc:
                payloads.append(
                    {
                        "filename": Path(path).name,
                        "status": "failed",
                        "error_code": "ATTACHMENT_PATH_NOT_ALLOWED",
                        "error_message": str(exc),
                    }
                )
        return json.dumps(
            {"chunks": [{"output_type": "json", "content": payloads}]},
            ensure_ascii=False,
        )

    @tool(
        description=(
            "Search parsed attachment chunks using BM25 keyword ranking. Parameters: "
            '{"query": "keywords", "file_path": "optional legacy path", '
            '"attachment_id": "optional stable ID", "top_k": 6, '
            '"include_tables": true}.'
        )
    )
    def search_attachment(
        query: str,
        file_path: str = "",
        attachment_id: str = "",
        top_k: int = 6,
        include_tables: bool = True,
    ) -> str:
        if not (query or "").strip():
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": "Query is empty"}]},
                ensure_ascii=False,
            )
        attachment_ids = []
        for path in _select_paths(file_path, attachment_id):
            try:
                result = _parse_path(path)
            except AttachmentPathError:
                continue
            attachment_ids.append(result.attachment_id)
        matches = get_attachment_chunk_store().search(
            str(react_state.get("user_id") or "default"),
            query,
            attachment_ids=attachment_ids,
            top_k=top_k,
            include_tables=include_tables,
        )
        return json.dumps(
            {"chunks": [{"output_type": "json", "content": matches}]},
            ensure_ascii=False,
        )

    @tool(
        description=(
            "Read selected attachment blocks. Parameters: "
            '{"file_path": "optional path", "page": 1, "slide": 1, '
            '"paragraph": 1, "line_start": 1, "line_end": 20, "table": 1, '
            '"sheet": "optional worksheet name", "row_start": 1, '
            '"row_end": 20, "block_type": "optional type"}.'
        )
    )
    def read_attachment_region(
        file_path: str = "",
        attachment_id: str = "",
        page: int | None = None,
        slide: int | None = None,
        paragraph: int | None = None,
        sheet: str = "",
        row_start: int | None = None,
        row_end: int | None = None,
        block_type: str = "",
        line_start: int | None = None,
        line_end: int | None = None,
        table: int | None = None,
        max_blocks: int = 100,
    ) -> str:
        selected = _select_paths(file_path, attachment_id)
        if not selected:
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": "No attachment found"}]},
                ensure_ascii=False,
            )
        output: List[Dict[str, Any]] = []
        output_limit = max(1, min(int(max_blocks), 500))
        remaining_chars = 40_000
        for path in selected:
            if len(output) >= output_limit or remaining_chars <= 0:
                break
            try:
                result = _parse_path(path)
            except AttachmentPathError:
                continue
            for item in result.blocks:
                location = item.get("location") or {}
                if page is not None and location.get("page") != page:
                    continue
                if slide is not None and location.get("slide") != slide:
                    continue
                if paragraph is not None and location.get("paragraph") != paragraph:
                    continue
                if table is not None and location.get("table") != table:
                    continue
                if sheet and location.get("sheet") != sheet:
                    continue
                if block_type and item.get("type") != block_type:
                    continue
                row = location.get("row")
                if row_start is not None and (
                    not isinstance(row, int) or row < row_start
                ):
                    continue
                if row_end is not None and (
                    not isinstance(row, int) or row > row_end
                ):
                    continue
                line = location.get("line")
                if line_start is not None and (
                    not isinstance(line, int) or line < line_start
                ):
                    continue
                if line_end is not None and (
                    not isinstance(line, int) or line > line_end
                ):
                    continue
                block_text = str(item.get("text", ""))
                bounded_text = block_text[:remaining_chars]
                remaining_chars -= len(bounded_text)
                output.append(
                    {
                        "attachment_id": result.attachment_id,
                        "filename": result.filename,
                        "type": item.get("type"),
                        "location": location,
                        "text": bounded_text,
                        **{
                            key: value
                            for key, value in item.items()
                            if key not in {"type", "text", "location"}
                        },
                    }
                )
                if len(output) >= output_limit:
                    break
        return json.dumps(
            {"chunks": [{"output_type": "json", "content": output}]},
            ensure_ascii=False,
        )

    @tool(
        description=(
            "Export parsed attachment content. Parameters: "
            '{"format": "markdown|json|txt|csv", '
            '"file_path": "optional legacy path", '
            '"attachment_id": "optional stable ID"}.'
        )
    )
    def export_attachment_text(
        format: str = "markdown", file_path: str = "", attachment_id: str = ""
    ) -> str:
        selected = _select_paths(file_path, attachment_id)
        if not selected:
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": "No attachment found"}]},
                ensure_ascii=False,
            )
        export_format = (format or "markdown").lower()
        if export_format not in {"markdown", "json", "txt", "csv"}:
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": "Unsupported export format"}]},
                ensure_ascii=False,
            )
        outputs = []
        for path in selected:
            try:
                result = _parse_path(path)
            except AttachmentPathError:
                continue
            if export_format == "json":
                content = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
            elif export_format == "txt":
                content = result.plain_text[:200_000]
            elif export_format == "csv":
                buffer = io.StringIO()
                writer = csv.writer(buffer)
                tables = [item for item in result.blocks if item.get("type") == "table"]
                for table in tables:
                    rows = table.get("rows") or []
                    columns = table.get("columns") or []
                    if columns:
                        writer.writerow(columns)
                    writer.writerows(rows)
                content = buffer.getvalue()
            else:
                sections = []
                for item in result.blocks:
                    text = str(item.get("text") or "").strip()
                    if not text:
                        continue
                    if item.get("type") == "heading":
                        sections.append(f"{'#' * min(int(item.get('level', 1)), 6)} {text}")
                    else:
                        sections.append(text)
                content = "\n\n".join(sections)[:200_000]
            outputs.append(
                {
                    "attachment_id": result.attachment_id,
                    "filename": result.filename,
                    "format": export_format,
                    "content": content,
                }
            )
        return json.dumps(
            {"chunks": [{"output_type": "json", "content": outputs}]},
            ensure_ascii=False,
        )

    return [
        list_attachments,
        parse_attachment,
        search_attachment,
        read_attachment_region,
        export_attachment_text,
    ]
