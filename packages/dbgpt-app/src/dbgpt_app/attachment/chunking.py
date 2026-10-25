"""Structure-aware chunking for parsed attachment blocks."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .models import AttachmentParseResult

DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 150


def _table_text(block: Dict[str, Any]) -> str:
    text = str(block.get("text") or "").strip()
    if text:
        return text
    columns = [str(value) for value in block.get("columns") or []]
    rows = block.get("rows") or []
    lines = []
    if columns:
        lines.append(" | ".join(columns))
    for row in rows:
        lines.append(" | ".join(str(value) for value in row))
    return "\n".join(lines).strip()


def _split_text(text: str, chunk_size: int, chunk_overlap: int) -> Iterable[str]:
    if len(text) <= chunk_size:
        yield text
        return
    start = 0
    text_length = len(text)
    while start < text_length:
        end = min(start + chunk_size, text_length)
        if end < text_length:
            boundary_start = start + max(1, int(chunk_size * 0.65))
            boundary = max(
                text.rfind("\n", boundary_start, end),
                text.rfind(" ", boundary_start, end),
            )
            if boundary > start:
                end = boundary
        piece = text[start:end].strip()
        if piece:
            yield piece
        if end >= text_length:
            break
        next_start = max(end - chunk_overlap, start + 1)
        start = next_start


def chunk_parse_result(
    result: AttachmentParseResult,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[Dict[str, Any]]:
    """Convert normalized blocks into bounded, location-preserving chunks."""

    bounded_size = max(1, int(chunk_size))
    bounded_overlap = max(0, min(int(chunk_overlap), bounded_size - 1))
    chunks: List[Dict[str, Any]] = []
    for block_index, source_block in enumerate(result.blocks, start=1):
        block_type = str(source_block.get("type") or "paragraph")
        text = (
            _table_text(source_block)
            if block_type == "table"
            else str(source_block.get("text") or "").strip()
        )
        if not text:
            continue
        block_id = str(source_block.get("block_id") or f"b_{block_index:05d}")
        location = dict(source_block.get("location") or {})
        for chunk_index, chunk_text in enumerate(
            _split_text(text, bounded_size, bounded_overlap), start=1
        ):
            chunks.append(
                {
                    "chunk_id": f"{block_id}_{chunk_index:04d}",
                    "block_id": block_id,
                    "chunk_index": chunk_index,
                    "type": block_type,
                    "text": chunk_text,
                    "location": location,
                }
            )
    return chunks
