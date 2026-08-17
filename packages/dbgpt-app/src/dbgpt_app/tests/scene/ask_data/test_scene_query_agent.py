import pytest

from dbgpt_app.scene.ask_data.agents import SceneQueryAgent, SceneQueryAgentError
from dbgpt_app.scene.ask_data.schemas.snapshot import SnapshotStatus
from dbgpt_app.scene.ask_data.testsupport import build_test_snapshot


@pytest.mark.asyncio
async def test_scene_selector_prompt_uses_scene_snapshots():
    snapshot = build_test_snapshot("contracts", "rev-1").model_copy(
        update={
            "routing_projection": {
                "scene_id": "contracts",
                "name": "合同分析",
                "description": "用于回答合同金额、合同项目和部门归属问题",
                "keywords": ["合同", "项目"],
            },
            "runtime_config": {
                **build_test_snapshot("contracts", "rev-1").runtime_config,
                "documents": {
                    "business_semantics_md": "国拨经费项目属于信息化项目合同分析范围。"
                },
            },
        }
    )
    prompts: list[str] = []

    async def generate(prompt: str) -> str:
        prompts.append(prompt)
        return '{"scene_ids":["contracts"],"reason":"合同金额问题"}'

    scene_ids = await SceneQueryAgent(generate).select_scenes(
        [snapshot], "国拨经费项目有哪些", max_scenes=3
    )

    assert scene_ids == ["contracts"]
    assert "## Available Scene Snapshots" in prompts[0]
    assert "snapshot_id" in prompts[0]
    assert "routing_projection" in prompts[0]
    assert "business_semantics_excerpt" in prompts[0]
    assert "国拨经费项目属于信息化项目合同分析范围" in prompts[0]
    assert "Do not perform keyword-only matching" in prompts[0]


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


@pytest.mark.asyncio
async def test_scene_sql_prompt_includes_general_predicate_matching_guidance():
    snapshot = build_test_snapshot("contracts", "rev-1")
    snapshot = snapshot.model_copy(
        update={
            "status": SnapshotStatus.ACTIVE,
            "runtime_config": {
                **snapshot.runtime_config,
                "schema": {
                    "dialect": "postgresql",
                    "columns": [
                        {
                            "name": "subject_name",
                            "data_type": "TEXT",
                            "nullable": True,
                            "comment": "业务对象名称",
                        },
                        {
                            "name": "amount",
                            "data_type": "NUMERIC",
                            "nullable": True,
                            "comment": "金额",
                        },
                    ],
                },
            },
        }
    )
    prompts: list[str] = []

    async def generate(prompt: str) -> str:
        prompts.append(prompt)
        return (
            "SELECT subject_name, amount FROM dbo.vw_contracts "
            "WHERE subject_name LIKE '%电子签章%'"
        )

    sql = await SceneQueryAgent(generate).build_sql(
        snapshot,
        "电子签章相关业务多少钱",
        dialect="postgresql",
    )

    assert "subject_name LIKE" in sql
    assert "## Predicate Matching Guidance" in prompts[0]
    assert "natural-language text fields" in prompts[0]
    assert "structured identifiers" in prompts[0]
    assert "query-intent words" in prompts[0]
    assert "never apply it blindly to IDs" in prompts[0]


@pytest.mark.asyncio
async def test_scene_sql_agent_retries_exact_match_on_natural_text_field():
    snapshot = _snapshot_with_sql_schema()
    responses = [
        (
            "SELECT project_name FROM dbo.vw_contracts "
            "WHERE department_name = '智慧校园建设办公室'"
        ),
        (
            "SELECT project_name FROM dbo.vw_contracts "
            "WHERE department_name LIKE '%智慧校园建设办公室%'"
        ),
    ]
    prompts: list[str] = []

    async def generate(prompt: str) -> str:
        prompts.append(prompt)
        return responses[len(prompts) - 1]

    sql = await SceneQueryAgent(generate).build_sql(
        snapshot,
        "智慧校园建设办公室有哪些项目",
        dialect="postgresql",
    )

    assert len(prompts) == 2
    assert "Retry Feedback" in prompts[1]
    assert "department_name LIKE" in sql


@pytest.mark.asyncio
async def test_scene_sql_agent_keeps_exact_match_when_user_requests_exact():
    snapshot = _snapshot_with_sql_schema()
    prompts: list[str] = []

    async def generate(prompt: str) -> str:
        prompts.append(prompt)
        return (
            "SELECT project_name FROM dbo.vw_contracts "
            "WHERE department_name = '智慧校园建设办公室'"
        )

    sql = await SceneQueryAgent(generate).build_sql(
        snapshot,
        "所属部门完全等于智慧校园建设办公室的项目有哪些",
        dialect="postgresql",
    )

    assert len(prompts) == 1
    assert "department_name =" in sql


@pytest.mark.asyncio
async def test_scene_sql_agent_does_not_treat_bidder_name_as_identifier():
    snapshot = _snapshot_with_sql_schema()
    responses = [
        "SELECT project_name FROM dbo.vw_contracts WHERE winning_bidder = '科技公司'",
        (
            "SELECT project_name FROM dbo.vw_contracts "
            "WHERE winning_bidder LIKE '%科技公司%'"
        ),
    ]
    prompts: list[str] = []

    async def generate(prompt: str) -> str:
        prompts.append(prompt)
        return responses[len(prompts) - 1]

    sql = await SceneQueryAgent(generate).build_sql(
        snapshot,
        "科技公司中标了哪些项目",
        dialect="postgresql",
    )

    assert len(prompts) == 2
    assert "winning_bidder LIKE" in sql


def _snapshot_with_sql_schema():
    snapshot = build_test_snapshot("contracts", "rev-1")
    return snapshot.model_copy(
        update={
            "status": SnapshotStatus.ACTIVE,
            "runtime_config": {
                **snapshot.runtime_config,
                "schema": {
                    "dialect": "postgresql",
                    "columns": [
                        {
                            "name": "department_name",
                            "data_type": "varchar(255)",
                            "nullable": True,
                            "comment": "所属部门",
                        },
                        {
                            "name": "project_name",
                            "data_type": "varchar(500)",
                            "nullable": True,
                            "comment": "项目名称",
                        },
                        {
                            "name": "project_contract_status",
                            "data_type": "varchar(255)",
                            "nullable": True,
                            "comment": "项目合同状态",
                        },
                        {
                            "name": "winning_bidder",
                            "data_type": "varchar(1000)",
                            "nullable": True,
                            "comment": "中标单位名称",
                        },
                    ],
                },
            },
        }
    )
