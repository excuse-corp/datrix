# Filling Rules

1. The original upload is immutable. The output filename defaults to `<original-stem>_filled.<suffix>` in `OUTPUT_DIR`.
2. Only explicit assignments are applied. The query result and attachment context provide evidence, not implicit write instructions.
3. Excel formulas are not overwritten by default. A target that points to a formula cell is returned in `unresolved_fields` with a warning. This prevents replacing calculated values with a query result.
4. Excel writes preserve the workbook's sheets, styles, merged ranges, and formulas outside explicitly targeted cells.
5. Word placeholder replacement works across multiple runs where possible. Direct table-cell or paragraph assignments may replace the text formatting inside that target, but do not rebuild the document.
6. A missing placeholder, invalid coordinate, unsupported extension, malformed assignment, or formula overwrite is reported instead of being silently ignored.
7. `.xls` is parseable by the attachment service but is not writable by this MVP. Convert it to `.xlsx` before calling this skill.
8. A successful result has `status: "completed"`, a readable `file_path`, and an empty `unresolved_fields` list. `partial` means a copy was produced but one or more targets were not filled.
