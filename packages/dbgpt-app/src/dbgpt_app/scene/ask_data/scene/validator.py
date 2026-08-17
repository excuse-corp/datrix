"""Scene document versus bound table/view validation."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..schemas.schema import ViewSchema
from ..schemas.semantic import SemanticConfig


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    path: str
    message: str


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]


class SceneConfigValidator:
    """Validate only deterministic Scene binding constraints.

    The business semantic document is narrative context. It is intentionally not
    treated as a query model and does not need to declare metrics, dimensions,
    filters or examples. Queryable fields are derived from the real bound
    table/view schema after this validation succeeds.
    """

    def validate(
        self,
        config: SemanticConfig,
        view_schema: ViewSchema,
        *,
        data_dictionary_md: str | None = None,
        expected_scene_id: str | None = None,
        expected_data_source: str | None = None,
        expected_view: str | None = None,
    ) -> ValidationResult:
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []

        def error(code: str, path: str, message: str) -> None:
            errors.append(ValidationIssue(code=code, path=path, message=message))

        if expected_scene_id and config.scene_id != expected_scene_id:
            error(
                "SCENE_ID_MISMATCH",
                "scene_id",
                "Scene ID does not match revision metadata",
            )
        if expected_data_source and config.data_source != expected_data_source:
            error(
                "DATA_SOURCE_MISMATCH",
                "data_source",
                "Data source does not match revision metadata",
            )
        if expected_view and config.view != expected_view:
            error("VIEW_MISMATCH", "view", "View does not match revision metadata")
        if config.data_source != view_schema.data_source:
            error(
                "DATA_SOURCE_NOT_BOUND",
                "data_source",
                "Data source is not the inspected view source",
            )
        if config.view != view_schema.view:
            error("VIEW_NOT_BOUND", "view", "View is not the inspected object")

        if data_dictionary_md is not None:
            documented_fields = self.extract_dictionary_fields(data_dictionary_md)
            actual_fields = [column.name for column in view_schema.columns]
            if not documented_fields:
                error(
                    "DATA_DICTIONARY_FIELDS_NOT_FOUND",
                    "data_dictionary_md",
                    "Data dictionary must contain a Markdown field table",
                )
            else:
                documented = {self._field_key(field): field for field in documented_fields}
                actual = {self._field_key(field): field for field in actual_fields}
                missing_in_dictionary = [
                    actual[key] for key in sorted(actual.keys() - documented.keys())
                ]
                missing_in_object = [
                    documented[key] for key in sorted(documented.keys() - actual.keys())
                ]
                if missing_in_dictionary:
                    error(
                        "FIELD_MISSING_IN_DICTIONARY",
                        "data_dictionary_md",
                        "Actual field is missing from data dictionary: "
                        f"{missing_in_dictionary[0]}",
                    )
                if missing_in_object:
                    error(
                        "FIELD_MISSING_IN_OBJECT",
                        "data_dictionary_md",
                        "Data dictionary field does not exist in bound object: "
                        f"{missing_in_object[0]}",
                    )

        return ValidationResult(valid=not errors, errors=errors, warnings=warnings)

    @staticmethod
    def _require_column(columns: dict[str, Any], field: str, path: str, error) -> None:
        if field not in columns:
            error("UNKNOWN_COLUMN", path, f"Column does not exist: {field}")

    @staticmethod
    def _derived_references(expression: dict[str, Any]) -> set[str]:
        references: set[str] = set()
        for key in ("metric", "numerator", "denominator"):
            value = expression.get(key)
            if isinstance(value, str):
                references.add(value)
        for value in expression.values():
            if isinstance(value, dict):
                references.update(SceneConfigValidator._derived_references(value))
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        references.update(
                            SceneConfigValidator._derived_references(item)
                        )
        return references

    @classmethod
    def extract_dictionary_fields(cls, markdown: str) -> list[str]:
        """Extract field names from common Markdown data dictionary tables."""
        if not markdown or "|" not in markdown:
            return []
        lines = [line.strip() for line in markdown.replace("\r\n", "\n").split("\n")]
        preferred_headers = (
            "目标字段",
            "字段名",
            "字段",
            "列名",
            "column_name",
            "column",
            "field_name",
            "field",
            "name",
        )
        ignored_headers = {"上游视图字段", "来源字段", "含义", "中文字段", "类型"}
        fields: list[str] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            if not cls._is_table_row(line):
                index += 1
                continue
            header = cls._split_table_row(line)
            divider_index = index + 1
            if divider_index >= len(lines) or not cls._is_table_divider(lines[divider_index]):
                index += 1
                continue
            normalized_headers = [cls._normalize_header(cell) for cell in header]
            field_index = None
            for candidate in preferred_headers:
                if candidate in normalized_headers and candidate not in ignored_headers:
                    field_index = normalized_headers.index(candidate)
                    break
            if field_index is None:
                index += 1
                continue
            row_index = divider_index + 1
            while row_index < len(lines) and cls._is_table_row(lines[row_index]):
                cells = cls._split_table_row(lines[row_index])
                if field_index < len(cells):
                    field = cls._clean_field_name(cells[field_index])
                    if field and field not in fields:
                        fields.append(field)
                row_index += 1
            index = row_index
        return fields

    @staticmethod
    def _is_table_row(line: str) -> bool:
        return line.startswith("|") and line.endswith("|")

    @staticmethod
    def _is_table_divider(line: str) -> bool:
        if not SceneConfigValidator._is_table_row(line):
            return False
        cells = SceneConfigValidator._split_table_row(line)
        return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)

    @staticmethod
    def _split_table_row(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    @staticmethod
    def _normalize_header(value: str) -> str:
        return SceneConfigValidator._clean_field_name(value).lower()

    @staticmethod
    def _clean_field_name(value: str) -> str:
        value = value.strip()
        match = re.search(r"`([^`]+)`", value)
        if match:
            value = match.group(1)
        value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
        value = value.replace("`", "").strip()
        value = value.strip("\"'“”‘’[]【】")
        return value.strip()

    @staticmethod
    def _field_key(value: str) -> str:
        return SceneConfigValidator._clean_field_name(value).casefold()
