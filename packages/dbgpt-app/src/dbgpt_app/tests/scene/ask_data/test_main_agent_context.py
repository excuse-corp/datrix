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
