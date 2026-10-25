"""Shared helpers for the document-data-filler skill."""

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Iterable


def emit_result(result: dict[str, Any]) -> None:
    """Emit the standard skill-tool envelope."""
    print(
        json.dumps(
            {"chunks": [{"output_type": "json", "content": result}]},
            ensure_ascii=False,
        )
    )


def error_result(
    message: str, *, status: str = "failed", code: str = "error"
) -> dict[str, Any]:
    return {
        "status": status,
        "code": code,
        "message": message,
        "filled_fields": [],
        "unresolved_fields": [],
        "warnings": [],
    }


def payload_from_argv(argv: list[str]) -> dict[str, Any]:
    if len(argv) < 2:
        raise ValueError("JSON arguments are required")
    payload = json.loads(argv[1])
    if not isinstance(payload, dict):
        raise ValueError("Script arguments must be a JSON object")
    return payload


def require_input_file(payload: dict[str, Any]) -> Path:
    raw_path = str(payload.get("input_file") or "").strip()
    if not raw_path:
        raise ValueError("input_file is required")
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Input file not found: {path}")
    return path


def load_query_result(payload: dict[str, Any]) -> str:
    result_text = str(payload.get("query_result_text") or "")
    result_file = str(payload.get("query_result_file") or "").strip()
    if result_file:
        path = Path(result_file).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"query_result_file not found: {path}")
        result_text = path.read_text(encoding="utf-8")
    return result_text


def output_path(input_path: Path, payload: dict[str, Any]) -> Path:
    output_dir = Path(os.environ.get("OUTPUT_DIR") or input_path.parent).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    requested = str(payload.get("output_filename") or "").strip()
    if requested:
        filename = Path(requested).name
        if filename != requested or filename in {".", ".."}:
            raise ValueError("output_filename must be a filename, not a path")
    else:
        filename = f"{input_path.stem}_filled{input_path.suffix}"

    candidate = output_dir / filename
    index = 2
    while candidate.resolve() == input_path or candidate.exists():
        candidate = output_dir / f"{input_path.stem}_filled_{index}{input_path.suffix}"
        index += 1
    shutil.copy2(input_path, candidate)
    return candidate


def _coerce_assignment(item: Any) -> tuple[str, Any]:
    if isinstance(item, dict):
        target = str(item.get("target") or item.get("field") or "").strip()
        if not target:
            raise ValueError("Each fill_values item needs a target")
        if "value" not in item:
            raise ValueError(f"Missing value for target: {target}")
        return target, item["value"]
    raise ValueError("fill_values list items must be objects with target and value")


def parse_assignments(
    payload: dict[str, Any],
) -> tuple[list[tuple[str, Any]], list[str]]:
    assignments: list[tuple[str, Any]] = []
    errors: list[str] = []
    structured = payload.get("fill_values")
    if isinstance(structured, dict):
        for target, value in structured.items():
            target_text = str(target).strip()
            if target_text:
                assignments.append((target_text, value))
    elif isinstance(structured, list):
        for index, item in enumerate(structured, start=1):
            try:
                assignments.append(_coerce_assignment(item))
            except ValueError as exc:
                errors.append(f"fill_values[{index}]: {exc}")
    elif structured is not None:
        errors.append("fill_values must be a mapping or a list")

    text = str(payload.get("fill_values_text") or "")
    for raw_line in re.split(r"[\r\n;]+", text):
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", line)
        if "=" in line:
            target, value = line.split("=", 1)
        elif "：" in line:
            target, value = line.split("：", 1)
        else:
            errors.append(f"Malformed assignment: {raw_line.strip()}")
            continue
        target = target.strip()
        if not target:
            errors.append(f"Malformed assignment: {raw_line.strip()}")
            continue
        assignments.append((target, value.strip()))
    return assignments, errors


def text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def completed_or_partial(
    *,
    output_file: Path,
    filled: list[str],
    unresolved: list[str],
    warnings: list[str],
    query_result_text: str,
) -> dict[str, Any]:
    return {
        "status": "completed" if not unresolved else "partial",
        "file_path": str(output_file),
        "filename": output_file.name,
        "filled_fields": unique(filled),
        "unresolved_fields": unique(unresolved),
        "warnings": unique(warnings),
        "query_result_received": bool(query_result_text.strip()),
    }
