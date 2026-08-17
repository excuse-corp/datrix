from __future__ import annotations

from sqlalchemy import create_engine, text

from dbgpt_app.scene.ask_data.query import QuerySpec
from dbgpt_app.scene.ask_data.query_service import SceneQueryService
from dbgpt_app.tests.scene.ask_data.test_query import _snapshot


def test_scene_query_service_returns_scene_result():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE vw_contracts ("
                "department_id TEXT, contract_amount INTEGER, signed_date TEXT)"
            )
        )
        connection.execute(
            text("INSERT INTO vw_contracts VALUES ('D1', 10, '2026-01-01')")
        )
    snapshot = _snapshot().model_copy(
        update={
            "runtime_config": {
                **_snapshot().runtime_config,
                "view": "vw_contracts",
            }
        }
    )
    result = SceneQueryService().execute(
        task_id="task-1",
        spec=QuerySpec(
            dimensions=["department"], metrics=["contract_amount"], limit=2
        ),
        snapshot=snapshot,
        engine=engine,
    )

    assert result.status == "succeeded"
    assert result.row_count == 1
    assert result.rows[0]["contract_amount"] == 10
