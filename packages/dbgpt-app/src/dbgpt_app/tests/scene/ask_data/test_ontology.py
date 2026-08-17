from datetime import datetime, timezone

from dbgpt_app.scene.ask_data.ontology import (
    DEFAULT_ONTOLOGY_MARKDOWN,
    LEGACY_DEFAULT_ONTOLOGY_MARKDOWN,
    OntologyMarkdownParser,
)
from dbgpt_app.scene.ask_data.ontology.repository import InMemoryOntologyRepository
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


def test_invalid_relation_is_rejected_before_publish():
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

    assert not result.valid
    assert any(issue.code == "UNKNOWN_RELATION_TARGET" for issue in result.issues)


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
            "metrics": [{"key": "contract_amount"}],
            "dimensions": [],
        },
        runtime_config={"rag": {}},
        created_at=datetime.now(timezone.utc),
    )


def test_scene_sync_must_be_accepted_before_publishing_active_scenes():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")
    scene = _scene_snapshot("snap_contract_v1")

    try:
        service.build_snapshot(draft.revision, [scene])
        assert False, "active scene changes must not be implicitly published"
    except ValueError as exc:
        assert getattr(exc, "code", None) == "ONTOLOGY_SCENE_SYNC_REQUIRED"

    synced = service.scene_sync(
        scene_snapshots=[scene],
        user_id="admin",
        expected_revision=draft.revision,
        apply=True,
    )
    snapshot = service.build_snapshot(synced["revision"].revision, [scene])

    assert snapshot.source_scene_snapshots[0]["snapshot_id"] == "snap_contract_v1"


def test_save_draft_rewrites_server_managed_frontmatter():
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    draft = service.draft(user_id="admin")

    saved = service.save_draft(
        markdown=draft.ontology_md.replace(
            'ontology_id: "global_business"', 'ontology_id: "not_allowed"'
        ),
        user_id="admin",
        expected_revision=draft.revision,
    )

    assert 'ontology_id: "global_business"' in saved.ontology_md
    assert 'ontology_id: "not_allowed"' not in saved.ontology_md


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
