"""Helpers shared by document parsers."""

from pathlib import Path
from typing import Any, Dict, Iterable, List

from dbgpt_app.attachment.models import AttachmentParseResult, ParseOptions


def block(
    block_type: str,
    text: str = "",
    *,
    location: Dict[str, Any] | None = None,
    **extra: Any,
) -> Dict[str, Any]:
    item: Dict[str, Any] = {"type": block_type, "text": text}
    if location:
        item["location"] = location
    item.update(extra)
    return item


def finalize_result(
    *,
    attachment_id: str,
    path: Path,
    format_name: str,
    mime_type: str | None,
    parser: str,
    blocks: Iterable[Dict[str, Any]],
    metadata: Dict[str, Any] | None = None,
    warnings: List[str] | None = None,
    options: ParseOptions,
) -> AttachmentParseResult:
    all_blocks = list(blocks)
    normalized_blocks = all_blocks[: options.max_blocks]
    warning_list = list(warnings or [])
    if len(normalized_blocks) < len(all_blocks):
        warning_list.append(f"Block limit reached: {options.max_blocks}")

    remaining_chars = options.max_text_chars

    def limit_value(value: Any) -> Any:
        nonlocal remaining_chars
        if isinstance(value, str):
            limited = value[:remaining_chars]
            remaining_chars -= len(limited)
            return limited
        if isinstance(value, list):
            return [limit_value(item) for item in value]
        if isinstance(value, dict):
            return {
                key: item if key in {"type", "location"} else limit_value(item)
                for key, item in value.items()
            }
        return value

    normalized_blocks = [limit_value(item) for item in normalized_blocks]
    for index, item in enumerate(normalized_blocks, start=1):
        item.setdefault("block_id", f"b_{index:05d}")

    text_parts = []
    for item in normalized_blocks:
        text = str(item.get("text") or "").strip()
        if text:
            text_parts.append(text)
    plain_text = "\n\n".join(text_parts)
    if len(plain_text) > options.max_text_chars:
        plain_text = plain_text[: options.max_text_chars]
    if remaining_chars <= 0:
        warning_list.append(f"Text limit reached: {options.max_text_chars} characters")

    result_metadata = {
        "source_name": path.name,
        "size_bytes": path.stat().st_size if path.is_file() else None,
    }
    if metadata:
        result_metadata.update(metadata)
    return AttachmentParseResult(
        attachment_id=attachment_id,
        filename=path.name,
        format=format_name,
        mime_type=mime_type,
        parser=parser,
        metadata=result_metadata,
        blocks=normalized_blocks,
        plain_text=plain_text,
        warnings=warning_list,
    )
