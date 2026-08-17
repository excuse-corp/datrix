"""Deterministic multi-scene result combination."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from ..query.query_spec import SceneResult
from ..schemas.combine import CombinedColumn, CombinedResult
from ..schemas.snapshot import Snapshot


class ResultCombiner:
    def combine(
        self,
        *,
        results: list[SceneResult],
        snapshots: dict[str, Snapshot],
        mode: str = "separate",
        keys: list[str] | None = None,
        derived_metrics: list[str] | None = None,
    ) -> CombinedResult:
        source_scene_ids = [result.scene_id for result in results]
        if mode == "separate" or len(results) < 2:
            return self._separate(results, source_scene_ids, mode)
        warnings = self._compatibility_warnings(results, snapshots, keys or [])
        if warnings:
            combined = self._separate(results, source_scene_ids, mode)
            combined.warnings.extend(warnings)
            return combined
        selected_keys = keys or self._common_keys(snapshots, source_scene_ids)
        rows, columns = self._align(results, selected_keys, snapshots)
        if mode == "derived":
            rows, derived_columns, derived_warnings = self._apply_derived(
                rows, derived_metrics or [], snapshots, source_scene_ids
            )
            columns.extend(derived_columns)
            warnings.extend(derived_warnings)
        time_grain = None
        if results and all(
            item.time_grain == results[0].time_grain for item in results
        ):
            time_grain = results[0].time_grain
        return CombinedResult(
            mode="derived" if mode == "derived" else "compare",
            status="partial_succeeded" if warnings else "succeeded",
            keys=selected_keys,
            time_grain=time_grain,
            columns=columns,
            rows=rows,
            source_scene_ids=source_scene_ids,
            warnings=warnings,
        )

    def _separate(
        self, results: list[SceneResult], source_scene_ids: list[str], mode: str
    ) -> CombinedResult:
        rows = [
            {"scene_id": result.scene_id, "rows": result.rows, "status": result.status}
            for result in results
        ]
        warnings = (
            [{"code": "SEPARATE_RESULTS", "message": "Results are kept separate"}]
            if len(results) > 1
            else []
        )
        return CombinedResult(
            mode="separate",
            status="partial_succeeded"
            if any(item.status != "succeeded" for item in results)
            else "separate",
            rows=rows,
            source_scene_ids=source_scene_ids,
            warnings=warnings,
        )

    @staticmethod
    def _common_keys(snapshots: dict[str, Snapshot], scene_ids: list[str]) -> list[str]:
        sets = [
            set(snapshots[scene_id].runtime_config.get("common_keys", []))
            for scene_id in scene_ids
        ]
        return sorted(set.intersection(*sets)) if sets else []

    def _compatibility_warnings(
        self,
        results: list[SceneResult],
        snapshots: dict[str, Snapshot],
        keys: list[str],
    ) -> list[dict[str, str]]:
        warnings: list[dict[str, str]] = []
        if any(result.status != "succeeded" for result in results):
            warnings.append(
                {
                    "code": "SCENE_RESULT_FAILED",
                    "message": "At least one scene result failed",
                }
            )
        if any(result.truncated for result in results):
            warnings.append(
                {
                    "code": "TRUNCATED_RESULT",
                    "message": "Truncated results cannot be aligned",
                }
            )
        first = results[0]
        for result in results[1:]:
            if result.time_range != first.time_range:
                warnings.append(
                    {
                        "code": "TIME_RANGE_MISMATCH",
                        "message": "Time ranges are not compatible",
                    }
                )
            if result.grain != first.grain:
                warnings.append(
                    {
                        "code": "GRAIN_MISMATCH",
                        "message": "Result grains are not compatible",
                    }
                )
            if result.time_grain != first.time_grain:
                warnings.append(
                    {
                        "code": "TIME_GRAIN_MISMATCH",
                        "message": "Time grains are not compatible",
                    }
                )
        common_keys = self._common_keys(
            snapshots, [result.scene_id for result in results]
        )
        if keys and not set(keys) <= set(common_keys):
            warnings.append(
                {
                    "code": "COMMON_KEY_NOT_ALLOWED",
                    "message": "Requested key is not common to all scenes",
                }
            )
        if not keys and not common_keys:
            warnings.append(
                {
                    "code": "COMBINE_NOT_ALLOWED",
                    "message": "No common stable key is available",
                }
            )
        return warnings

    @staticmethod
    def _align(
        results: list[SceneResult], keys: list[str], snapshots: dict[str, Snapshot]
    ) -> tuple[list[dict[str, Any]], list[CombinedColumn]]:
        merged: dict[tuple[Any, ...], dict[str, Any]] = {}
        columns = [CombinedColumn(key=key, name=key, type="dimension") for key in keys]
        metric_occurrences: dict[str, int] = {}
        metric_signatures: dict[str, set[tuple[Any, ...]]] = {}
        for result in results:
            for column in result.columns:
                if column.type == "metric":
                    metric_occurrences[column.key] = (
                        metric_occurrences.get(column.key, 0) + 1
                    )
                    metadata = {
                        item["key"]: item
                        for item in snapshots[result.scene_id].runtime_config.get(
                            "metrics", []
                        )
                    }
                    definition = metadata.get(column.key, {})
                    metric_signatures.setdefault(column.key, set()).add(
                        (
                            definition.get("name"),
                            definition.get("unit"),
                            definition.get("aggregation"),
                        )
                    )
        for result in results:
            metric_keys = {
                column.key for column in result.columns if column.type == "metric"
            }
            metadata = {
                item["key"]: item
                for item in snapshots[result.scene_id].runtime_config.get("metrics", [])
            }
            for metric_key in metric_keys:
                item = metadata.get(metric_key, {})
                output_key = (
                    f"{result.scene_id}__{metric_key}"
                    if (
                        metric_occurrences.get(metric_key, 0) > 1
                        and len(metric_signatures.get(metric_key, set())) > 1
                    )
                    else metric_key
                )
                columns.append(
                    CombinedColumn(
                        key=output_key,
                        name=item.get("name") or metric_key,
                        type="metric",
                        unit=item.get("unit"),
                        source_scene_id=result.scene_id,
                    )
                )
            for source_row in result.rows:
                identity = tuple(source_row.get(key) for key in keys)
                row = merged.setdefault(
                    identity, {key: source_row.get(key) for key in keys}
                )
                for metric_key in metric_keys:
                    output_key = (
                        f"{result.scene_id}__{metric_key}"
                        if (
                            metric_occurrences.get(metric_key, 0) > 1
                            and len(metric_signatures.get(metric_key, set())) > 1
                        )
                        else metric_key
                    )
                    row[output_key] = source_row.get(metric_key)
        return list(merged.values()), columns

    @staticmethod
    def _apply_derived(
        rows: list[dict[str, Any]],
        requested: list[str],
        snapshots: dict[str, Snapshot],
        scene_ids: list[str],
    ) -> tuple[list[dict[str, Any]], list[CombinedColumn], list[dict[str, str]]]:
        definitions = {}
        for scene_id in scene_ids:
            for item in snapshots[scene_id].runtime_config.get("derived_metrics", []):
                if isinstance(item, dict) and item.get("key"):
                    definitions[item["key"]] = item
        columns: list[CombinedColumn] = []
        warnings: list[dict[str, str]] = []
        for key in requested:
            definition = definitions.get(key)
            if not definition:
                warnings.append(
                    {
                        "code": "DERIVED_METRIC_SKIPPED",
                        "message": f"Unknown derived metric: {key}",
                    }
                )
                continue
            operation = definition.get("operation")
            numerator = definition.get("numerator")
            denominator = definition.get("denominator")
            numerator_key = str(numerator).rsplit(".", 1)[-1]
            denominator_key = str(denominator).rsplit(".", 1)[-1]
            numerator_scene = str(numerator).split(".", 1)[0]
            denominator_scene = str(denominator).split(".", 1)[0]
            numerator_definition = ResultCombiner._metric_definition(
                snapshots, numerator_scene, numerator_key
            )
            denominator_definition = ResultCombiner._metric_definition(
                snapshots, denominator_scene, denominator_key
            )
            if operation in {"add", "subtract"}:
                if not numerator_definition or not denominator_definition:
                    warnings.append(
                        {
                            "code": "DERIVED_METRIC_SKIPPED",
                            "message": f"Missing input metric for {key}",
                        }
                    )
                    continue
                if (
                    numerator_definition.get("unit")
                    != denominator_definition.get("unit")
                ):
                    warnings.append(
                        {
                            "code": "DERIVED_UNIT_MISMATCH",
                            "message": f"Input units differ for {key}",
                        }
                    )
                    continue
                if any(
                    item.get("additivity") == "non_additive"
                    for item in (numerator_definition, denominator_definition)
                ):
                    warnings.append(
                        {
                            "code": "DERIVED_NON_ADDITIVE",
                            "message": f"Non-additive input cannot be {operation}ed",
                        }
                    )
                    continue
            for row in rows:
                try:
                    if operation == "ratio":
                        denominator_value = Decimal(str(row.get(denominator_key)))
                        row[key] = (
                            None
                            if denominator_value == 0
                            else Decimal(str(row.get(numerator_key)))
                            / denominator_value
                        )
                    elif operation in {"add", "subtract"}:
                        left = Decimal(str(row.get(numerator_key)))
                        right = Decimal(str(row.get(denominator_key)))
                        row[key] = left + right if operation == "add" else left - right
                    else:
                        row[key] = None
                        warnings.append(
                            {
                                "code": "DERIVED_METRIC_SKIPPED",
                                "message": f"Unsupported operation: {operation}",
                            }
                        )
                except (InvalidOperation, TypeError, ValueError):
                    row[key] = None
            columns.append(
                CombinedColumn(
                    key=key,
                    name=definition.get("name") or key,
                    type="derived_metric",
                    unit=definition.get("unit"),
                )
            )
        return rows, columns, warnings

    @staticmethod
    def _metric_definition(
        snapshots: dict[str, Snapshot], scene_id: str, metric_key: str
    ) -> dict[str, Any] | None:
        snapshot = snapshots.get(scene_id)
        if snapshot is None:
            return None
        return next(
            (
                item
                for item in snapshot.runtime_config.get("metrics", [])
                if item.get("key") == metric_key
            ),
            None,
        )


__all__ = ["ResultCombiner"]
