import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dbgpt_app.scene.ask_data.api import ontology as ontology_api
from dbgpt_app.scene.ask_data.api import scenes as scenes_api
from dbgpt_app.scene.ask_data.models.entities import RevisionStatus
from dbgpt_app.scene.ask_data.models.repositories import InMemorySceneRepository
from dbgpt_app.scene.ask_data.ontology.llm_generator import OntologyLLMGenerator
from dbgpt_app.scene.ask_data.ontology.repository import InMemoryOntologyRepository
from dbgpt_app.scene.ask_data.ontology.service import OntologyLifecycleService
from dbgpt_app.scene.ask_data.schemas.snapshot import (
    Snapshot,
    SnapshotSourceHashes,
    SnapshotStatus,
)
from dbgpt_app.scene.ask_data.security import AskDataPrincipal
from dbgpt_app.scene.ask_data.snapshot.service import InMemorySnapshotService


def test_ontology_document_preview_and_publish_api():
    app = FastAPI()
    app.include_router(ontology_api.router)
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    previous_service = ontology_api.get_ontology_service()
    ontology_api.configure_ontology_service(service)
    app.dependency_overrides[ontology_api.get_principal] = lambda: AskDataPrincipal(
        user_id="anonymous"
    )
    try:
        client = TestClient(app)
        draft = client.get("/api/v1/ask-data/ontology/draft")
        assert draft.status_code == 200
        revision = draft.json()["data"]["revision"]
        markdown = draft.json()["data"]["markdown"]

        preview = client.post(
            "/api/v1/ask-data/ontology/compile-preview",
            json={"markdown": markdown},
        )
        assert preview.status_code == 200
        assert preview.json()["data"]["valid"]

        compile_check = client.post(
            "/api/v1/ask-data/ontology/compile",
            json={"markdown": markdown},
        )
        assert compile_check.status_code == 200
        assert compile_check.json()["data"]["valid"]

        source_manifest = client.get(
            "/api/v1/ask-data/ontology/draft/source-manifest"
        )
        assert source_manifest.status_code == 200
        assert source_manifest.json()["data"]["source_type"] == "active_scenes"

        generated = client.post(
            "/api/v1/ask-data/ontology/draft/from-active-scenes",
            json={"expected_revision": revision, "apply": False},
        )
        assert generated.status_code == 200

        saved = client.put(
            "/api/v1/ask-data/ontology/draft",
            json={"markdown": markdown, "expected_revision": revision},
        )
        assert saved.status_code == 200

        built = client.post(
            f"/api/v1/ask-data/ontology/revisions/{revision}/build-snapshot"
        )
        assert built.status_code == 200
        snapshot_id = built.json()["data"]["snapshot_id"]

        activated = client.post(
            f"/api/v1/ask-data/ontology/snapshots/{snapshot_id}/activate"
        )
        assert activated.status_code == 200
        assert activated.json()["data"]["status"] == "active"

        latest = client.get("/api/v1/ask-data/ontology/snapshot/latest")
        assert latest.status_code == 200
        assert latest.json()["data"]["snapshot_id"] == snapshot_id
    finally:
        ontology_api.configure_ontology_service(previous_service)


def test_generate_draft_defaults_to_llm_and_fails_when_unavailable():
    app = FastAPI()
    app.include_router(ontology_api.router)
    previous_service = ontology_api.get_ontology_service()
    ontology_api.configure_ontology_service(
        OntologyLifecycleService(InMemoryOntologyRepository())
    )
    app.dependency_overrides[ontology_api.get_principal] = lambda: AskDataPrincipal(
        user_id="admin", roles=frozenset({"admin"})
    )
    try:
        client = TestClient(app)
        draft = client.get("/api/v1/ask-data/ontology/draft")
        generated = client.post(
            "/api/v1/ask-data/ontology/draft/from-active-scenes",
            json={"expected_revision": draft.json()["data"]["revision"], "apply": True},
        )

        assert generated.status_code == 400
        assert generated.json()["detail"]["code"] == "ONTOLOGY_LLM_UNAVAILABLE"
    finally:
        ontology_api.configure_ontology_service(previous_service)


def test_generate_draft_from_active_scenes_uses_configured_scene_services():
    app = FastAPI()
    app.include_router(ontology_api.router)
    ontology_service = OntologyLifecycleService(InMemoryOntologyRepository())
    previous_ontology_service = ontology_api.get_ontology_service()
    previous_scene_state = {
        "repository": scenes_api._repository,
        "service": scenes_api._service,
        "snapshot_service": scenes_api._snapshot_service,
        "publish_service": scenes_api._publish_service,
        "run_service": scenes_api._run_service,
        "router_agent": scenes_api._router_agent,
        "audit_repository": scenes_api._audit_repository,
        "authorizer": scenes_api._authorizer,
        "idempotency_repository": scenes_api._idempotency_repository,
        "principal_resolver": scenes_api._principal_resolver,
    }
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
| 1 | `project_contract_amount` | 项目合同金额 | `numeric(18,2)` | 合同金额 |

<!-- dataman:document=business-semantics -->
# 业务语义说明

一行表示一个信息化项目。
"""
    scene_repository = InMemorySceneRepository()
    snapshot_service = InMemorySnapshotService()
    _, revision = scene_repository.create_scene(
        scene_id="project_analysis",
        name="信息化项目数据查询",
        description="项目合同分析",
        data_source_name="dataman_data",
        view_name="sync.pcm_project_info",
        semantic_md=semantic_md,
        created_by="admin",
    )
    scene_repository.save_revision(
        revision.model_copy(update={"status": RevisionStatus.READY})
    )
    snapshot = Snapshot(
        snapshot_id="snap_project_v1",
        scene_id="project_analysis",
        revision_id="1",
        status=SnapshotStatus.READY,
        content_hash="sha256:scene",
        source_hashes=SnapshotSourceHashes(
            semantic_hash="sha256:semantic",
            schema_hash="sha256:schema",
            knowledge_hash="sha256:knowledge",
        ),
        routing_projection={"name": "信息化项目数据查询"},
        runtime_config={"documents": {"semantic_md": semantic_md}},
    )
    snapshot_service.save_ready(snapshot)
    active = snapshot_service.activate(snapshot.snapshot_id)
    scene_repository.activate("project_analysis", 1, active)

    ontology_api.configure_ontology_service(ontology_service)
    scenes_api.configure_ask_data_services(
        repository=scene_repository,
        snapshot_service=snapshot_service,
        run_service=None,
    )
    app.dependency_overrides[ontology_api.get_principal] = lambda: AskDataPrincipal(
        user_id="admin", roles=frozenset({"admin"})
    )
    try:
        client = TestClient(app)
        overview = client.get("/api/v1/ask-data/ontology")
        assert overview.status_code == 200
        assert overview.json()["data"]["source_scenes"] == [
            {
                "scene_id": "project_analysis",
                "scene_name": "信息化项目数据查询",
                "snapshot_id": "snap_project_v1",
                "revision_id": "1",
                "semantic_md_hash": "sha256:semantic",
            }
        ]

        draft = client.get("/api/v1/ask-data/ontology/draft")
        revision_id = draft.json()["data"]["revision"]
        generated = client.post(
            "/api/v1/ask-data/ontology/draft/from-active-scenes",
            json={
                "expected_revision": revision_id,
                "apply": True,
                "mode": "rules",
            },
        )

        assert generated.status_code == 200
        generation = generated.json()["data"]["generation"]
        assert generation["mode"] == "rules"
        assert "markdown" not in generation
        assert "raw" not in generation
        markdown = generated.json()["data"]["revision"]["markdown"]
        assert not markdown.lstrip().startswith("---")
        assert "草稿" not in markdown
        assert "人工确认" not in markdown
        assert "| project | 项目 |" in markdown
        assert "| project_contract_amount | 项目合同金额 | project | 元 |" in markdown
        assert generated.json()["data"]["revision"]["source_scene_snapshots"] == [
            {
                "scene_id": "project_analysis",
                "snapshot_id": "snap_project_v1",
                "revision_id": "1",
                "semantic_md_hash": "sha256:semantic",
            }
        ]
    finally:
        ontology_api.configure_ontology_service(previous_ontology_service)
        scenes_api._repository = previous_scene_state["repository"]
        scenes_api._service = previous_scene_state["service"]
        scenes_api._snapshot_service = previous_scene_state["snapshot_service"]
        scenes_api._publish_service = previous_scene_state["publish_service"]
        scenes_api._run_service = previous_scene_state["run_service"]
        scenes_api._router_agent = previous_scene_state["router_agent"]
        scenes_api._audit_repository = previous_scene_state["audit_repository"]
        scenes_api._authorizer = previous_scene_state["authorizer"]
        scenes_api._idempotency_repository = previous_scene_state[
            "idempotency_repository"
        ]
        scenes_api._principal_resolver = previous_scene_state["principal_resolver"]


def test_llm_generate_uses_snapshot_semantic_md_when_revision_lookup_fails():
    app = FastAPI()
    app.include_router(ontology_api.router)
    previous_ontology_service = ontology_api.get_ontology_service()
    previous_scene_state = {
        "repository": scenes_api._repository,
        "service": scenes_api._service,
        "snapshot_service": scenes_api._snapshot_service,
        "publish_service": scenes_api._publish_service,
        "run_service": scenes_api._run_service,
        "router_agent": scenes_api._router_agent,
        "audit_repository": scenes_api._audit_repository,
        "authorizer": scenes_api._authorizer,
        "idempotency_repository": scenes_api._idempotency_repository,
        "principal_resolver": scenes_api._principal_resolver,
    }
    semantic_md = """---
schema_version: "1"
scene_id: project_analysis
name: 项目分析
description: 查询项目合同金额。
data_source: dataman_data
view: sync.pcm_project_info
metrics:
  - key: project_contract_amount
    name: 项目合同金额
    field: project_contract_amount
    aggregation: sum
    unit: 元
---
<!-- dataman:document=business-semantics -->
# 业务语义说明

一行表示一个项目。
"""
    snapshot_service = InMemorySnapshotService()
    snapshot = Snapshot(
        snapshot_id="snap_project_runtime_only",
        scene_id="project_analysis",
        revision_id="1",
        status=SnapshotStatus.READY,
        content_hash="sha256:scene",
        source_hashes=SnapshotSourceHashes(
            semantic_hash="sha256:semantic",
            schema_hash="sha256:schema",
            knowledge_hash="sha256:knowledge",
        ),
        routing_projection={"name": "项目分析"},
        runtime_config={"documents": {"semantic_md": semantic_md}},
    )
    snapshot_service.save_ready(snapshot)
    snapshot_service.activate(snapshot.snapshot_id)
    responses = iter(
        [
            json.dumps(_project_scene_extraction()),
            json.dumps(_project_global_synthesis()),
        ]
    )

    async def generate(_: str) -> str:
        return next(responses)

    ontology_api.configure_ontology_service(
        OntologyLifecycleService(
            InMemoryOntologyRepository(),
            llm_generator=OntologyLLMGenerator(generate),
        )
    )
    scenes_api.configure_ask_data_services(
        repository=InMemorySceneRepository(),
        snapshot_service=snapshot_service,
        run_service=None,
    )
    app.dependency_overrides[ontology_api.get_principal] = lambda: AskDataPrincipal(
        user_id="admin", roles=frozenset({"admin"})
    )
    try:
        client = TestClient(app)
        draft = client.get("/api/v1/ask-data/ontology/draft")
        generated = client.post(
            "/api/v1/ask-data/ontology/draft/from-active-scenes",
            json={
                "expected_revision": draft.json()["data"]["revision"],
                "apply": True,
                "mode": "llm",
            },
        )

        assert generated.status_code == 200
        data = generated.json()["data"]
        markdown = data["revision"]["markdown"]
        assert data["generation"]["mode"] == "llm"
        assert "项目是经营分析的核心载体" in markdown
        assert not markdown.lstrip().startswith("---")
        assert "草稿" not in markdown
        assert "待管理员确认事项" not in markdown
        assert data["revision"]["source_scene_snapshots"] == [
            {
                "scene_id": "project_analysis",
                "snapshot_id": "snap_project_runtime_only",
                "revision_id": "1",
                "semantic_md_hash": "sha256:semantic",
            }
        ]
    finally:
        ontology_api.configure_ontology_service(previous_ontology_service)
        scenes_api._repository = previous_scene_state["repository"]
        scenes_api._service = previous_scene_state["service"]
        scenes_api._snapshot_service = previous_scene_state["snapshot_service"]
        scenes_api._publish_service = previous_scene_state["publish_service"]
        scenes_api._run_service = previous_scene_state["run_service"]
        scenes_api._router_agent = previous_scene_state["router_agent"]
        scenes_api._audit_repository = previous_scene_state["audit_repository"]
        scenes_api._authorizer = previous_scene_state["authorizer"]
        scenes_api._idempotency_repository = previous_scene_state[
            "idempotency_repository"
        ]
        scenes_api._principal_resolver = previous_scene_state["principal_resolver"]


def _project_scene_extraction() -> dict:
    evidence = {
        "scene_id": "project_analysis",
        "quote": "一行表示一个项目。",
        "section": "业务语义说明",
    }
    return {
        "scene_id": "project_analysis",
        "scene_name": "项目分析",
        "entities": [
            {
                "local_id": "project",
                "name": "项目",
                "aliases": [],
                "definition": "项目是业务经营活动的载体。",
                "business_role": "项目是经营分析的核心载体。",
                "entity_type": "core_business_object",
                "evidence": [evidence],
            }
        ],
        "metrics": [
            {
                "local_id": "project_contract_amount",
                "name": "项目合同金额",
                "aliases": [],
                "unit": "元",
                "formula": None,
                "owner_entity_local_id": "project",
                "semantic_key": "project_contract_amount",
                "analysis_meaning": "衡量项目签约规模。",
                "interpretation": "金额越高代表项目合同规模越大。",
                "positive_direction": "depends",
                "common_anomalies": ["金额为空或为零"],
                "recommended_dimensions": ["部门", "负责人"],
                "evidence": [evidence],
            }
        ],
        "relations": [],
        "analysis_capabilities": [],
        "join_key_candidates": [],
        "clarification_rules": [],
        "quality_warnings": [],
    }


def _project_global_synthesis() -> dict:
    evidence = {
        "scene_id": "project_analysis",
        "quote": "一行表示一个项目。",
        "section": "业务语义说明",
    }
    return {
        "entities": [
            {
                "id": "project",
                "name": "项目",
                "aliases": [],
                "definition": "项目是业务经营活动的载体。",
                "business_analysis_role": "项目是经营分析的核心载体。",
                "source_scenes": ["project_analysis"],
                "evidence": [evidence],
            }
        ],
        "relations": [],
        "metrics": [
            {
                "id": "project_contract_amount",
                "name": "项目合同金额",
                "aliases": [],
                "entity_id": "project",
                "unit": "元",
                "formula": None,
                "analysis_meaning": "衡量项目签约规模。",
                "interpretation": "金额越高代表项目合同规模越大。",
                "positive_direction": "depends",
                "common_anomalies": ["金额为空或为零"],
                "recommended_dimensions": ["部门", "负责人"],
                "related_metrics": [],
                "source_scenes": ["project_analysis"],
                "scene_bindings": [
                    {
                        "scene": "project_analysis",
                        "semantic_key": "project_contract_amount",
                    }
                ],
                "conflict_note": None,
                "evidence": [evidence],
            }
        ],
        "metric_relations": [],
        "analysis_dimensions": [
            {
                "id": "dim_department",
                "name": "部门",
                "applicable_entities": ["project"],
                "applicable_metrics": ["project_contract_amount"],
                "analysis_usage": "按部门拆解项目合同金额。",
                "caveats": [],
            }
        ],
        "cross_scene_analysis_paths": [],
        "result_analysis_rules": [
            {
                "id": "rule_amount",
                "trigger": "当结果包含金额类指标",
                "guidance": "优先分析总量、结构占比和异常值。",
                "applies_to_entities": ["project"],
                "applies_to_metrics": ["project_contract_amount"],
            }
        ],
        "clarification_rules": ["当项目问题未说明金额或进度口径时先澄清。"],
        "unresolved_questions": [],
    }
