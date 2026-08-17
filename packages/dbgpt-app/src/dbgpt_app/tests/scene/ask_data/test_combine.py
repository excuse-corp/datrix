from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from dbgpt_app.scene.ask_data.combine import ResultCombiner
from dbgpt_app.scene.ask_data.query.query_spec import ResultColumn, SceneResult
from dbgpt_app.scene.ask_data.schemas.combine import CombinedResult
from dbgpt_app.scene.ask_data.schemas.snapshot import (
    Snapshot,
    SnapshotSourceHashes,
    SnapshotStatus,
)
from dbgpt_app.scene.ask_data.visualization import ChartSpecBuilder


def _snapshot(scene_id: str) -> Snapshot:
    return Snapshot(
        snapshot_id=f"snap_{scene_id}",
        scene_id=scene_id,
        revision_id="1",
        status=SnapshotStatus.ACTIVE,
        content_hash="sha256:content",
        source_hashes=SnapshotSourceHashes(
            semantic_hash="sha256:semantic",
            schema_hash="sha256:schema",
            knowledge_hash="sha256:knowledge",
        ),
        routing_projection={},
        runtime_config={
            "query_model": {
                "metrics": [{"key": "amount", "name": "Amount", "unit": "元"}],
                "dimensions": [{"key": "department", "field": "department"}],
                "time": {},
            },
        },
        created_at=datetime.now(timezone.utc),
    )


def _result(scene_id: str, rows, *, truncated=False) -> SceneResult:
    return SceneResult(
        task_id=scene_id,
        scene_id=scene_id,
        scene_revision="1",
        snapshot_id=f"snap_{scene_id}",
        status="succeeded",
        grain=["department"],
        columns=[
            ResultColumn(key="department", type="dimension"),
            ResultColumn(key="amount", type="metric", unit="元"),
        ],
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        query_spec_hash="sha256:query",
        compiler_version="1.0",
        sql_hash="sha256:sql",
    )


def test_compare_aligns_by_stable_key_and_keeps_metric_columns():
    first = _result("contracts", [{"department": "D1", "amount": 100}])
    second = _result("receipts", [{"department": "D1", "amount": 40}])
    result = ResultCombiner().combine(
        results=[first, second],
        snapshots={
            "contracts": _snapshot("contracts"),
            "receipts": _snapshot("receipts"),
        },
        mode="compare",
        keys=["department"],
    )

    assert result.status == "succeeded"
    assert result.rows == [{"department": "D1", "amount": 40}]
    assert len(result.columns) == 3


def test_incompatible_results_fall_back_to_separate():
    first = _result("contracts", [{"department": "D1", "amount": 100}], truncated=True)
    second = _result("receipts", [{"department": "D1", "amount": 40}])
    result = ResultCombiner().combine(
        results=[first, second],
        snapshots={
            "contracts": _snapshot("contracts"),
            "receipts": _snapshot("receipts"),
        },
        mode="compare",
        keys=["department"],
    )

    assert result.mode == "separate"
    assert any(item["code"] == "TRUNCATED_RESULT" for item in result.warnings)


def test_scene_snapshots_do_not_drive_derived_metric_calculation():
    first = _result("contracts", [{"department": "D1", "amount": Decimal("100")}])
    second = _result("receipts", [{"department": "D1", "amount": Decimal("40")}])
    snapshots = {
        "contracts": _snapshot("contracts"),
        "receipts": _snapshot("receipts"),
    }
    result = ResultCombiner().combine(
        results=[first, second],
        snapshots=snapshots,
        mode="derived",
        keys=["department"],
        derived_metrics=["rate"],
    )

    assert "rate" not in result.rows[0]
    assert any(item["code"] == "DERIVED_METRIC_SKIPPED" for item in result.warnings)


def test_chart_builder_selects_metric_bar_and_table_fallback():
    combined = CombinedResult(
        mode="compare",
        status="succeeded",
        columns=[
            {"key": "department", "type": "dimension"},
            {"key": "amount", "type": "metric", "unit": "元"},
        ],
        rows=[{"department": "D1", "amount": 100}],
    )
    chart = ChartSpecBuilder().build(combined)[0]
    assert chart.type == "bar"

    empty = combined.model_copy(update={"rows": []})
    assert ChartSpecBuilder().build(empty)[0].type == "table"


def test_chart_builder_selects_line_and_donut_from_structured_fields():
    line = CombinedResult(
        mode="compare",
        status="succeeded",
        time_grain="month",
        columns=[
            {"key": "month", "type": "dimension"},
            {"key": "amount", "type": "metric", "unit": "元"},
        ],
        rows=[{"month": "2026-01", "amount": 100}],
    )
    assert ChartSpecBuilder().build(line)[0].type == "line"
    donut = CombinedResult.model_validate(
        {
            **line.model_dump(),
            "time_grain": None,
            "columns": [
                {"key": "department", "type": "dimension"},
                {"key": "receipt_rate", "type": "metric", "unit": "%"},
            ],
        }
    )
    assert ChartSpecBuilder().build(donut)[0].type == "donut"
