"""Markdown parser preserving headings, code blocks, and tables."""

import re
from pathlib import Path
from typing import Any, Dict, List

from dbgpt_app.attachment.models import AttachmentParseResult, ParseOptions

from .base import block, finalize_result


class MarkdownParser:
    name = "markdown"

    def parse(
        self,
        path: Path,
        attachment_id: str,
        mime_type: str | None,
        options: ParseOptions,
    ) -> AttachmentParseResult:
        text = path.read_text(encoding="utf-8", errors="replace")
        warnings: List[str] = []
        if len(text) > options.max_text_chars:
            text = text[: options.max_text_chars]
            warnings.append(f"Text limit reached: {options.max_text_chars} characters")
        lines = text.splitlines()
        blocks: List[Dict[str, Any]] = []
        index = 0
        while index < len(lines):
            if len(blocks) >= options.max_blocks:
                warnings.append(f"Block limit reached: {options.max_blocks}")
                break
            line = lines[index]
            line_number = index + 1
            fence = re.match(r"^\s*(```+|~~~+)\s*([^ ]*)?\s*$", line)
            if fence:
                marker = fence.group(1)
                language = (fence.group(2) or "").strip()
                code_lines: List[str] = []
                index += 1
                while index < len(lines) and not lines[index].lstrip().startswith(
                    marker
                ):
                    code_lines.append(lines[index])
                    index += 1
                blocks.append(
                    block(
                        "code",
                        "\n".join(code_lines),
                        location={"line": line_number},
                        language=language,
                    )
                )
                index += 1
                continue

            heading = re.match(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$", line)
            if heading:
                blocks.append(
                    block(
                        "heading",
                        heading.group(2),
                        location={"line": line_number},
                        level=len(heading.group(1)),
                    )
                )
                index += 1
                continue

            if (
                options.extract_tables
                and index + 1 < len(lines)
                and "|" in line
                and re.match(r"^\s*\|?\s*:?-{3,}", lines[index + 1])
            ):
                table_lines = [line]
                index += 1
                while (
                    index < len(lines) and "|" in lines[index] and lines[index].strip()
                ):
                    table_lines.append(lines[index])
                    index += 1
                rows = [_split_markdown_row(item) for item in table_lines]
                blocks.append(
                    block(
                        "table",
                        "\n".join(table_lines),
                        location={"line": line_number},
                        columns=rows[0] if rows else [],
                        rows=rows[2:] if len(rows) > 2 else [],
                    )
                )
                continue

            if line.strip():
                blocks.append(
                    block("paragraph", line.strip(), location={"line": line_number})
                )
            index += 1

        return finalize_result(
            attachment_id=attachment_id,
            path=path,
            format_name="md",
            mime_type=mime_type,
            parser=self.name,
            blocks=blocks,
            metadata={"line_count": len(lines)},
            warnings=warnings,
            options=options,
        )


def _split_markdown_row(line: str) -> List[str]:
    value = line.strip().strip("|")
    return [cell.strip() for cell in value.split("|")]
