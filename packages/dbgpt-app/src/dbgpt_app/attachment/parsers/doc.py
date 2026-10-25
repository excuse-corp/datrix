"""Legacy Word 97-2003 DOC parser adapter."""

from pathlib import Path

from dbgpt_app.attachment.models import AttachmentParseResult, ParseOptions

from .base import block, finalize_result


class DocParser:
    name = "doc"

    def parse(
        self,
        path: Path,
        attachment_id: str,
        mime_type: str | None,
        options: ParseOptions,
    ) -> AttachmentParseResult:
        from dbgpt_ext.rag.knowledge.doc import Word97DocParser

        warnings = []
        with Word97DocParser(str(path)) as parser:
            paragraphs = parser.extract_text_by_paragraphs()
        blocks = [
            block("paragraph", paragraph.strip(), location={"paragraph": index})
            for index, paragraph in enumerate(paragraphs, start=1)
            if paragraph and paragraph.strip()
        ]
        if not blocks:
            warnings.append("No readable DOC paragraphs found")
        return finalize_result(
            attachment_id=attachment_id,
            path=path,
            format_name="doc",
            mime_type=mime_type,
            parser=self.name,
            blocks=blocks,
            warnings=warnings,
            options=options,
        )
