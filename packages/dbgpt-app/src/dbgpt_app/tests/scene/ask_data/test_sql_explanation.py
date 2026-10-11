import pytest

from dbgpt_app.scene.ask_data.result.sql_explanation import build_sql_explanation
from dbgpt_app.scene.ask_data.schemas.snapshot import SnapshotStatus
from dbgpt_app.scene.ask_data.testsupport import build_test_snapshot


def test_sql_explanation_describes_sql_scope_and_business_rules():
    base = build_test_snapshot("contracts", "rev-1")
    snapshot = base.model_copy(
        update={
            "status": SnapshotStatus.ACTIVE,
            "runtime_config": {
                **base.runtime_config,
                "view": "dbo.vw_contracts",
                "schema": {"dialect": "postgresql", "columns": []},
                "query_model": {
                    **base.runtime_config["query_model"],
                    "time": {"field": "signed_date", "granularities": []},
                },
                "documents": {
                    "business_semantics_md": (
                        "## 业务口径\n"
                        "- 一行表示一份合同。\n"
                        "- 金额为空时不可按零处理。"
                    )
                },
            },
        }
    )

    explanation = build_sql_explanation(
        "SELECT department_id, SUM(contract_amount) AS contract_amount "
        "FROM dbo.vw_contracts "
        "WHERE signed_date >= '2026-01-01' "
        "GROUP BY department_id",
        snapshot,
    )

    assert explanation["source"] == "dbo.vw_contracts"
    assert explanation["grain"] == ["所属部门"]
    assert "每一行代表" in explanation["grain_description"]
    assert "合同金额" in explanation["metrics"][0]["definition"]
    assert "加在一起" in explanation["metrics"][0]["definition"]
    assert "从2026年1月1日开始" in explanation["time_scope"]
    assert "数据来自" in explanation["summary"]
    assert "数字的计算方式" in explanation["summary"]
    assert "SUM" not in explanation["summary"]
    assert "contract_amount" not in explanation["summary"]
    assert "SQL" not in explanation["summary"]
    assert "金额为空时不可按零处理" in explanation["summary"]


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ("status IN ('active', 'pending')", "状态是“active”、“pending”之一"),
        ("contract_amount BETWEEN 10 AND 20", "合同金额在10到20之间"),
        ("project_name LIKE '%华东%'", "项目名称包含“华东”"),
        ("customer_id IS NULL", "客户尚未填写"),
    ],
)
def test_sql_explanation_translates_common_filters(condition, expected):
    base = build_test_snapshot("contracts", "rev-1")
    snapshot = base.model_copy(
        update={
            "runtime_config": {
                **base.runtime_config,
                "schema": {"dialect": "postgresql", "columns": []},
            }
        }
    )

    explanation = build_sql_explanation(
        f"SELECT * FROM dbo.vw_contracts WHERE {condition}", snapshot
    )

    assert expected in explanation["filters"]
    assert "没有对数值做合计或平均处理" in explanation["summary"]


def test_sql_explanation_translates_time_range():
    base = build_test_snapshot("contracts", "rev-1")
    snapshot = base.model_copy(
        update={
            "runtime_config": {
                **base.runtime_config,
                "schema": {"dialect": "postgresql", "columns": []},
                "query_model": {
                    **base.runtime_config["query_model"],
                    "time": {"field": "signed_date", "granularities": []},
                },
            }
        }
    )

    explanation = build_sql_explanation(
        "SELECT * FROM dbo.vw_contracts "
        "WHERE signed_date >= '2026-01-01' AND signed_date < '2026-04-01'",
        snapshot,
    )

    assert explanation["time_scope"] == [
        "从2026年1月1日开始",
        "早于2026年4月1日",
    ]
