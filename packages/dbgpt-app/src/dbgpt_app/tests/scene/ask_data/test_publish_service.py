from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dbgpt_app.scene.ask_data.models import InMemorySceneRepository
from dbgpt_app.scene.ask_data.rag import InMemoryKnowledgeBackend, SceneKnowledgeManager
from dbgpt_app.scene.ask_data.scene.publish_service import (
    ScenePublishError,
    ScenePublishService,
)
from dbgpt_app.scene.ask_data.schemas.schema import ViewColumn, ViewSchema
from dbgpt_app.scene.ask_data.snapshot import InMemorySnapshotService

DOCUMENT = """---
schema_version: "1"
scene_id: contracts_publish
name: Contracts
description: Contract analysis
data_source: ecology
view: dbo.vw_contracts
agent:
  capabilities: [query contracts]
  cannot_do: [predict]
dimensions:
  - key: department
    field: department_id
    filter_operators: [eq]
metrics:
  - key: amount
    field: amount
    aggregation: sum
---
<!-- dataman:document=data-dictionary -->
# 数据字典

| 字段 | 含义 |
| --- | --- |
| department_id | 部门 |
| amount | 合同金额 |

<!-- dataman:document=business-semantics -->
# 业务语义说明

一行表示一份合同。
"""


def _schema() -> ViewSchema:
    return ViewSchema(
        dialect="mssql",
        data_source="ecology",
        view="dbo.vw_contracts",
        columns=[
            ViewColumn(name="department_id", data_type="int", normalized_type="number"),
            ViewColumn(name="amount", data_type="decimal", normalized_type="number"),
        ],
        schema_hash="sha256:schema",
        inspected_at=datetime.now(timezone.utc),
    )


def _service(*, quality_gate_passed: bool = True):
    repository = InMemorySceneRepository()
    repository.create_scene(
        scene_id="contracts_publish",
        name="Contracts",
        description="Contract analysis",
        data_source_name="ecology",
        view_name="dbo.vw_contracts",
        semantic_md=DOCUMENT,
        created_by="test",
    )
    snapshots = InMemorySnapshotService()
    knowledge = SceneKnowledgeManager(
        InMemoryKnowledgeBackend(quality_gate_passed=quality_gate_passed)
    )
    return (
        ScenePublishService(
            repository, snapshots, lambda *_: _schema(), knowledge_manager=knowledge
        ),
        repository,
        snapshots,
    )


def test_publish_service_validates_builds_and_activates():
    service, repository, snapshots = _service()

    validation = service.validate("contracts_publish", 1)
    assert validation.valid
    snapshot = service.build_snapshot("contracts_publish", 1)
    assert snapshot.status.value == "ready"
    assert repository.get_revision("contracts_publish", 1).status.value == "ready"

    active, registry_version = service.activate(
        "contracts_publish", snapshot.snapshot_id
    )

    assert active.status.value == "active"
    assert registry_version == 1
    assert (
        repository.get_scene("contracts_publish").current_snapshot_id
        == snapshot.snapshot_id
    )
    assert snapshots.active("contracts_publish").snapshot_id == snapshot.snapshot_id


def test_publish_service_rejects_wrong_snapshot_scene():
    service, _, _ = _service()
    with pytest.raises(ScenePublishError):
        service.activate("contracts_publish", "missing")


def test_publish_service_builds_internal_snapshot_without_rag_sync():
    service, repository, snapshots = _service()

    service.validate("contracts_publish", 1)
    snapshot = service.build_snapshot("contracts_publish", 1)

    assert snapshot.runtime_config["rag"]["knowledge_space"] is None
    assert snapshot.source_hashes.knowledge_hash == "sha256:none"
    assert (
        repository.get_revision("contracts_publish", 1).knowledge_hash
        == "sha256:none"
    )
    assert snapshots.get(snapshot.snapshot_id).content_hash == snapshot.content_hash


def test_publish_service_ignores_rag_quality_gate_in_scene_lifecycle():
    service, repository, _ = _service(quality_gate_passed=False)

    service.validate("contracts_publish", 1)
    snapshot = service.build_snapshot("contracts_publish", 1)

    assert snapshot.source_hashes.knowledge_hash == "sha256:none"
    assert repository.get_revision("contracts_publish", 1).status.value == "ready"


def test_rebuild_active_snapshots_replaces_legacy_snapshot_schema():
    service, repository, snapshots = _service()
    service.builder.snapshot_schema_version = "1"
    _, old_snapshot, _ = service.publish("contracts_publish", 1)

    service.builder.snapshot_schema_version = "2"
    results, registry_version = service.rebuild_active_snapshots()
    active = snapshots.active("contracts_publish")

    assert results[0]["status"] == "rebuilt"
    assert results[0]["old_snapshot_id"] == old_snapshot.snapshot_id
    assert results[0]["new_snapshot_id"] == active.snapshot_id
    assert active.schema_version == "2"
    assert active.runtime_config["schema_version"] == "2"
    assert (
        repository.get_scene("contracts_publish").current_snapshot_id
        == active.snapshot_id
    )
    assert registry_version == snapshots.registry_version


def test_rebuild_active_snapshots_is_idempotent_for_current_schema():
    service, _, snapshots = _service()
    _, active_before, _ = service.publish("contracts_publish", 1)

    results, _ = service.rebuild_active_snapshots()

    assert results[0]["status"] == "unchanged"
    assert (
        snapshots.active("contracts_publish").snapshot_id
        == active_before.snapshot_id
    )
