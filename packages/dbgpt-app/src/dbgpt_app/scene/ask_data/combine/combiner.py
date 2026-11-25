"""Deterministic multi-scene result combination."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..query.capabilities import metric_definition as snapshot_metric_definition
from ..query.capabilities import query_metrics
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
        selected_keys = keys or []
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

    def _compatibility_warnings(
        self,
        results: list[SceneResult],
        snapshots: dict[str, Snapshot],
        keys: list[str],
    ) -> list[dict[str, str]]:
        del snapshots
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
            if result.time_basis != first.time_basis:
                warnings.append(
                    {
                        "code": "TIME_BASIS_MISMATCH",
                        "message": "Time bases are not compatible",
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
        if keys and not self._keys_exist_in_all_results(results, keys):
            warnings.append(
                {
                    "code": "COMMON_KEY_NOT_ALLOWED",
                    "message": "Requested key is not present in every scene result",
                }
            )
        elif keys and not all(
            self._has_unique_row_keys(result, keys) for result in results
        ):
            warnings.append(
                {
                    "code": "INVALID_ROW_KEYS",
                    "message": (
                        "Alignment requires complete and unique row keys "
                        "in each result"
                    ),
                }
            )
        if not keys:
            warnings.append(
                {
                    "code": "COMBINE_NOT_ALLOWED",
                    "message": "No Ontology relation key was provided for alignment",
                }
            )
        return warnings

    @staticmethod
    def _keys_exist_in_all_results(results: list[SceneResult], keys: list[str]) -> bool:
        for result in results:
            available = {column.key for column in result.columns}
            if not set(keys) <= available:
                return False
        return True

    @staticmethod
    def _has_unique_row_keys(result: SceneResult, keys: list[str]) -> bool:
        seen = set()
        for row in result.rows:
            identity = tuple(row.get(key) for key in keys)
            if any(value is None for value in identity):
                return False
            try:
                if identity in seen:
                    return False
                seen.add(identity)
            except TypeError:
                return False
        return True

    @staticmethod
    def _align(
        results: list[SceneResult], keys: list[str], snapshots: dict[str, Snapshot]
    ) -> tuple[list[dict[str, Any]], list[CombinedColumn]]:
        merged: dict[tuple[Any, ...], dict[str, Any]] = {}
        columns = [CombinedColumn(key=key, name=key, type="dimension") for key in keys]
        metric_occurrences = Counter(
            column.key
            for result in results
            for column in result.columns
            if column.type == "metric"
        )
        scene_occurrences = Counter(result.scene_id for result in results)
        used_keys = set(keys)
        for result in results:
            metadata = {
                item["key"]: item for item in query_metrics(snapshots[result.scene_id])
            }
            metric_output_keys = {}
            for column in result.columns:
                if column.type != "metric":
                    continue
                metric_key = column.key
                item = metadata.get(metric_key, {})
                output_key = metric_key
                if metric_occurrences[metric_key] > 1:
                    prefix = result.scene_id
                    if scene_occurrences[result.scene_id] > 1:
                        prefix = f"{prefix}__{result.task_id}"
                    output_key = f"{prefix}__{metric_key}"
                while output_key in used_keys:
                    output_key = f"{result.task_id}__{output_key}"
                used_keys.add(output_key)
                metric_output_keys[metric_key] = output_key
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
                for metric_key, output_key in metric_output_keys.items():
                    row[output_key] = source_row.get(metric_key)
        return list(merged.values()), columns

    @staticmethod
    def _apply_derived(
        rows: list[dict[str, Any]],
        requested: list[str],
        snapshots: dict[str, Snapshot],
        scene_ids: list[str],
    ) -> tuple[list[dict[str, Any]], list[CombinedColumn], list[dict[str, str]]]:
        del snapshots, scene_ids
        warnings: list[dict[str, str]] = []
        for key in requested:
            warnings.append(
                {
                    "code": "DERIVED_METRIC_SKIPPED",
                    "message": (
                        "Derived metric must be declared in the active Ontology "
                        f"before deterministic combination can apply it: {key}"
                    ),
                }
            )
        return rows, [], warnings

    @staticmethod
    def _metric_definition(
        snapshots: dict[str, Snapshot], scene_id: str, metric_key: str
    ) -> dict[str, Any] | None:
        snapshot = snapshots.get(scene_id)
        if snapshot is None:
            return None
        return snapshot_metric_definition(snapshot, metric_key)


__all__ = ["ResultCombiner"]
