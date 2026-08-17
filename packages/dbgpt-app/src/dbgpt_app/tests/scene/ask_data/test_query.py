from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

from dbgpt_app.scene.ask_data.query import (
    QueryAstValidator,
    QuerySafetyError,
    QuerySpec,
    QuerySpecCompiler,
    QuerySpecValidationError,
    SafeSceneQueryExecutor,
    TimeRange,
)
from dbgpt_app.scene.ask_data.schemas.snapshot import (
    Snapshot,
    SnapshotSourceHashes,
    SnapshotStatus,
)


def _snapshot() -> Snapshot:
    return Snapshot(
        snapshot_id="snap_contracts",
        scene_id="contracts",
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
            "data_source": "test",
            "view": "dbo.vw_contracts",
            "dimensions": [
                {
                    "key": "department",
                    "field": "department_id",
                    "filter_operators": ["eq", "in"],
                }
            ],
            "metrics": [
                {
                    "key": "contract_amount",
                    "field": "contract_amount",
                    "aggregation": "sum",
                }
            ],
            "time": {
                "field": "signed_date",
                "required": False,
                "granularities": ["day", "month"],
            },
            "named_filters": [],
            "query_limits": {"max_rows": 2},
        },
    )


def _spec() -> QuerySpec:
    return QuerySpec(
        dimensions=["department"],
        metrics=["contract_amount"],
        time_range=TimeRange(
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_exclusive=datetime(2026, 4, 1, tzinfo=timezone.utc),
            timezone="UTC",
        ),
        filters=[{"dimension": "department", "operator": "in", "values": ["D1", "D2"]}],
        order_by=[{"key": "contract_amount", "direction": "desc"}],
        limit=2,
    )


def test_query_spec_rejects_non_snapshot_keys_and_limit():
    with pytest.raises(QuerySpecValidationError) as raised:
        QuerySpecCompiler().compile(
            QuerySpec(dimensions=["other_table"], metrics=["contract_amount"], limit=3),
            _snapshot(),
        )

    assert {issue.code for issue in raised.value.issues} >= {
        "DIMENSION_NOT_ALLOWED",
        "LIMIT_EXCEEDED",
    }


def test_compiler_uses_parameters_and_bound_view_only():
    compiled = QuerySpecCompiler().compile(_spec(), _snapshot())

    assert "D1" not in compiled.sql
    assert "p_filter_0_0" in compiled.parameters
    assert "vw_contracts" in compiled.sql
    assert "contract_amount" not in str(compiled.parameters)


def test_ast_validator_rejects_write_star_and_other_view():
    validator = QueryAstValidator()
    with pytest.raises(QuerySafetyError):
        validator.validate(
            "SELECT * FROM dbo.vw_contracts", _snapshot(), dialect="sqlite"
        )
    with pytest.raises(QuerySafetyError):
        validator.validate(
            "DELETE FROM dbo.vw_contracts", _snapshot(), dialect="sqlite"
        )
    with pytest.raises(QuerySafetyError):
        validator.validate(
            "SELECT department_id FROM dbo.other_view", _snapshot(), dialect="sqlite"
        )
    with pytest.raises(QuerySafetyError):
        validator.validate(
            "SELECT readfile('secret.txt') FROM dbo.vw_contracts",
            _snapshot(),
            dialect="sqlite",
        )
    with pytest.raises(QuerySafetyError):
        validator.validate(
            "WITH x AS (SELECT department_id FROM dbo.other_view) "
            "SELECT department_id FROM x",
            _snapshot(),
            dialect="sqlite",
        )


def test_ast_validator_allows_count_star_and_postgresql_alias():
    QueryAstValidator().validate(
        "SELECT COUNT(*) AS contract_count FROM dbo.vw_contracts",
        _snapshot(),
        dialect="postgresql",
    )


def test_executor_returns_rows_and_truncates():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE vw_contracts ("
                "department_id TEXT, contract_amount INTEGER, signed_date TEXT)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO vw_contracts VALUES "
                "('D1', 10, '2026-01-02'), ('D2', 20, '2026-02-01'), "
                "('D3', 30, '2026-03-01')"
            )
        )
    snapshot = _snapshot().model_copy(
        update={
            "runtime_config": {**_snapshot().runtime_config, "view": "vw_contracts"}
        }
    )
    spec = _spec().model_copy(update={"filters": [], "limit": 2})
    compiled = QuerySpecCompiler().compile(spec, snapshot)

    keys, rows, truncated, duration = SafeSceneQueryExecutor().execute(
        engine, compiled, snapshot
    )

    assert keys == ["department", "contract_amount"]
    assert len(rows) == 2
    assert truncated
    assert duration >= 0


def test_executor_does_not_mark_exact_row_limit_as_truncated():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE vw_contracts ("
                "department_id TEXT, contract_amount INTEGER, signed_date TEXT)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO vw_contracts VALUES "
                "('D1', 10, '2026-01-02'), ('D2', 20, '2026-02-01')"
            )
        )
    snapshot = _snapshot().model_copy(
        update={
            "runtime_config": {**_snapshot().runtime_config, "view": "vw_contracts"}
        }
    )
    spec = _spec().model_copy(update={"filters": [], "limit": 2})
    compiled = QuerySpecCompiler().compile(spec, snapshot)

    _, rows, truncated, _ = SafeSceneQueryExecutor().execute(
        engine, compiled, snapshot
    )

    assert len(rows) == 2
    assert not truncated
