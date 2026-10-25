# Input Contract

## Common arguments

- `input_file`: required. The uploaded `.xlsx` or `.docx` path. Keep this as a top-level argument so the platform can replace it with the real upload path.
- `user_query`: optional. The original user request, retained for audit context.
- `attachment_context_text`: optional. Relevant locations found by the attachment tools.
- `query_result_text`: required when the request used `ask_data_query`. It is passed through as audit context; it is not parsed as an instruction to write arbitrary cells.
- `query_result_file`: optional path to a text/JSON result for large query output. The script reads it as UTF-8 text and includes it in the audit summary.
- `fill_values_text`: compact assignments separated by newlines or semicolons. Split on the first `=` so values may contain `=`.
- `fill_values`: optional structured assignments. It can be a mapping from target to value or a list of objects such as `{"target": "销售!B2", "value": "128万元"}`.
- `output_filename`: optional filename only. The generated file is always placed under `OUTPUT_DIR`.

## Excel targets

- `Sheet Name!A1`: write one cell. This is the preferred form.
- `A1`: write the active worksheet only when the workbook has one sheet; otherwise report an unresolved target.
- `{{field}}` or `<<field>>`: replace that placeholder in every non-formula cell in the workbook.

Examples:

```text
销售!B2=128万元
销售!C2=12.5%
{{填报日期}}=2026-10-14
```

## Word targets

- `{{field}}` or `<<field>>`: replace all matching placeholders in body paragraphs, table cells, headers, and footers.
- `table[1].cell[2,3]`: write a 1-based row and column in the first body table.
- `paragraph[3]`: write the third body paragraph exposed by `python-docx`.

Examples:

```text
{{合同金额}}=128万元
table[1].cell[2,3]=已审批
paragraph[3]=2026年10月14日
```

Use the exact coordinates and placeholder names established by attachment parsing. A natural-language description such as `填写销售额` is not a valid target.
