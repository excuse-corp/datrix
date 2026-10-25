"""PDF parser producing page and table blocks."""

from pathlib import Path
from typing import Any, Dict, List

from dbgpt_app.attachment.models import AttachmentParseResult, ParseOptions

from .base import block, finalize_result


class PdfParser:
    name = "pdf"

    def parse(
        self,
        path: Path,
        attachment_id: str,
        mime_type: str | None,
        options: ParseOptions,
    ) -> AttachmentParseResult:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        blocks: List[Dict[str, Any]] = []
        warnings: List[str] = []
        page_count = len(reader.pages)
        max_pages = 2_000
        if page_count > max_pages:
            warnings.append(f"Page limit reached: parsing first {max_pages} pages")
        for page_number, page in enumerate(reader.pages, start=1):
            if page_number > max_pages:
                break
            if len(blocks) >= options.max_blocks:
                warnings.append(f"Block limit reached: {options.max_blocks}")
                break
            text = page.extract_text() or ""
            if text.strip():
                blocks.append(
                    block("page", text.strip(), location={"page": page_number})
                )
            else:
                warnings.append(f"Page {page_number} contains no extractable text")

        if options.extract_tables:
            try:
                import pdfplumber

                with pdfplumber.open(str(path)) as pdf:
                    for page_number, page in enumerate(pdf.pages, start=1):
                        if page_number > max_pages:
                            break
                        if len(blocks) >= options.max_blocks:
                            warnings.append(
                                f"Block limit reached: {options.max_blocks}"
                            )
                            break
                        for table_index, table in enumerate(
                            page.extract_tables() or [], start=1
                        ):
                            if len(blocks) >= options.max_blocks:
                                break
                            rows = [
                                [str(cell or "").strip() for cell in row]
                                for row in table
                                if row
                            ]
                            if rows:
                                blocks.append(
                                    block(
                                        "table",
                                        "\n".join(" | ".join(row) for row in rows),
                                        location={
                                            "page": page_number,
                                            "table": table_index,
                                        },
                                        columns=rows[0],
                                        rows=rows[1:],
                                    )
                                )
            except ImportError:
                warnings.append("pdfplumber is unavailable; table extraction skipped")
            except Exception as exc:
                warnings.append(f"PDF table extraction failed: {exc}")

        result = finalize_result(
            attachment_id=attachment_id,
            path=path,
            format_name="pdf",
            mime_type=mime_type,
            parser=self.name,
            blocks=blocks,
            metadata={"page_count": page_count},
            warnings=warnings,
            options=options,
        )
        if not result.plain_text.strip() and page_count:
            result.status = "needs_ocr"
            result.warnings.append("No extractable PDF text found; OCR is required")
        return result
