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
        spec=QuerySpec(dimensions=["department"], metrics=["contract_amount"], limit=2),
        snapshot=snapshot,
        engine=engine,
    )

    assert result.status == "succeeded"
    assert result.row_count == 1
    assert result.rows[0]["contract_amount"] == 10
    columns = {column.key: column for column in result.columns}
    assert columns["contract_amount"].type == "metric"
    assert columns["contract_amount"].semantic_key == "contract_amount"
    assert columns["contract_amount"].aggregation == "sum"
    assert columns["department"].type == "dimension"
    assert result.time_basis == "signed_date"
    assert result.sql and "vw_contracts" in result.sql
    assert result.sql_explanation and result.sql_explanation["source"] == "vw_contracts"


def test_numeric_result_dimension_is_not_inferred_as_metric():
    snapshot = _snapshot().model_copy(
        update={
            "runtime_config": {
                **_snapshot().runtime_config,
                "query_model": {
                    **_snapshot().runtime_config["query_model"],
                    "dimensions": [{"key": "fiscal_year", "field": "fiscal_year"}],
                },
            }
        }
    )

    column = SceneQueryService._result_column(snapshot, "fiscal_year")

    assert column.type == "dimension"


def test_result_column_uses_custom_semantic_key_from_scene_dictionary():
    semantic_md = """---
schema_version: "1"
scene_id: contracts
name: 合同分析
data_source: test
view: dbo.vw_contracts
---
<!-- dataman:document=data-dictionary -->
# 数据字典

| 字段 | 业务名称 | 语义键 | 单位 | 聚合方式 |
| --- | --- | --- | --- | --- |
| contract_amount | 合同金额 | finance.contract.signed_amount | 元 | sum |

<!-- dataman:document=business-semantics -->
# 业务语义说明

合同金额按合同签订口径统计。
"""
    base = _snapshot()
    snapshot = base.model_copy(
        update={
            "runtime_config": {
                **base.runtime_config,
                "documents": {"semantic_md": semantic_md},
            }
        }
    )

    column = SceneQueryService._result_column(snapshot, "contract_amount")

    assert column.type == "metric"
    assert column.semantic_key == "finance.contract.signed_amount"
    assert column.label == "合同金额"
    assert column.source_field == "contract_amount"
    assert column.aggregation == "sum"
    assert column.unit == "元"
