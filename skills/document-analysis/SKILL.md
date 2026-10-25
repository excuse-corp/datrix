---
name: document-analysis
description: Use this skill for questions about uploaded PDF, DOCX, PPTX, DOC, TXT, Markdown, or HTML documents, including summaries, clause extraction, document comparison, meeting notes, structured extraction, and table export. Trigger keywords include "analyze document", "summarize PDF", "extract clauses", "compare documents", "文档分析", "总结PDF", "提取条款", "比较文档", "读取附件".
---

# Document Analysis

Use the platform attachment tools as the only source of uploaded document content. Do not open, shell-read, or parse the raw upload path directly.

## Required workflow

1. Call `list_attachments` first and identify the relevant attachment by filename, format, and parse status.
2. If the document is not completed, call `parse_attachment` with its `attachment_id`.
3. Use `search_attachment` for a focused question or clause lookup. Include the `attachment_id` whenever known.
4. Use `read_attachment_region` to inspect the page, slide, paragraph, line, table, or block cited by a search result.
5. For explicit export requests, call `export_attachment_text` with `format` set to `markdown`, `json`, `txt`, or `csv`. Use `csv` only for table output.

## Response requirements

- Cite the filename and the page, slide, paragraph, line, or table location when the tool provides one.
- Separate direct document evidence from interpretation and recommendations.
- For summaries, preserve the document's heading/section order and call out extraction warnings.
- For payment, legal, financial, or compliance clauses, quote only the necessary short passage and identify its source location.
- For comparisons, inspect matching regions in both attachments before concluding that content differs.
- If a PDF reports `needs_ocr`, explain that it is a scanned or textless document and do not claim that missing text was analyzed.
- If parsing fails, report the parser error and ask for a supported or repaired file rather than bypassing the attachment tools.

## Output patterns

### Summary
- Executive summary
- Section-by-section findings
- Important figures, dates, obligations, and risks
- Sources and parser warnings

### Structured extraction
Return a compact table with the extracted field, value, source location, and confidence/notes. Do not invent values absent from the document.

### Comparison
Use a table with topic, document A, document B, and difference. Distinguish missing content from unchanged content.
