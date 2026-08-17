from __future__ import annotations

from datetime import datetime, timezone

from dbgpt_app.scene.ask_data.scene.validator import SceneConfigValidator
from dbgpt_app.scene.ask_data.schemas.schema import ViewColumn, ViewSchema
from dbgpt_app.scene.ask_data.schemas.semantic import SemanticConfig


def _schema() -> ViewSchema:
    return ViewSchema(
        dialect="mssql",
        data_source="ecology",
        view="dbo.vw_contracts",
        columns=[
            ViewColumn(name="department_id", data_type="int", normalized_type="number"),
            ViewColumn(name="contract_id", data_type="int", normalized_type="number"),
            ViewColumn(
                name="contract_amount", data_type="decimal", normalized_type="number"
            ),
            ViewColumn(
                name="contract_status_code",
                data_type="varchar",
                normalized_type="string",
            ),
            ViewColumn(name="signed_date", data_type="date", normalized_type="date"),
        ],
        schema_hash="sha256:test",
        inspected_at=datetime.now(timezone.utc),
    )


def _config(**overrides) -> SemanticConfig:
    values = {
        "scene_id": "contracts",
        "name": "Contracts",
        "data_source": "ecology",
        "view": "dbo.vw_contracts",
        "agent": {"capabilities": ["query contracts"], "cannot_do": ["predict"]},
        "time": {"field": "signed_date"},
        "dimensions": [
            {
                "key": "status",
                "field": "contract_status_code",
                "filter_operators": ["in"],
            }
        ],
        "metrics": [
            {"key": "amount", "field": "contract_amount", "aggregation": "sum"}
        ],
        "named_filters": [
            {
                "key": "valid",
                "conditions": [
                    {"dimension": "status", "operator": "in", "values": ["SIGNED"]}
                ],
            }
        ],
    }
    values.update(overrides)
    return SemanticConfig.model_validate(values)


DICTIONARY = """
| 字段 | 含义 |
| --- | --- |
| department_id | 部门 |
| contract_id | 合同 |
| contract_amount | 合同金额 |
| contract_status_code | 合同状态 |
| signed_date | 签订日期 |
"""


def test_validator_accepts_bound_object_and_matching_dictionary():
    result = SceneConfigValidator().validate(
        _config(),
        _schema(),
        data_dictionary_md=DICTIONARY,
        expected_scene_id="contracts",
        expected_data_source="ecology",
        expected_view="dbo.vw_contracts",
    )

    assert result.valid
    assert result.errors == []


def test_validator_rejects_binding_mismatch():
    result = SceneConfigValidator().validate(
        _config(view="dbo.other_view"),
        _schema(),
        expected_scene_id="contracts",
        expected_data_source="ecology",
        expected_view="dbo.vw_contracts",
    )

    codes = {issue.code for issue in result.errors}

    assert not result.valid
    assert {"VIEW_MISMATCH", "VIEW_NOT_BOUND"} <= codes


def test_validator_rejects_dictionary_missing_actual_field():
    result = SceneConfigValidator().validate(
        _config(),
        _schema(),
        data_dictionary_md=DICTIONARY.replace("| signed_date | 签订日期 |\n", ""),
    )

    assert not result.valid
    assert any(issue.code == "FIELD_MISSING_IN_DICTIONARY" for issue in result.errors)


def test_validator_rejects_dictionary_field_missing_from_object():
    result = SceneConfigValidator().validate(
        _config(),
        _schema(),
        data_dictionary_md=DICTIONARY + "| missing_field | 不存在 |\n",
    )

    assert not result.valid
    assert any(issue.code == "FIELD_MISSING_IN_OBJECT" for issue in result.errors)
