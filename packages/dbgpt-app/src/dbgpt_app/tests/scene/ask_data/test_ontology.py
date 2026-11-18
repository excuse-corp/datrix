import json
from datetime import datetime, timezone

import pytest

from dbgpt_app.scene.ask_data.ontology import (
    DEFAULT_ONTOLOGY_MARKDOWN,
    LEGACY_DEFAULT_ONTOLOGY_MARKDOWN,
    OntologyMarkdownParser,
)
from dbgpt_app.scene.ask_data.ontology.context import relevant_ontology_projection
from dbgpt_app.scene.ask_data.ontology.fragments import (
    build_scene_fragment,
    source_snapshot_refs,
)
from dbgpt_app.scene.ask_data.ontology.generator import (
    generate_ontology_markdown_from_scene_sources,
    scene_semantic_keys_from_source,
)
from dbgpt_app.scene.ask_data.ontology.llm_generator import (
    OntologyLLMGenerationError,
    OntologyLLMGenerator,
)
from dbgpt_app.scene.ask_data.ontology.llm_schemas import (
    OntologySceneSource,
    SceneOntologyExtraction,
)
from dbgpt_app.scene.ask_data.ontology.llm_validator import (
    OntologyLLMValidationError,
    validate_scene_extraction,
)
from dbgpt_app.scene.ask_data.ontology.markdown import with_managed_frontmatter
from dbgpt_app.scene.ask_data.ontology.repository import InMemoryOntologyRepository
from dbgpt_app.scene.ask_data.ontology.schemas import (
    OntologyRevisionStatus,
    OntologySnapshot,
)
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


def _approve_revision_for_test(service, revision, scene_snapshots=None):
    sources = scene_snapshots or []
    state = service.review_state(
        revision, user_id="admin", scene_snapshots=sources
    )
    for item in state["items"]:
        if item["required"] and item["status"] != "confirmed":
            service.review_object(
                revision,
                target_type=item["target_type"],
                target_id=item["target_id"],
                decision="confirmed",
                comment="test approval",
                actor_id="admin",
                expected_fingerprint=item["fingerprint"],
                scene_snapshots=sources,
            )
    state = service.review_state(
        revision, user_id="admin", scene_snapshots=sources
    )
    assert state["current_content_hash"]
    service.approve_revision(
        revision,
        actor_id="admin",
        expected_content_hash=state["current_content_hash"],
        scene_snapshots=sources,
    )


def _metric_ontology_markdown(metric_name="合同金额"):
    return (
        DEFAULT_ONTOLOGY_MARKDOWN
        + f"""
## 业务实体

| ID | 名称 | 别名 | 定义 |
| --- | --- | --- | --- |
| contract | 合同 |  | 业务合同 |

## 指标

| ID | 名称 | 所属实体 | 单位 | 计算公式 | 来源场景 |
| --- | --- | --- | --- | --- | --- |
| contract_amount | {metric_name} | contract | 元 |  | contract_analysis |
"""
    )


def test_publication_requires_confirmation_and_revision_approval():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")
    saved = service.save_draft(
        markdown=_metric_ontology_markdown(),
        user_id="admin",
        expected_revision=draft.revision,
    )

    with pytest.raises(ValueError) as exc_info:
        service.build_snapshot(saved.revision)

    assert getattr(exc_info.value, "code", None) == "ONTOLOGY_REVIEW_INCOMPLETE"

    state = service.review_state(saved.revision, user_id="admin")
    assert state["pending_count"] == 1
    metric_review = next(item for item in state["items"] if item["target_id"] == "contract_amount")
    assert metric_review["object"]["id"] == "contract_amount"
    assert metric_review["object"]["data"]["unit"] == "元"
    assert "formula" in metric_review["object"]["data"]
    with pytest.raises(ValueError) as rejection_error:
        service.review_object(
            saved.revision,
            target_type="node",
            target_id="contract_amount",
            decision="rejected",
            comment="  ",
            actor_id="admin",
            expected_fingerprint=metric_review["fingerprint"],
        )
    assert getattr(rejection_error.value, "code", None) == "ONTOLOGY_REVIEW_COMMENT_REQUIRED"

    _approve_revision_for_test(service, saved.revision)
    snapshot = service.build_snapshot(saved.revision)

    assert snapshot.revision == saved.revision
    assert snapshot.graph_json["nodes"]


def test_clean_invalid_metric_sources_removes_metrics_and_bindings():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")
    saved = service.save_draft(
        markdown=(
            DEFAULT_ONTOLOGY_MARKDOWN
            + """
## 指标

| ID | 名称 | 所属实体 | 单位 | 计算公式 | 来源场景 |
| --- | --- | --- | --- | --- | --- |
| invented_metric | 推演指标 | entity | 个 | 由字段推演 | contract_analysis |

## 指标关系

| ID | 主指标 | 关系 | 关联指标 | 分析用途 |
| --- | --- | --- | --- | --- |
| relation_invented | invented_metric | correlates_with | invented_metric | 不应保留 |

## 场景绑定

| 场景 | Ontology 对象 | 场景语义键 |
| --- | --- | --- |
| contract_analysis | invented_metric | contract.count |
"""
        ),
        user_id="admin",
        expected_revision=draft.revision,
    )
    source = {
        "snapshot": _scene_snapshot("snap_contract_v1"),
        "semantic_md": _project_semantic_markdown_without_query_model(),
    }

    result = service.clean_invalid_metric_sources(
        saved.revision,
        actor_id="admin",
        scene_snapshots=[source],
    )

    assert result["removed_metric_ids"] == ["invented_metric"]
    assert result["review_state"]["pending_count"] == 0
    cleaned = result["revision"].ontology_md
    assert "invented_metric" not in cleaned
    assert "relation_invented" not in cleaned
    assert "场景绑定" in cleaned


def test_exclude_review_object_removes_metric_and_records_audit_event():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")
    saved = service.save_draft(
        markdown=(
            DEFAULT_ONTOLOGY_MARKDOWN
            + """
## 指标

| ID | 名称 | 所属实体 | 单位 | 计算公式 | 来源场景 |
| --- | --- | --- | --- | --- | --- |
| invented_metric | 推演指标 | entity | 个 | 由字段推演 | contract_analysis |

## 指标关系

| ID | 主指标 | 关系 | 关联指标 | 分析用途 |
| --- | --- | --- | --- | --- |
| relation_invented | invented_metric | correlates_with | invented_metric | 不应保留 |

## 场景绑定

| 场景 | Ontology 对象 | 场景语义键 |
| --- | --- | --- |
| contract_analysis | invented_metric | contract.count |
"""
        ),
        user_id="admin",
        expected_revision=draft.revision,
    )
    item = next(
        value
        for value in service.review_state(saved.revision, user_id="admin")["items"]
        if value["target_id"] == "invented_metric"
    )

    result = service.exclude_review_object(
        saved.revision,
        target_type="node",
        target_id="invented_metric",
        comment="该指标没有业务确认，不纳入本体。",
        actor_id="admin",
        expected_fingerprint=item["fingerprint"],
    )

    assert "invented_metric" not in result["revision"].ontology_md
    assert "relation_invented" not in result["revision"].ontology_md
    assert result["review_state"]["pending_count"] == 0
    assert result["event"]["event_type"] == "object_excluded"
    assert any(
        event["event_type"] == "object_excluded"
        for event in result["review_state"]["events"]
    )


def test_exclude_review_object_removes_cross_scene_analysis_path():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")
    saved = service.save_draft(
        markdown=(
            DEFAULT_ONTOLOGY_MARKDOWN
            + """
## 跨场景分析路径

| ID | 分析主题 | 涉及场景 | 关联实体 | 推荐分析方式 |
| --- | --- | --- | --- | --- |
| path_unused | 不需要的跨场景盘点 | scene_a、scene_b | entity_a、entity_b | 不纳入 |
"""
        ),
        user_id="admin",
        expected_revision=draft.revision,
    )
    item = next(
        value
        for value in service.review_state(saved.revision, user_id="admin")["items"]
        if value["target_id"] == "path_unused"
    )

    result = service.exclude_review_object(
        saved.revision,
        target_type="node",
        target_id="path_unused",
        comment="当前没有实际业务关联，不纳入分析本体。",
        actor_id="admin",
        expected_fingerprint=item["fingerprint"],
    )

    assert "path_unused" not in result["revision"].ontology_md
    assert result["review_state"]["pending_count"] == 0


def test_validation_rejects_metric_without_semantic_document_declaration():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")
    saved = service.save_draft(
        markdown=(
            DEFAULT_ONTOLOGY_MARKDOWN
            + """
## 指标

| ID | 名称 | 所属实体 | 单位 | 计算公式 | 来源场景 |
| --- | --- | --- | --- | --- | --- |
| invented_metric | 推演指标 | entity | 个 | 由字段推演 | contract_analysis |
"""
        ),
        user_id="admin",
        expected_revision=draft.revision,
    )
    result = service.validate(
        saved.revision,
        [
            {
                "snapshot": _scene_snapshot("snap_contract_v1"),
                "semantic_md": _project_semantic_markdown_without_query_model(),
            }
        ],
    )

    assert not result["valid"]
    assert any(
        issue["code"] == "ONTOLOGY_METRIC_SOURCE_INVALID"
        for issue in result["issues"]
    )
def test_changed_ontology_revision_requires_fresh_review_and_approval():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")
    saved = service.save_draft(
        markdown=_metric_ontology_markdown(),
        user_id="admin",
        expected_revision=draft.revision,
    )
    _approve_revision_for_test(service, saved.revision)

    changed = service.save_draft(
        markdown=_metric_ontology_markdown("合同回款金额"),
        user_id="admin",
        expected_revision=saved.revision,
    )
    state = service.review_state(changed.revision, user_id="admin")

    assert changed.revision != saved.revision
    assert state["pending_count"] == 1
    assert state["items"][-1]["status"] == "candidate"
    assert not state["approved"]
    with pytest.raises(ValueError) as exc_info:
        service.build_snapshot(changed.revision)
    assert getattr(exc_info.value, "code", None) == "ONTOLOGY_REVIEW_INCOMPLETE"


def test_incomplete_cross_scene_contract_cannot_be_confirmed_by_api():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")
    saved = service.save_draft(
        markdown=(
            DEFAULT_ONTOLOGY_MARKDOWN
            + """
## 跨场景关联

| ID | 左侧场景 | 右侧场景 | 业务实体 | 关联键 | 粒度 |
| --- | --- | --- | --- | --- | --- |
| contract_payment | contract_analysis | payment_analysis | contract |  |  |
"""
        ),
        user_id="admin",
        expected_revision=draft.revision,
    )
    state = service.review_state(saved.revision, user_id="admin")
    edge_review = next(
        item for item in state["items"] if item["target_id"] == "contract_payment"
    )

    with pytest.raises(ValueError) as exc_info:
        service.review_object(
            saved.revision,
            target_type="edge",
            target_id="contract_payment",
            decision="confirmed",
            comment="确认",
            actor_id="admin",
            expected_fingerprint=edge_review["fingerprint"],
        )

    assert getattr(exc_info.value, "code", None) == "ONTOLOGY_REVIEW_EVIDENCE_INCOMPLETE"


def test_ontology_snapshot_is_immutable_after_activation():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")

    _approve_revision_for_test(service, draft.revision)
    snapshot = service.build_snapshot(draft.revision)
    active = service.activate(snapshot.snapshot_id)

    assert active.status == "active"
    assert service.active_snapshot().snapshot_id == snapshot.snapshot_id


def test_ontology_can_activate_an_older_snapshot_as_a_rollback():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    first = service.draft(user_id="admin")
    _approve_revision_for_test(service, first.revision)
    first_snapshot = service.build_snapshot(first.revision)
    service.activate(first_snapshot.snapshot_id)
    second = service.save_draft(
        markdown=first.ontology_md + "\n\n",
        user_id="admin",
        expected_revision=first.revision,
    )
    _approve_revision_for_test(service, second.revision)
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
    _approve_revision_for_test(service, draft.revision)
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
    _approve_revision_for_test(
        service, refreshed["revision"].revision, [scene]
    )
    snapshot = service.build_snapshot(refreshed["revision"].revision, [scene])

    assert snapshot.source_scene_snapshots[0]["snapshot_id"] == "snap_contract_v1"
    assert snapshot.source_scene_snapshots[0]["semantic_md_hash"] == "sha256:semantic"


def test_scene_fragment_only_adds_metrics_declared_in_semantic_document():
    semantic_md = """---
schema_version: "1"
scene_id: contract_analysis
name: 合同分析
data_source: dataman_data
view: public.contracts
metrics:
  - key: contract_amount
    name: 合同金额
    field: amount
    aggregation: sum
---
"""
    snapshot = _scene_snapshot("snap_contract_v1").model_copy(
        update={
            "routing_projection": {
                "name": "合同分析",
                "metrics": [
                    {"key": "contract_amount", "name": "合同金额"},
                    {"key": "sum_amount", "name": "金额合计"},
                    {"key": "count_rows", "name": "记录数"},
                ],
            },
            "runtime_config": {"documents": {"semantic_md": semantic_md}},
        }
    )

    graph = build_scene_fragment(snapshot)

    assert {node.id for node in graph.nodes if node.type == "metric"} == {
        "contract_analysis__contract_amount"
    }


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


def test_runtime_ontology_projection_hides_unassigned_and_unscoped_sources():
    snapshot = OntologySnapshot(
        snapshot_id="onto_shared",
        ontology_id="ontology",
        revision=3,
        status="active",
        content_hash="sha256:ontology",
        graph_json={
            "nodes": [
                {
                    "id": "contracts",
                    "type": "scene",
                    "name": "合同场景",
                    "provenance": [{"scene_id": "contracts"}],
                },
                {
                    "id": "contract_amount",
                    "type": "metric",
                    "name": "合同金额",
                    "data": {"source_scene": "contracts"},
                },
                {
                    "id": "payroll_amount",
                    "type": "metric",
                    "name": "薪酬金额",
                    "data": {"source_scene": "payroll"},
                },
                {
                    "id": "global_secret",
                    "type": "entity",
                    "name": "未标记全局实体",
                },
            ],
            "edges": [
                {
                    "id": "contract_binding",
                    "source": "contracts",
                    "target": "contract_amount",
                    "type": "scene_binding",
                    "name": "绑定",
                },
                {
                    "id": "payroll_binding",
                    "source": "payroll_amount",
                    "target": "global_secret",
                    "type": "relation",
                    "name": "泄露关系",
                    "provenance": [{"scene_id": "payroll"}],
                },
            ],
        },
        prompt_projection_json={
            "entities": [
                {
                    "id": "contract_entity",
                    "name": "合同",
                    "data": {"source_scenes": ["contracts"]},
                },
                {
                    "id": "payroll_entity",
                    "name": "薪酬",
                    "data": {"source_scenes": ["payroll"]},
                },
                {"id": "unscoped_entity", "name": "未标记实体", "data": {}},
            ],
            "metrics": [
                {
                    "id": "contract_amount",
                    "name": "合同金额",
                    "data": {"source_scene": "contracts"},
                },
                {
                    "id": "payroll_amount",
                    "name": "薪酬金额",
                    "data": {"source_scene": "payroll"},
                },
            ],
            "analysis_dimensions": [],
            "metric_relations": [],
            "cross_scene_analysis_paths": [
                {"id": "contract_payroll", "data": {"scenes": ["contracts", "payroll"]}}
            ],
            "result_analysis_rules": [
                {"id": "global_rule", "data": {"applies_to_metrics": []}}
            ],
            "clarification_rules": ["全局澄清规则"],
        },
    )

    projection = relevant_ontology_projection(
        snapshot,
        "合同金额",
        scene_ids=["contracts"],
        authorized_scene_ids=["contracts"],
        is_platform_admin=False,
    )
    encoded = json.dumps(projection, ensure_ascii=False)

    assert "合同场景" in encoded
    assert "合同金额" in encoded
    assert "薪酬" not in encoded
    assert "未标记" not in encoded
    assert projection["cross_scene_analysis_paths"] == []
    assert projection["result_analysis_rules"] == []
    assert projection["clarification_rules"] == []


def _cross_scene_bridge_markdown(status: str = "candidate") -> str:
    return f"""# 企业业务本体

## 跨场景分析路径

| ID | 状态 | 关系类型 | 分析主题 | 涉及场景 | 关联实体 | 关联键 | 映射依据 | 左侧时间依据 | 右侧时间依据 | 时间对齐条件 | 粒度对齐条件 | 推荐分析方式 | 允许分析 | 禁止推断 | 注意事项 | 证据 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| project_finance_bridge | {status} | reconciles_with | 信息化项目经费执行与财务支付 | project, finance | project | project_code | 项目主数据映射表 | 项目执行年度 | 财务支付日期 | 同一财年 | 同一项目编码、年度 | 对比项目经费执行与财务支付差异 | 展示差异并解释口径 | 不得将预算执行直接等同于已支付 | 两指标确认时点不同 | project: 项目语义文档；finance: 财务口径文档 |

## 跨场景关联

| ID | 状态 | 关系类型 | 左侧场景 | 右侧场景 | 业务实体 | 关联键 | 映射依据 | 左侧时间依据 | 右侧时间依据 | 时间对齐条件 | 粒度 | 粒度对齐条件 | 业务含义 | 允许分析 | 禁止推断 | 证据 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| project_finance_bridge_join | {status} | reconciles_with | project | finance | project | project_code | 项目主数据映射表 | 项目执行年度 | 财务支付日期 | 同一财年 | 年 | 同一项目编码、年度 | 分析项目经费执行与支付差异 | 展示差异并解释口径 | 不得视为同一指标 | 项目主数据映射与场景语义文档 |
"""


def test_markdown_compiles_bridge_status_and_analysis_contract():
    result = OntologyMarkdownParser().compile(_cross_scene_bridge_markdown())

    path = result.prompt_projection["cross_scene_analysis_paths"][0]
    edge = result.prompt_projection["cross_scene_joins"][0]
    assert result.valid
    assert path["data"]["status"] == "candidate"
    assert path["data"]["mapping_source"] == "项目主数据映射表"
    assert path["data"]["time_alignment_conditions"] == ["同一财年"]
    assert path["data"]["forbidden_inferences"] == ["不得将预算执行直接等同于已支付"]
    assert edge["data"]["status"] == "candidate"
    assert edge["data"]["scenes"] == ["project", "finance"]


def test_incomplete_confirmed_bridge_cannot_be_published():
    markdown = _cross_scene_bridge_markdown().replace(
        "| project_finance_bridge | candidate |",
        "| project_finance_bridge | confirmed |",
    )
    markdown = markdown.replace("项目主数据映射表", "", 1)

    result = OntologyMarkdownParser().compile(markdown)

    assert not result.valid
    assert any(issue.code == "CONFIRMED_BRIDGE_INCOMPLETE" for issue in result.issues)


def test_candidate_cross_scene_bridge_is_never_recalled_for_analysis():
    compilation = OntologyMarkdownParser().compile(_cross_scene_bridge_markdown())
    snapshot = OntologySnapshot(
        snapshot_id="onto_bridge_candidate",
        ontology_id="ontology",
        revision=1,
        status="active",
        content_hash="sha256:bridge",
        graph_json=compilation.graph.model_dump(mode="json"),
        prompt_projection_json=compilation.prompt_projection,
    )

    projection = relevant_ontology_projection(
        snapshot,
        "信息化项目经费执行与财务支付差异",
        scene_ids=["project", "finance"],
        result_profiles=[
            {
                "scene_id": "project",
                "grain": ["project_id", "fiscal_year"],
                "time_grain": "year",
                "time_basis": "execution_year",
            },
            {
                "scene_id": "finance",
                "grain": ["project_id", "month"],
                "time_grain": "month",
                "time_basis": "payment_date",
            },
        ],
    )

    assert projection["cross_scene_analysis_paths"] == []
    assert not any(
        node["id"] == "project_finance_bridge" for node in projection["nodes"]
    )
    assert not any(edge["type"] == "cross_scene_join" for edge in projection["edges"])


def test_confirmed_bridge_is_recalled_with_its_analysis_constraints():
    compilation = OntologyMarkdownParser().compile(
        _cross_scene_bridge_markdown("confirmed")
    )
    snapshot = OntologySnapshot(
        snapshot_id="onto_bridge_confirmed",
        ontology_id="ontology",
        revision=1,
        status="active",
        content_hash="sha256:bridge",
        graph_json=compilation.graph.model_dump(mode="json"),
        prompt_projection_json=compilation.prompt_projection,
    )

    time_range = {
        "start": "2025-01-01T00:00:00+00:00",
        "end_exclusive": "2026-01-01T00:00:00+00:00",
        "timezone": "UTC",
    }
    result_columns = [
        {"scene_id": "project", "key": "execution_amount", "type": "metric"},
        {"scene_id": "finance", "key": "paid_amount", "type": "metric"},
    ]
    result_profiles = [
        {
            "scene_id": "project",
            "grain": ["project_id", "fiscal_year"],
            "time_grain": "year",
            "time_basis": "项目执行年度",
            "time_range": time_range,
        },
        {
            "scene_id": "finance",
            "grain": ["project_id", "fiscal_year"],
            "time_grain": "year",
            "time_basis": "财务支付日期",
            "time_range": time_range,
        },
    ]
    projection = relevant_ontology_projection(
        snapshot,
        "信息化项目经费执行与财务支付差异",
        scene_ids=["project", "finance"],
        result_columns=result_columns,
        result_profiles=result_profiles,
    )

    assert compilation.valid
    assert len(projection["cross_scene_analysis_paths"]) == 1
    assert projection["cross_scene_analysis_paths"][0]["data"]["mapping_source"]
    assert (
        projection["cross_scene_analysis_paths"][0]["data"]["alignment_status"]
        == "verified"
    )
    assert (
        len(projection["cross_scene_analysis_paths"][0]["data"]["result_profiles"]) == 2
    )
    assert any(edge["type"] == "cross_scene_join" for edge in projection["edges"])

    authorized_projection = relevant_ontology_projection(
        snapshot,
        "信息化项目经费执行与财务支付差异",
        scene_ids=["project", "finance"],
        result_columns=result_columns,
        result_profiles=result_profiles,
        authorized_scene_ids=["project", "finance"],
        is_platform_admin=False,
    )
    restricted_projection = relevant_ontology_projection(
        snapshot,
        "信息化项目经费执行与财务支付差异",
        scene_ids=["project", "finance"],
        result_columns=result_columns,
        result_profiles=result_profiles,
        authorized_scene_ids=["project"],
        is_platform_admin=False,
    )
    assert len(authorized_projection["cross_scene_analysis_paths"]) == 1
    assert restricted_projection["cross_scene_analysis_paths"] == []


def test_confirmed_bridge_is_not_recalled_when_result_grain_or_time_does_not_align():
    compilation = OntologyMarkdownParser().compile(
        _cross_scene_bridge_markdown("confirmed")
    )
    snapshot = OntologySnapshot(
        snapshot_id="onto_bridge_misaligned",
        ontology_id="ontology",
        revision=1,
        status="active",
        content_hash="sha256:bridge",
        graph_json=compilation.graph.model_dump(mode="json"),
        prompt_projection_json=compilation.prompt_projection,
    )

    projection = relevant_ontology_projection(
        snapshot,
        "信息化项目经费执行与财务支付差异",
        scene_ids=["project", "finance"],
        result_columns=[
            {"scene_id": "project", "key": "execution_amount", "type": "metric"},
            {"scene_id": "finance", "key": "paid_amount", "type": "metric"},
        ],
        result_profiles=[
            {
                "scene_id": "project",
                "grain": ["project_id", "fiscal_year"],
                "time_grain": "year",
                "time_basis": "项目执行年度",
                "time_range": {
                    "start": "2025-01-01T00:00:00+00:00",
                    "end_exclusive": "2026-01-01T00:00:00+00:00",
                    "timezone": "UTC",
                },
            },
            {
                "scene_id": "finance",
                "grain": ["project_id", "month"],
                "time_grain": "month",
                "time_basis": "财务支付日期",
                "time_range": {
                    "start": "2025-02-01T00:00:00+00:00",
                    "end_exclusive": "2025-03-01T00:00:00+00:00",
                    "timezone": "UTC",
                },
            },
        ],
    )

    assert projection["cross_scene_analysis_paths"] == []
    assert not any(edge["type"] == "cross_scene_join" for edge in projection["edges"])


def test_result_metric_requires_an_explicit_scene_binding():
    snapshot = OntologySnapshot(
        snapshot_id="onto_binding",
        ontology_id="ontology",
        revision=1,
        status="active",
        content_hash="sha256:binding",
        graph_json={
            "nodes": [
                {"id": "projects", "type": "scene", "name": "项目场景"},
                {"id": "project.revenue", "type": "metric", "name": "项目收入"},
            ],
            "edges": [
                {
                    "id": "project_revenue_binding",
                    "source": "projects",
                    "target": "project.revenue",
                    "type": "scene_binding",
                    "name": "绑定",
                    "data": {"semantic_key": "project.revenue"},
                }
            ],
        },
        prompt_projection_json={
            "entities": [],
            "metrics": [{"id": "project.revenue", "name": "项目收入", "data": {}}],
            "edges": [
                {
                    "id": "project_revenue_binding",
                    "source": "projects",
                    "target": "project.revenue",
                    "type": "scene_binding",
                    "name": "绑定",
                    "data": {"semantic_key": "project.revenue"},
                }
            ],
            "analysis_dimensions": [],
            "metric_relations": [],
            "cross_scene_analysis_paths": [],
            "result_analysis_rules": [],
        },
    )

    projection = relevant_ontology_projection(
        snapshot,
        "项目收入",
        scene_ids=["projects"],
        result_columns=[
            {
                "key": "revenue_total",
                "semantic_key": "project.revenue",
                "type": "metric",
            }
        ],
    )

    assert projection["matched_bindings"][0]["matched_by"] == "semantic_key"
    assert projection["matched_bindings"][0]["confidence"] == "exact"
    assert projection["unmatched_columns"] == []


def test_candidate_scene_binding_is_not_used_for_result_matching():
    snapshot = OntologySnapshot(
        snapshot_id="onto_candidate_binding",
        ontology_id="ontology",
        revision=1,
        status="active",
        content_hash="sha256:candidate-binding",
        graph_json={
            "nodes": [
                {"id": "projects", "type": "scene", "name": "项目场景"},
                {"id": "project.revenue", "type": "metric", "name": "项目收入"},
            ],
            "edges": [
                {
                    "id": "candidate_binding",
                    "source": "projects",
                    "target": "project.revenue",
                    "type": "scene_binding",
                    "name": "待审核绑定",
                    "data": {
                        "semantic_key": "project.revenue",
                        "status": "candidate",
                    },
                }
            ],
        },
        prompt_projection_json={"entities": [], "metrics": [], "edges": []},
    )

    projection = relevant_ontology_projection(
        snapshot,
        scene_ids=["projects"],
        result_columns=[
            {
                "key": "project.revenue",
                "semantic_key": "project.revenue",
                "type": "metric",
                "scene_id": "projects",
            }
        ],
    )

    assert projection["matched_bindings"] == []
    assert projection["unmatched_columns"] == ["project.revenue"]


def test_metric_id_collision_without_binding_stays_unmatched():
    snapshot = OntologySnapshot(
        snapshot_id="onto_unbound",
        ontology_id="ontology",
        revision=1,
        status="active",
        content_hash="sha256:unbound",
        graph_json={
            "nodes": [
                {"id": "revenue", "type": "metric", "name": "收入"},
            ],
            "edges": [],
        },
        prompt_projection_json={
            "entities": [],
            "metrics": [{"id": "revenue", "name": "收入", "data": {}}],
            "edges": [],
            "analysis_dimensions": [],
            "metric_relations": [],
            "cross_scene_analysis_paths": [],
            "result_analysis_rules": [],
        },
    )

    projection = relevant_ontology_projection(
        snapshot,
        "收入",
        result_columns=[{"key": "revenue", "type": "metric"}],
    )

    assert projection["matched_bindings"] == []
    assert projection["unmatched_columns"] == ["revenue"]


def test_result_binding_can_match_exact_snapshot_scoped_field_lineage():
    snapshot = OntologySnapshot(
        snapshot_id="onto_field_binding",
        ontology_id="ontology",
        revision=2,
        status="active",
        content_hash="sha256:field-binding",
        graph_json={
            "nodes": [
                {"id": "finance", "type": "scene", "name": "财务场景"},
                {"id": "finance.payment_amount", "type": "metric", "name": "支付金额"},
            ],
            "edges": [
                {
                    "id": "payment_binding",
                    "source": "finance",
                    "target": "finance.payment_amount",
                    "type": "scene_binding",
                    "name": "字段绑定",
                    "data": {
                        "semantic_key": "finance.payment_amount",
                        "scene_snapshot_id": "scene_finance_v3",
                        "field_ref": "finance@scene_finance_v3:paid_amount",
                    },
                }
            ],
        },
        prompt_projection_json={
            "entities": [],
            "metrics": [
                {
                    "id": "finance.payment_amount",
                    "name": "支付金额",
                    "data": {
                        "field_ref": "finance@scene_finance_v3:paid_amount"
                    },
                }
            ],
            "edges": [
                {
                    "id": "payment_binding",
                    "source": "finance",
                    "target": "finance.payment_amount",
                    "type": "scene_binding",
                    "name": "字段绑定",
                    "data": {
                        "semantic_key": "finance.payment_amount",
                        "scene_snapshot_id": "scene_finance_v3",
                        "field_ref": "finance@scene_finance_v3:paid_amount",
                    },
                }
            ],
            "analysis_dimensions": [],
            "metric_relations": [],
            "cross_scene_analysis_paths": [],
            "result_analysis_rules": [],
        },
    )

    projection = relevant_ontology_projection(
        snapshot,
        scene_ids=["finance"],
        result_columns=[
            {
                "key": "amount_total",
                "semantic_key": "unmapped_alias",
                "scene_id": "finance",
                "scene_snapshot_id": "scene_finance_v3",
                "type": "metric",
            }
        ],
        result_lineage=[
            {
                "result_key": "amount_total",
                "field_ref": "finance@scene_finance_v3:paid_amount",
                "resolved": True,
            }
        ],
    )

    assert projection["matched_bindings"][0]["matched_by"] == "field_ref"
    assert projection["unmatched_columns"] == []
    assert projection["retrieval_trace"][0]["status"] == "matched"


def test_scene_semantic_keys_include_business_binding_aliases():
    scene = _scene_snapshot("snap_project_v2").model_copy(
        update={"scene_id": "project_analysis", "revision_id": "2"}
    )
    semantic_md = _project_semantic_markdown_without_query_model()

    keys = scene_semantic_keys_from_source(
        {"snapshot": scene, "semantic_md": semantic_md}
    )

    assert {
        "contract.contract_amount",
        "contract.contract_paid_amount",
        "contract.budget",
    }.issubset(keys)
    assert not {
        "project.count",
        "project.contract_paid_ratio",
        "project.contract_unpaid_balance",
        "project.amount_missing_count",
        "project.date_missing_count",
    }.intersection(keys)


def test_rules_generator_keeps_same_named_entities_in_domain_namespaces():
    semantic_template = """---
schema_version: "1"
scene_id: {scene_id}
name: 项目分析
domain_id: {domain_id}
data_source: ecology
view: dbo.vw_projects
dimensions:
  - key: project_id
    field: project_id
metrics:
  - key: project_amount
    field: project_amount
    aggregation: sum
time:
  field: updated_at
  required: false
---

# 项目分析

## 业务语义

项目是业务管理对象。
"""
    sources = []
    for scene_id, domain_id in (
        ("project_analysis", "project"),
        ("finance_project_analysis", "finance"),
    ):
        snapshot = _scene_snapshot(f"snap_{scene_id}").model_copy(
            update={"scene_id": scene_id}
        )
        sources.append(
            {
                "snapshot": snapshot,
                "semantic_md": semantic_template.format(
                    scene_id=scene_id, domain_id=domain_id
                ),
            }
        )

    markdown = generate_ontology_markdown_from_scene_sources(sources, revision=2)
    compilation = OntologyMarkdownParser().compile(markdown)
    entity_ids = {node.id for node in compilation.graph.nodes if node.type == "entity"}
    metric_ids = {node.id for node in compilation.graph.nodes if node.type == "metric"}

    assert "project.project" in entity_ids
    assert "finance.project" in entity_ids
    assert "project.project.amount" in metric_ids
    assert "finance.project.amount" in metric_ids
    assert compilation.valid


def test_publish_validation_accepts_business_scene_binding_aliases():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")
    scene = _scene_snapshot("snap_project_v2").model_copy(
        update={"scene_id": "project_analysis", "revision_id": "2"}
    )
    semantic_md = _project_semantic_markdown_without_query_model().replace(
        "view: sync.pcm_project_info\n---",
        """view: sync.pcm_project_info
metrics:
  - key: count
    name: 项目数量
    field: project_id
    aggregation: count_distinct
  - key: contract_amount
    name: 合同金额
    field: project_contract_amount
    aggregation: sum
  - key: contract_paid_ratio
    name: 合同已付比例
    field: contract_paid_ratio
    aggregation: none
---""",
    )
    source = {
        "snapshot": scene,
        "semantic_md": semantic_md,
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

    _approve_revision_for_test(service, saved.revision, [source])
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

    assert "| contract_amount | 合同金额 |" not in markdown
    assert not markdown.lstrip().startswith("---")
    assert "草稿" not in markdown
    assert "人工确认" not in markdown
    assert "| ID | 状态 | 关系类型 | 左侧场景 |" in markdown
    assert "| contract_analysis | contract_amount | contract_amount |" not in markdown
    assert "| contract_analysis | contract | contract |" not in markdown


def test_scene_ontology_draft_does_not_infer_metrics_from_dictionary_fields():
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

    assert "project_contract_amount | 项目合同金额" not in markdown
    assert "contract_paid_amount | 合同已付金额" not in markdown
    assert "| project_analysis | project_contract_amount |" not in markdown


def test_ontology_draft_preserves_custom_dictionary_semantic_key_and_metric_contract():
    scene = _scene_snapshot("snap_finance_v1").model_copy(
        update={"scene_id": "finance_analysis"}
    )
    semantic_md = """---
schema_version: "1"
scene_id: finance_analysis
name: 财务分析
domain_id: finance
data_source: dataman_data
view: sync.vw_contract_payment
metrics:
  - key: paid_amount
    name: 合同支付金额
    field: paid_amount
    aggregation: sum
---
<!-- dataman:document=data-dictionary -->
# 数据字典

| 字段 | 业务名称 | 语义键 | 单位 | 聚合方式 | 粒度 | 时间依据 |
| --- | --- | --- | --- | --- | --- | --- |
| paid_amount | 合同支付金额 | finance.contract.paid_amount | 元 | sum | 合同 | payment_date |

<!-- dataman:document=business-semantics -->
# 业务语义说明

合同支付金额按实际支付日期累计。
"""

    markdown = generate_ontology_markdown_from_scene_sources(
        [{"snapshot": scene, "semantic_md": semantic_md}], revision=1
    )
    keys = scene_semantic_keys_from_source(
        {"snapshot": scene, "semantic_md": semantic_md}
    )

    assert (
        "| finance_analysis | finance.contract.paid_amount | "
        "finance.contract.paid_amount |"
    ) in markdown
    assert "| finance.contract.paid_amount | 合同支付金额 | finance |" in markdown
    assert "## 指标来源追溯" in markdown
    assert "| 元 | sum | 合同 | payment_date |" in markdown
    assert "finance.contract.paid_amount" in keys
    compilation = OntologyMarkdownParser().compile(markdown)
    paid_amount = next(
        node
        for node in compilation.graph.nodes
        if node.id == "finance.contract.paid_amount"
    )
    assert paid_amount.data["source_metric_refs"][0]["metric_id"] == "paid_amount"


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

    result = await OntologyLLMGenerator(generate).generate([_llm_source()], revision=1)

    assert result.mode == "llm"
    assert result.entity_count == 1
    assert "## 指标关系" in result.markdown
    assert "## 指标来源追溯" in result.markdown
    assert "合同是收入确认和履约约束的核心对象" in result.markdown
    assert not result.markdown.lstrip().startswith("---")
    assert "草稿" not in result.markdown
    assert "待管理员确认事项" not in result.markdown
    compilation = OntologyMarkdownParser().compile(result.markdown)
    assert compilation.valid
    assert compilation.prompt_projection["analysis_dimensions"]
    assert compilation.prompt_projection["result_analysis_rules"]
    metric = next(node for node in compilation.graph.nodes if node.type == "metric")
    assert metric.data["source_metric_refs"][0]["metric_id"] == "contract_amount"


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


def test_llm_metric_must_be_explicitly_named_in_semantic_document():
    extraction = _scene_extraction()
    extraction["metrics"][0]["name"] = "合同支付率"

    with pytest.raises(OntologyLLMValidationError) as exc_info:
        validate_scene_extraction(
            SceneOntologyExtraction.model_validate(extraction),
            _llm_source(),
        )

    assert exc_info.value.code == "ONTOLOGY_METRIC_SOURCE_INVALID"


def test_llm_metric_cannot_be_supported_by_dictionary_evidence():
    extraction = _scene_extraction()
    extraction["metrics"][0]["evidence"][0]["quote"] = "amount 合同金额 decimal"

    with pytest.raises(OntologyLLMValidationError) as exc_info:
        validate_scene_extraction(
            SceneOntologyExtraction.model_validate(extraction),
            _llm_source(),
        )

    assert exc_info.value.code == "ONTOLOGY_METRIC_SOURCE_INVALID"


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

    result = await OntologyLLMGenerator(generate).generate([_llm_source()], revision=1)

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
合同金额指合同签约金额。
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
    metric_evidence = {
        "scene_id": "contract_analysis",
        "quote": "合同金额指合同签约金额。",
        "section": "语义文档",
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
                "evidence": [metric_evidence],
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
    metric_evidence = {
        "scene_id": "contract_analysis",
        "quote": "合同金额指合同签约金额。",
        "section": "语义文档",
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
                "evidence": [metric_evidence],
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
