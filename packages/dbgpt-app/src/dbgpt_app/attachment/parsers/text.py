"""Plain text parser with conservative encoding detection."""

import io
from pathlib import Path
from typing import Any, Dict, List

from dbgpt_app.attachment.models import AttachmentParseResult, ParseOptions

from .base import block, finalize_result


class TextParser:
    name = "text"

    def parse(
        self,
        path: Path,
        attachment_id: str,
        mime_type: str | None,
        options: ParseOptions,
    ) -> AttachmentParseResult:
        raw = path.read_bytes()
        encoding = "utf-8"
        confidence = None
        try:
            import chardet

            detected = chardet.detect(raw)
            encoding = detected.get("encoding") or encoding
            confidence = detected.get("confidence")
        except ImportError:
            pass

        warnings: List[str] = []
        try:
            text = raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            encoding = "utf-8"
            text = raw.decode(encoding, errors="replace")
            warnings.append("Encoding fallback to UTF-8 with replacement characters")

        replacement_count = text.count("\ufffd")
        if replacement_count:
            warnings.append(f"Inserted {replacement_count} replacement characters")

        if len(text) > options.max_text_chars:
            text = text[: options.max_text_chars]
            warnings.append(f"Text limit reached: {options.max_text_chars} characters")

        blocks: List[Dict[str, Any]] = []
        for line_number, paragraph in enumerate(
            io.StringIO(text, newline=None), start=1
        ):
            paragraph = paragraph.rstrip("\r\n")
            if paragraph.strip():
                if len(blocks) >= options.max_blocks:
                    warnings.append(f"Block limit reached: {options.max_blocks}")
                    break
                blocks.append(
                    block(
                        "paragraph",
                        paragraph,
                        location={"line": line_number},
                    )
                )
        line_count = (
            text.count("\n")
            + text.count("\r")
            - text.count("\r\n")
            + (1 if text and not text.endswith(("\n", "\r")) else 0)
        )
        return finalize_result(
            attachment_id=attachment_id,
            path=path,
            format_name="txt",
            mime_type=mime_type,
            parser=self.name,
            blocks=blocks,
            metadata={
                "encoding": encoding,
                "encoding_confidence": confidence,
                "line_count": line_count,
                "replacement_char_count": replacement_count,
            },
            warnings=warnings,
            options=options,
        )
