import json

from dbgpt_app.openapi.api_v1.tools import ask_data as ask_data_tools
from dbgpt_app.scene.ask_data.security import AskDataPrincipal


def test_main_agent_context_aggregates_scene_introductions_only(monkeypatch):
    monkeypatch.setattr(
        ask_data_tools,
        "_capability_items",
        lambda _principal: [
            {
                "scene_id": "contracts",
                "name": "合同分析",
                "description": "查询合同金额、数量、状态和签订趋势。",
                "can_do": ["查询合同金额"],
                "typical_questions": [],
            },
            {
                "scene_id": "projects",
                "name": "项目分析",
                "description": "查询项目阶段、预算和负责人分布。",
                "can_do": ["查询项目预算"],
                "typical_questions": [],
            },
        ],
    )

    context = ask_data_tools.ask_data_capability_summary(
        AskDataPrincipal(user_id="user-1")
    )

    assert "查询合同金额、数量、状态和签订趋势。" in context
    assert "查询项目阶段、预算和负责人分布。" in context
    assert "Full View Data Dictionary" not in context
    assert "Ontology" not in context


def test_main_agent_result_context_passes_scene_ids_and_columns(monkeypatch):
    captured = {}

    def fake_ontology_context(question, *, scene_ids=None, result_columns=None):
        captured["question"] = question
        captured["scene_ids"] = scene_ids
        captured["result_columns"] = result_columns
        return "ontology-context"

    monkeypatch.setattr(
        ask_data_tools,
        "_active_ontology_analysis_context",
        fake_ontology_context,
    )

    content = ask_data_tools._main_agent_result_content(
        {
            "status": "succeeded",
            "query_id": "qry_1",
            "results": [
                {
                    "scene_id": "contracts",
                    "status": "succeeded",
                    "row_count": 1,
                    "columns": [
                        {"key": "contract_amount", "type": "metric", "unit": "元"}
                    ],
                    "rows": [{"contract_amount": "100.00"}],
                }
            ],
        },
        max_result_tokens=None,
        question="合同金额是多少？",
    )

    payload = json.loads(content)
    assert payload["ontology_context_for_analysis"] == "ontology-context"
    assert payload["analysis_guidance_for_final_answer"]
    assert "先直接回答用户问题" in payload["analysis_guidance_for_final_answer"][0]
    assert captured["question"] == "合同金额是多少？"
    assert captured["scene_ids"] == ["contracts"]
    assert captured["result_columns"] == [
        {"key": "contract_amount", "type": "metric", "unit": "元"}
    ]
