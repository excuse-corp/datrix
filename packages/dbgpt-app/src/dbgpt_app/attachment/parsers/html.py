"""Safe static HTML parser."""

from pathlib import Path
from typing import Any, Dict, List

from dbgpt_app.attachment.models import AttachmentParseResult, ParseOptions

from .base import block, finalize_result


class HtmlParser:
    name = "html"

    def parse(
        self,
        path: Path,
        attachment_id: str,
        mime_type: str | None,
        options: ParseOptions,
    ) -> AttachmentParseResult:
        from bs4 import BeautifulSoup

        raw = path.read_bytes()
        encoding = "utf-8"
        try:
            import chardet

            encoding = chardet.detect(raw).get("encoding") or encoding
        except ImportError:
            pass
        html = raw.decode(encoding, errors="replace")
        warnings: List[str] = []
        if len(html) > options.max_text_chars:
            html = html[: options.max_text_chars]
            warnings.append(f"Text limit reached: {options.max_text_chars} characters")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "iframe", "object", "embed", "noscript"]):
            tag.decompose()

        blocks: List[Dict[str, Any]] = []
        if soup.title and soup.title.get_text(" ", strip=True):
            blocks.append(block("title", soup.title.get_text(" ", strip=True)))

        for element in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]):
            if len(blocks) >= options.max_blocks:
                warnings.append(f"Block limit reached: {options.max_blocks}")
                break
            value = element.get_text(" ", strip=True)
            if not value:
                continue
            if element.name.startswith("h"):
                blocks.append(block("heading", value, level=int(element.name[1:])))
            elif element.name == "li":
                blocks.append(block("list_item", value))
            else:
                blocks.append(block("paragraph", value))

        if options.extract_tables:
            for table_index, table in enumerate(soup.find_all("table"), start=1):
                if len(blocks) >= options.max_blocks:
                    warnings.append(f"Block limit reached: {options.max_blocks}")
                    break
                rows: List[List[str]] = []
                for row in table.find_all("tr"):
                    cells = [
                        cell.get_text(" ", strip=True)
                        for cell in row.find_all(["th", "td"])
                    ]
                    if cells:
                        rows.append(cells)
                if rows:
                    blocks.append(
                        block(
                            "table",
                            "\n".join(" | ".join(row) for row in rows),
                            location={"table": table_index},
                            columns=rows[0],
                            rows=rows[1:],
                        )
                    )

        links = []
        for anchor in soup.find_all("a", href=True):
            label = anchor.get_text(" ", strip=True)
            if label:
                links.append({"text": label, "href": anchor.get("href")})
        result = finalize_result(
            attachment_id=attachment_id,
            path=path,
            format_name="html",
            mime_type=mime_type,
            parser=self.name,
            blocks=blocks,
            metadata={"encoding": encoding, "links": links},
            warnings=warnings,
            options=options,
        )
        if not blocks:
            result.warnings.append("No readable HTML content found")
        return result
