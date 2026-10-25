"""Isolated worker entry point for resource-limited document parsers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

from .models import AttachmentParseResult, ParseOptions
from .parsers import DocParser, DocxParser, ExcelParser, PdfParser, PptxParser


PARSER_CLASSES = {
    "pdf": PdfParser,
    "doc": DocParser,
    "docx": DocxParser,
    "pptx": PptxParser,
    "xlsx": ExcelParser,
    "xls": ExcelParser,
}


def parse_worker_payload(payload: Dict[str, Any]) -> AttachmentParseResult:
    format_name = str(payload.get("format") or "")
    parser_class = PARSER_CLASSES.get(format_name)
    if parser_class is None:
        raise ValueError("Unsupported isolated parser format")
    options = ParseOptions(**(payload.get("options") or {})).normalized()
    return parser_class().parse(
        Path(str(payload["path"])),
        str(payload["attachment_id"]),
        payload.get("mime_type"),
        options,
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = parse_worker_payload(payload)
        sys.stdout.write(json.dumps(result.to_dict(), ensure_ascii=False))
        return 0
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
