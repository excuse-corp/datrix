import pytest

from dbgpt_app.scene.ask_data.agents import SceneQueryAgent, SceneQueryAgentError
from dbgpt_app.scene.ask_data.schemas.snapshot import SnapshotStatus
from dbgpt_app.scene.ask_data.testsupport import build_test_snapshot


@pytest.mark.asyncio
async def test_scene_query_agent_uses_full_documents_and_returns_valid_spec():
    snapshot = build_test_snapshot("contracts", "rev-1")
    snapshot = snapshot.model_copy(
        update={
            "status": SnapshotStatus.ACTIVE,
            "runtime_config": {
                **snapshot.runtime_config,
                "documents": {
                    "data_dictionary_md": "| amount | decimal | 合同金额 |",
                    "business_semantics_md": "金额为空时不可当作零。",
                },
            },
        }
    )
    prompts: list[str] = []

    async def generate(prompt: str) -> str:
        prompts.append(prompt)
        return '{"metrics":["contract_amount"],"limit":2}'

    spec = await SceneQueryAgent(generate).build_query_spec(snapshot, "查询合同金额")

    assert spec.metrics == ["contract_amount"]
    assert "| amount | decimal | 合同金额 |" in prompts[0]
    assert "金额为空时不可当作零。" in prompts[0]


@pytest.mark.asyncio
async def test_scene_query_agent_rejects_spec_outside_snapshot_allowlist():
    snapshot = build_test_snapshot("contracts", "rev-1").model_copy(
        update={"status": SnapshotStatus.ACTIVE}
    )

    async def generate(_: str) -> str:
        return '{"metrics":["unapproved_metric"]}'

    with pytest.raises(SceneQueryAgentError) as exc_info:
        await SceneQueryAgent(generate).build_query_spec(snapshot, "查询金额")

    assert exc_info.value.code == "SCENE_QUERY_SPEC_INVALID"
