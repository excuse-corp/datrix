from dbgpt_app.scene.ask_data.agents.context import build_scene_agent_context
from dbgpt_app.scene.ask_data.testsupport import build_test_snapshot


def test_scene_agent_context_contains_both_full_documents():
    current = build_test_snapshot("contracts", "rev-documents")
    current = current.model_copy(
        update={
            "runtime_config": {
                **current.runtime_config,
                "documents": {
                    "data_dictionary_md": "| contract_id | 合同编号 |",
                    "business_semantics_md": "合同金额为空时不可按零处理。",
                },
            }
        }
    )

    context = build_scene_agent_context(current, "查询合同金额")

    assert "查询合同金额" in context
    assert "| contract_id | 合同编号 |" in context
    assert "合同金额为空时不可按零处理。" in context
    assert "never output SQL" in context
