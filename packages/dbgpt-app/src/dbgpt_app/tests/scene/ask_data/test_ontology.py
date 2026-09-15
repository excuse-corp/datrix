import json
from datetime import datetime, timezone

import pytest

from dbgpt_app.scene.ask_data.ontology import (
    DEFAULT_ONTOLOGY_MARKDOWN,
    LEGACY_DEFAULT_ONTOLOGY_MARKDOWN,
    OntologyMarkdownParser,
)
from dbgpt_app.scene.ask_data.ontology.fragments import source_snapshot_refs
from dbgpt_app.scene.ask_data.ontology.generator import (
    generate_ontology_markdown_from_scene_sources,
    scene_semantic_keys_from_source,
)
from dbgpt_app.scene.ask_data.ontology.llm_generator import (
    OntologyLLMGenerationError,
    OntologyLLMGenerator,
)
from dbgpt_app.scene.ask_data.ontology.llm_schemas import OntologySceneSource
from dbgpt_app.scene.ask_data.ontology.llm_validator import OntologyLLMValidationError
from dbgpt_app.scene.ask_data.ontology.markdown import with_managed_frontmatter
from dbgpt_app.scene.ask_data.ontology.repository import InMemoryOntologyRepository
from dbgpt_app.scene.ask_data.ontology.schemas import OntologyRevisionStatus
from dbgpt_app.scene.ask_data.ontology.service import OntologyLifecycleService
from dbgpt_app.scene.ask_data.schemas.snapshot import (
    Snapshot,
    SnapshotSourceHashes,
    SnapshotStatus,
)


def test_default_ontology_is_empty_until_scenes_are_synchronized():
    result = OntologyMarkdownParser().compile(DEFAULT_ONTOLOGY_MARKDOWN)

    assert result.valid
    assert result.graph.nodes == []
    assert result.graph.edges == []


def test_business_reference_issues_do_not_block_format_compile():
    markdown = (
        DEFAULT_ONTOLOGY_MARKDOWN
        + """
## 业务实体

| ID | 名称 | 别名 | 定义 |
| --- | --- | --- | --- |
| customer | 客户 |  | 客户 |

## 实体关系

| ID | 主体 | 关系 | 客体 | 基数 | 说明 |
| --- | --- | --- | --- | --- | --- |
| customer_signs_unknown | customer | 签订 | missing | 1:N | 无效关系 |
"""
    )

    result = OntologyMarkdownParser().compile(markdown)

    assert result.valid
    assert not any(issue.code == "UNKNOWN_RELATION_TARGET" for issue in result.issues)
    assert any(edge.id == "customer_signs_unknown" for edge in result.graph.edges)


def test_format_issues_still_block_publish():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")
    saved = service.save_draft(
        markdown=(
            DEFAULT_ONTOLOGY_MARKDOWN
            + """
## 业务实体

| ID | 名称 | 别名 | 定义 |
| --- | --- | --- | --- |
| customer | 客户 |
"""
        ),
        user_id="admin",
        expected_revision=draft.revision,
    )

    with pytest.raises(ValueError) as exc_info:
        service.build_snapshot(saved.revision)

    assert getattr(exc_info.value, "code", None) == "ONTOLOGY_VALIDATION_FAILED"


def test_ontology_snapshot_is_immutable_after_activation():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")

    snapshot = service.build_snapshot(draft.revision)
    active = service.activate(snapshot.snapshot_id)

    assert active.status == "active"
    assert service.active_snapshot().snapshot_id == snapshot.snapshot_id


def test_ontology_can_activate_an_older_snapshot_as_a_rollback():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    first = service.draft(user_id="admin")
    first_snapshot = service.build_snapshot(first.revision)
    service.activate(first_snapshot.snapshot_id)
    second = service.save_draft(
        markdown=first.ontology_md + "\n\n",
        user_id="admin",
        expected_revision=first.revision,
    )
    second_snapshot = service.build_snapshot(second.revision)
    service.activate(second_snapshot.snapshot_id)

    restored = service.activate(first_snapshot.snapshot_id)

    assert restored.snapshot_id == first_snapshot.snapshot_id
    assert service.active_snapshot().snapshot_id == first_snapshot.snapshot_id
    assert (
        service.repository.get_snapshot(second_snapshot.snapshot_id).status == "ready"
    )


def test_sql_ontology_round_trip(tmp_path):
    from dbgpt.storage.metadata import db
    from dbgpt_app.scene.ask_data.ontology.repository import SqlOntologyRepository

    db.init_db(f"sqlite:///{tmp_path / 'ontology.db'}")
    service = OntologyLifecycleService(SqlOntologyRepository(db))

    draft = service.draft(user_id="admin")
    snapshot = service.build_snapshot(draft.revision)
    active = service.activate(snapshot.snapshot_id)

    assert active.status == "active"
    assert service.active_snapshot().revision == draft.revision


def _scene_snapshot(snapshot_id: str) -> Snapshot:
    return Snapshot(
        snapshot_id=snapshot_id,
        scene_id="contract_analysis",
        revision_id="1",
        status=SnapshotStatus.ACTIVE,
        content_hash="sha256:scene",
        source_hashes=SnapshotSourceHashes(
            semantic_hash="sha256:semantic",
            schema_hash="sha256:schema",
            knowledge_hash="sha256:knowledge",
        ),
        routing_projection={
            "name": "合同分析",
        },
        runtime_config={
            "query_model": {
                "dimensions": [{"key": "contract", "field": "contract_id"}],
                "metrics": [
                    {"key": "contract_amount", "field": "amount", "aggregation": "sum"}
                ],
                "time": {},
            },
            "rag": {},
        },
        created_at=datetime.now(timezone.utc),
    )


def test_source_refresh_must_be_accepted_before_publishing_active_scenes():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")
    scene = _scene_snapshot("snap_contract_v1")

    try:
        service.build_snapshot(draft.revision, [scene])
        assert False, "active scene changes must not be implicitly published"
    except ValueError as exc:
        assert getattr(exc, "code", None) == "ONTOLOGY_SOURCE_REFRESH_REQUIRED"

    refreshed = service.generate_from_active_scenes(
        scene_snapshots=[scene],
        user_id="admin",
        expected_revision=draft.revision,
        apply=True,
    )
    snapshot = service.build_snapshot(refreshed["revision"].revision, [scene])

    assert snapshot.source_scene_snapshots[0]["snapshot_id"] == "snap_contract_v1"
    assert snapshot.source_scene_snapshots[0]["semantic_md_hash"] == "sha256:semantic"


def test_source_manifest_uses_semantic_md_hash_name():
    scene = _scene_snapshot("snap_contract_v1")

    refs = source_snapshot_refs([scene])

    assert refs == [
        {
            "scene_id": "contract_analysis",
            "snapshot_id": "snap_contract_v1",
            "revision_id": "1",
            "semantic_md_hash": "sha256:semantic",
        }
    ]


def test_scene_semantic_keys_include_business_binding_aliases():
    scene = _scene_snapshot("snap_project_v2").model_copy(
        update={"scene_id": "project_analysis", "revision_id": "2"}
    )
    semantic_md = _project_semantic_markdown_without_query_model()

    keys = scene_semantic_keys_from_source(
        {"snapshot": scene, "semantic_md": semantic_md}
    )

    assert {
        "project.count",
        "project.contract_amount",
        "project.contract_paid_amount",
        "project.budget_amount",
        "project.contract_paid_ratio",
        "project.contract_unpaid_balance",
        "project.amount_missing_count",
        "project.date_missing_count",
    }.issubset(keys)


def test_publish_validation_accepts_business_scene_binding_aliases():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")
    scene = _scene_snapshot("snap_project_v2").model_copy(
        update={"scene_id": "project_analysis", "revision_id": "2"}
    )
    source = {
        "snapshot": scene,
        "semantic_md": _project_semantic_markdown_without_query_model(),
    }
    saved = service.save_draft(
        markdown=(
            DEFAULT_ONTOLOGY_MARKDOWN
            + """
## 业务实体

| ID | 名称 | 别名 | 定义 |
| --- | --- | --- | --- |
| ent_project | 项目 |  | 项目 |

## 指标

| ID | 名称 | 所属实体 | 单位 | 计算公式 | 来源场景 |
| --- | --- | --- | --- | --- | --- |
| met_project_count | 项目数量 | ent_project | 个 | 按 project_id 去重计数 | project_analysis |
| met_contract_amount | 合同金额 | ent_project | 元 | project_contract_amount 求和 | project_analysis |
| met_contract_paid_ratio | 合同已付比例 | ent_project | % | contract_paid_amount / project_contract_amount | project_analysis |

## 场景绑定

| 场景 | Ontology 对象 | 场景语义键 |
| --- | --- | --- |
| project_analysis | met_project_count | project.count |
| project_analysis | met_contract_amount | project.contract_amount |
| project_analysis | met_contract_paid_ratio | project.contract_paid_ratio |
"""
        ),
        user_id="admin",
        expected_revision=draft.revision,
    )
    saved = service.repository.save_source_scene_snapshots(
        saved.revision, source_snapshot_refs([source])
    )

    snapshot = service.build_snapshot(saved.revision, [source])

    assert snapshot.revision == saved.revision


def _project_semantic_markdown_without_query_model() -> str:
    return """---
schema_version: "1"
scene_id: project_analysis
name: 信息化项目数据查询
description: 查询项目、合同金额、付款、项目阶段、负责人、联系人、部门和供应商等信息。
data_source: dataman_data
view: sync.pcm_project_info
---
<!-- dataman:document=data-dictionary -->
# 数据字典

| 序号 | 字段名 | 中文名 | PostgreSQL 类型 | 来源口径 |
| --- | --- | --- | --- | --- |
| 1 | `project_id` | 项目ID | `integer` | 项目业务主键 |
| 2 | `project_contract_amount` | 项目合同金额 | `numeric(18,2)` | 项目库合同摘要金额 |
| 3 | `contract_paid_amount` | 合同已付金额 | `numeric(18,2)` | 项目口径已付款金额 |
| 4 | `project_budget` | 项目预算 | `numeric(18,2)` | 项目预算金额 |
| 5 | `project_start_date` | 项目启动日期 | `date` | 项目启动时间 |

<!-- dataman:document=business-semantics -->
# 业务语义说明

一行表示一个信息化项目，`project_id` 是项目业务主键。
"""


def test_scene_ontology_draft_reads_semantic_document_and_binds_query_key():
    scene = _scene_snapshot("snap_contract_v1")
    semantic_md = """---
schema_version: "1"
scene_id: contract_analysis
name: 合同分析
description: 合同业务语义
data_source: ecology
view: dbo.vw_contracts
---
<!-- dataman:document=data-dictionary -->
# 数据字典

| 字段 | 含义 | 类型 |
| --- | --- | --- |
| amount | 合同金额 | decimal |

<!-- dataman:document=business-semantics -->
# 业务语义说明

一行表示一份合同。
"""

    markdown = generate_ontology_markdown_from_scene_sources(
        [{"snapshot": scene, "semantic_md": semantic_md}], revision=1
    )

    expected = (
        "| contract_amount | 合同金额 | contract | 元 |  | "
        "contract_analysis | 无 |"
    )
    assert expected in markdown
    assert not markdown.lstrip().startswith("---")
    assert "草稿" not in markdown
    assert "人工确认" not in markdown
    assert "| 状态 |" not in markdown
    assert "| contract_analysis | contract_amount | contract_amount |" in markdown
    assert "| contract_analysis | contract | contract |" not in markdown


def test_scene_ontology_draft_reads_chinese_dictionary_headers_for_metrics():
    scene = _scene_snapshot("snap_project_v1").model_copy(
        update={"scene_id": "project_analysis"}
    )
    semantic_md = """---
schema_version: "1"
scene_id: project_analysis
name: 信息化项目数据查询
description: 查询项目、合同金额、付款、项目阶段、负责人、联系人、部门和供应商等信息。
data_source: dataman_data
view: sync.pcm_project_info
---
<!-- dataman:document=data-dictionary -->
# 视图数据字典

| 序号 | 目标字段 | 中文字段 | PostgreSQL 类型 | 含义与来源口径 |
|---:|---|---|---|---|
| 1 | `project_name` | 项目名称 | `varchar(500)` | 项目主名称，来源于项目库 |
| 2 | `project_contract_amount` | 项目合同金额 | `numeric` | 合同金额 |
| 3 | `contract_paid_amount` | 合同已付金额 | `numeric(18,2)` | 合同累计已付金额 |

<!-- dataman:document=business-semantics -->
# 业务语义说明

一行表示一个信息化项目。
"""

    markdown = generate_ontology_markdown_from_scene_sources(
        [{"snapshot": scene, "semantic_md": semantic_md}], revision=1
    )

    assert "| project_contract_amount | 项目合同金额 | project | 元 |" in markdown
    assert "| contract_paid_amount | 合同已付金额 | contract | 元 |" in markdown
    assert (
        "| project_analysis | project_contract_amount | "
        "project_contract_amount |"
    ) in markdown


def test_compile_check_treats_metric_conflict_note_as_business_context():
    markdown = (
        DEFAULT_ONTOLOGY_MARKDOWN
        + """
# 企业业务本体

## 业务实体

| ID | 名称 | 别名 | 定义 |
| --- | --- | --- | --- |
| project | 项目 |  | 项目 |

## 指标

| ID | 名称 | 所属实体 | 单位 | 计算公式 | 来源场景 | 冲突说明 |
| --- | --- | --- | --- | --- | --- | --- |
| project_amount | 项目金额 | project | 元 |  | scene_a | 口径冲突 |

## 跨场景关联

| ID | 左侧场景 | 右侧场景 | 业务实体 | 关联键 | 粒度 |
| --- | --- | --- | --- | --- | --- |
| scene_a_scene_b_project | scene_a | scene_b | project | project_id | 项目 |
"""
    )

    result = OntologyMarkdownParser().compile(markdown)
    codes = {issue.code for issue in result.issues}

    assert result.valid
    assert "METRIC_DEFINITION_CONFLICT" not in codes


def test_save_draft_strips_legacy_frontmatter():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")

    saved = service.save_draft(
        markdown=(
            "---\n"
            'schema_version: "1"\n'
            'ontology_id: "not_allowed"\n'
            "revision: 99\n"
            "---\n\n"
            "# 企业业务本体\n"
        ),
        user_id="admin",
        expected_revision=draft.revision,
    )

    assert not saved.ontology_md.startswith("---")
    assert 'ontology_id: "not_allowed"' not in saved.ontology_md
    assert saved.ontology_md == "# 企业业务本体\n"


def test_managed_markdown_strips_frontmatter_and_generation_boilerplate():
    cleaned = with_managed_frontmatter(
        (
            " \n"
            "---\n"
            'schema_version: "1"\n'
            'ontology_id: "global_business"\n'
            "revision: 9\n"
            "---\n\n"
            "# 企业业务本体\n\n"
            "> 本草稿由已激活场景的语义 Markdown 通过 LLM 生成；请人工确认。\n\n"
            "## 待管理员确认事项\n\n"
            "- 这类管理性内容不进入业务分析上下文。\n\n"
            "## 业务实体\n\n"
            "| ID | 名称 | 别名 | 定义 |\n"
            "| --- | --- | --- | --- |\n"
            "| project | 项目 |  | 项目 |\n"
        ),
        revision=9,
    )

    assert not cleaned.lstrip().startswith("---")
    assert "schema_version" not in cleaned
    assert "草稿" not in cleaned
    assert "待管理员确认事项" not in cleaned
    assert "| project | 项目 |" in cleaned


def test_draft_read_strips_existing_legacy_frontmatter():
    repository = InMemoryOntologyRepository()
    service = OntologyLifecycleService(repository)
    original = service.draft(user_id="admin")
    repository.save_draft(
        markdown=(
            "---\n"
            'schema_version: "1"\n'
            'ontology_id: "global_business"\n'
            f"revision: {original.revision}\n"
            "---\n\n"
            "# 企业业务本体\n"
        ),
        created_by="admin",
        expected_revision=original.revision,
    )

    current = service.draft(user_id="admin")

    assert current.revision == original.revision
    assert current.ontology_md == "# 企业业务本体\n"


def test_draft_read_sanitizes_ready_revision_without_creating_new_revision():
    repository = InMemoryOntologyRepository()
    service = OntologyLifecycleService(repository)
    original = service.draft(user_id="admin")
    saved = repository.save_draft(
        markdown=(
            "---\n"
            'schema_version: "1"\n'
            'ontology_id: "global_business"\n'
            f"revision: {original.revision}\n"
            "---\n\n"
            "# 企业业务本体\n"
            "> 本草稿由已激活场景的语义 Markdown 通过 LLM 生成。\n"
        ),
        created_by="admin",
        expected_revision=original.revision,
    )
    repository.save_compilation(
        saved.model_copy(update={"status": OntologyRevisionStatus.READY})
    )

    current = service.draft(user_id="admin")

    assert current.revision == original.revision
    assert not current.ontology_md.startswith("---")
    assert "schema_version" not in current.ontology_md
    assert "草稿" not in current.ontology_md


def test_untouched_legacy_sample_is_replaced_with_an_empty_ontology():
    repository = InMemoryOntologyRepository()
    service = OntologyLifecycleService(repository)
    original = service.draft(user_id="admin")
    repository.save_draft(
        markdown=LEGACY_DEFAULT_ONTOLOGY_MARKDOWN,
        created_by="admin",
        expected_revision=original.revision,
    )

    migrated = service.draft(user_id="admin")

    assert migrated.ontology_md == DEFAULT_ONTOLOGY_MARKDOWN


@pytest.mark.asyncio
async def test_llm_generator_outputs_compilable_analysis_markdown():
    responses = iter([json.dumps(_scene_extraction()), json.dumps(_global_synthesis())])

    async def generate(_: str) -> str:
        return next(responses)

    result = await OntologyLLMGenerator(generate).generate(
        [_llm_source()], revision=1
    )

    assert result.mode == "llm"
    assert result.entity_count == 1
    assert "## 指标关系" in result.markdown
    assert "合同是收入确认和履约约束的核心对象" in result.markdown
    assert not result.markdown.lstrip().startswith("---")
    assert "草稿" not in result.markdown
    assert "待管理员确认事项" not in result.markdown
    compilation = OntologyMarkdownParser().compile(result.markdown)
    assert compilation.valid
    assert compilation.prompt_projection["analysis_dimensions"]
    assert compilation.prompt_projection["result_analysis_rules"]


@pytest.mark.asyncio
async def test_llm_generator_rejects_invalid_json():
    async def generate(_: str) -> str:
        return "not-json"

    with pytest.raises(OntologyLLMGenerationError) as exc_info:
        await OntologyLLMGenerator(generate).generate([_llm_source()], revision=1)

    assert exc_info.value.code == "ONTOLOGY_LLM_INVALID_JSON"


@pytest.mark.asyncio
async def test_llm_generator_rejects_unknown_metric_entity():
    synthesis = _global_synthesis()
    synthesis["metrics"][0]["entity_id"] = "missing_entity"
    responses = iter(
        [json.dumps(_scene_extraction()), json.dumps(synthesis), json.dumps(synthesis)]
    )

    async def generate(_: str) -> str:
        return next(responses)

    with pytest.raises(OntologyLLMValidationError) as exc_info:
        await OntologyLLMGenerator(generate).generate([_llm_source()], revision=1)

    assert exc_info.value.code == "ONTOLOGY_LLM_REFERENCE_INVALID"


@pytest.mark.asyncio
async def test_llm_generator_retries_global_synthesis_reference_errors():
    invalid = _global_synthesis()
    invalid["cross_scene_analysis_paths"] = [
        {
            "id": "path_external_system",
            "topic": "跨系统合同分析",
            "scenes": ["contract_analysis", "external_finance_system"],
            "entities": ["contract"],
            "join_keys": ["contract_id"],
            "recommended_analysis": "联动外部系统分析合同收款。",
            "caveats": [],
        }
    ]
    responses = iter(
        [
            json.dumps(_scene_extraction()),
            json.dumps(invalid),
            json.dumps(_global_synthesis()),
        ]
    )
    prompts = []

    async def generate(prompt: str) -> str:
        prompts.append(prompt)
        return next(responses)

    result = await OntologyLLMGenerator(generate).generate(
        [_llm_source()], revision=1
    )

    assert result.mode == "llm"
    assert len(prompts) == 3
    assert "allowed_scene_ids" in prompts[-1]
    assert "external_finance_system" in prompts[-1]


def _llm_source() -> OntologySceneSource:
    return OntologySceneSource(
        scene_id="contract_analysis",
        snapshot_id="snap_contract_v1",
        revision_id="1",
        semantic_md_hash="sha256:semantic",
        semantic_md="""---
schema_version: "1"
scene_id: contract_analysis
name: 合同分析
description: 合同金额和付款分析
data_source: ecology
view: dbo.vw_contracts
---
<!-- dataman:document=data-dictionary -->
# 数据字典

| 字段 | 含义 | 类型 |
| --- | --- | --- |
| amount | 合同金额 | decimal |

<!-- dataman:document=business-semantics -->
# 业务语义说明

一行表示一份合同。
""",
        name="合同分析",
        description="合同金额和付款分析",
    )


def _scene_extraction() -> dict:
    evidence = {
        "scene_id": "contract_analysis",
        "quote": "一行表示一份合同。",
        "section": "业务语义说明",
    }
    return {
        "scene_id": "contract_analysis",
        "scene_name": "合同分析",
        "entities": [
            {
                "local_id": "contract",
                "name": "合同",
                "aliases": ["协议"],
                "definition": "合同是业务往来的履约和金额约束。",
                "business_role": "合同是收入确认和履约约束的核心对象。",
                "entity_type": "core_business_object",
                "evidence": [evidence],
            }
        ],
        "metrics": [
            {
                "local_id": "contract_amount",
                "name": "合同金额",
                "aliases": [],
                "unit": "元",
                "formula": None,
                "owner_entity_local_id": "contract",
                "semantic_key": "contract_amount",
                "analysis_meaning": "衡量合同签约规模。",
                "interpretation": (
                    "金额越高代表合同规模越大，应结合付款和阶段判断风险。"
                ),
                "positive_direction": "depends",
                "common_anomalies": ["金额为空", "金额为零"],
                "recommended_dimensions": ["部门", "负责人"],
                "evidence": [evidence],
            }
        ],
        "relations": [],
        "analysis_capabilities": [
            {
                "topic": "合同金额分析",
                "suitable_questions": ["合同金额是多少"],
                "recommended_dimensions": ["部门"],
                "recommended_metrics": ["合同金额"],
                "caveats": [],
            }
        ],
        "join_key_candidates": ["contract_id"],
        "clarification_rules": ["当合同金额缺少时间口径时，先确认统计期间。"],
        "quality_warnings": [],
    }


def _global_synthesis() -> dict:
    evidence = {
        "scene_id": "contract_analysis",
        "quote": "一行表示一份合同。",
        "section": "业务语义说明",
    }
    return {
        "entities": [
            {
                "id": "contract",
                "name": "合同",
                "aliases": ["协议"],
                "definition": "合同是业务往来的履约和金额约束。",
                "business_analysis_role": "合同是收入确认和履约约束的核心对象。",
                "source_scenes": ["contract_analysis"],
                "evidence": [evidence],
            }
        ],
        "relations": [],
        "metrics": [
            {
                "id": "contract_amount",
                "name": "合同金额",
                "aliases": [],
                "entity_id": "contract",
                "unit": "元",
                "formula": None,
                "analysis_meaning": "衡量合同签约规模。",
                "interpretation": (
                    "金额越高代表合同规模越大，应结合付款和阶段判断风险。"
                ),
                "positive_direction": "depends",
                "common_anomalies": ["金额为空", "金额为零"],
                "recommended_dimensions": ["department_dimension"],
                "related_metrics": [],
                "source_scenes": ["contract_analysis"],
                "scene_bindings": [
                    {"scene": "contract_analysis", "semantic_key": "contract_amount"}
                ],
                "conflict_note": None,
                "evidence": [evidence],
            }
        ],
        "metric_relations": [],
        "analysis_dimensions": [
            {
                "id": "department_dimension",
                "name": "部门",
                "applicable_entities": ["contract"],
                "applicable_metrics": ["contract_amount"],
                "analysis_usage": "按部门拆解合同金额结构和责任归属。",
                "caveats": ["注意责任部门与记录部门可能不同"],
            }
        ],
        "cross_scene_analysis_paths": [],
        "result_analysis_rules": [
            {
                "id": "amount_rule",
                "trigger": "当结果包含金额类指标时",
                "guidance": "优先分析总量、结构占比、Top/Bottom 和异常值。",
                "applies_to_entities": ["contract"],
                "applies_to_metrics": ["contract_amount"],
            }
        ],
        "clarification_rules": ["当合同金额缺少时间口径时，先确认统计期间。"],
        "unresolved_questions": [],
    }
