---
name: document-data-filler
description: Fill uploaded DOCX or XLSX templates with explicit values derived from attachment parsing and data-query results, preserving the original and returning a validated copy. Use for requests such as "填写Word/Excel模板", "把查数结果回写附件", "填报表", or "populate a document template"; do not use for document summarization or general Excel analysis.
---

# Document Data Filler

Use this skill only after the platform has identified the uploaded template and, when needed, completed the data query. The attachment tools remain the source of truth for the uploaded file; this skill only performs deterministic file edits.

## Responsibilities

- Accept the uploaded file path, the user request, attachment context, query result text, and explicit fill assignments.
- Support `.xlsx` and `.docx` in the first version. Do not silently rewrite `.xls`; ask the user to convert it to `.xlsx`.
- Copy the original into `OUTPUT_DIR` and edit the copy. Never overwrite the upload.
- Write only the targets listed in `fill_values` or `fill_values_text`. Do not infer target cells from prose or invent missing values.
- Preserve Excel styles, merged ranges, formulas, worksheets, and Word structure as far as the library allows.
- Return the script's JSON result, including the generated `file_path`, filled targets, unresolved targets, and warnings.

## Required workflow

1. Call `list_attachments` and identify the relevant Word or Excel upload.
2. Call `parse_attachment` if it is not already parsed.
3. Use `search_attachment` and/or `read_attachment_region` to locate placeholders, worksheet/cell coordinates, table cells, or paragraphs. For Excel, use the `sheet`, `row_start`, and `row_end` filters when useful.
4. Decide whether the user request requires `ask_data_query`. If it does, call that tool before editing.
5. Load this skill and call `execute_skill_script_file` with one of the scripts below. The top-level `input_file` key is required because the platform replaces it with the actual uploaded path.
6. Pass the query result explicitly. The script cannot see the model conversation unless it is included in `query_result_text` or `query_result_file`.
7. Pass explicit assignments. Prefer `fill_values` for machine-generated values; use `fill_values_text` for a compact text contract.
8. If the result is not `completed`, do not claim the file was filled. Ask for clarification when targets are unresolved.
9. Return the generated `file_path` to the user and explain that it is a new copy.

## Script calls

For an Excel template:

```json
{
  "skill_name": "document-data-filler",
  "script_file_name": "fill_excel_template.py",
  "args": {
    "input_file": "",
    "user_query": "填写2026年9月华东销售情况",
    "attachment_context_text": "Sheet=销售；华东在第2行；B2为销售额，C2为同比",
    "query_result_text": "2026年9月华东销售额为128万元，同比增长12.5%。",
    "fill_values_text": "销售!B2=128万元\n销售!C2=12.5%"
  }
}
```

For a Word template:

```json
{
  "skill_name": "document-data-filler",
  "script_file_name": "fill_word_template.py",
  "args": {
    "input_file": "",
    "query_result_text": "合同金额为128万元。",
    "fill_values_text": "{{合同金额}}=128万元\ntable[1].cell[2,3]=已审批"
  }
}
```

The supported target syntax and safety rules are in `references/input-contract.md` and `references/filling-rules.md`.

## Validation

The fill scripts validate the generated copy before returning `completed`. Use `validate_filled_file.py` as a separate check when the workflow needs an additional verification step:

```json
{
  "skill_name": "document-data-filler",
  "script_file_name": "validate_filled_file.py",
  "args": {
    "input_file": "/path/to/generated/file.xlsx",
    "fill_values_text": "销售!B2=128万元"
  }
}
```
