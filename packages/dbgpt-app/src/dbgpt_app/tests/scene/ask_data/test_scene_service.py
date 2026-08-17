from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dbgpt_app.scene.ask_data.models import (
    InMemorySceneRepository,
    RevisionStatus,
    SceneRepositoryError,
    SceneStatus,
)
from dbgpt_app.scene.ask_data.scene.service import SceneLifecycleService
from dbgpt_app.scene.ask_data.schemas.snapshot import (
    Snapshot,
    SnapshotSourceHashes,
    SnapshotStatus,
)


def _snapshot(status: SnapshotStatus = SnapshotStatus.ACTIVE) -> Snapshot:
    return Snapshot(
        snapshot_id="snap_1",
        scene_id="contracts",
        revision_id="1",
        status=status,
        content_hash="sha256:content",
        source_hashes=SnapshotSourceHashes(
            semantic_hash="sha256:semantic",
            schema_hash="sha256:schema",
            knowledge_hash="sha256:knowledge",
        ),
        routing_projection={"scene_id": "contracts"},
        runtime_config={"scene_id": "contracts"},
        created_at=datetime.now(timezone.utc),
    )


def _service() -> SceneLifecycleService:
    return SceneLifecycleService(InMemorySceneRepository())


def test_create_update_and_activate_scene_revision():
    service = _service()
    scene, revision = service.create(
        scene_id="contracts",
        name="Contracts",
        description="Contract analysis",
        data_source_name="ecology",
        view_name="dbo.vw_contracts",
        semantic_md="---\nscene_id: contracts\n---",
        created_by="admin",
    )

    assert scene.status == SceneStatus.DRAFT
    assert revision.revision == 1
    service.mark_validating("contracts", 1)
    ready = service.mark_ready(
        "contracts",
        1,
        parsed_config_json={"scene_id": "contracts"},
        view_schema_json={"schema_hash": "sha256:schema"},
        semantic_hash="sha256:semantic",
        schema_hash="sha256:schema",
        knowledge_hash="sha256:knowledge",
    )
    active_scene, active_revision = service.activate("contracts", 1, _snapshot())

    assert ready.status == RevisionStatus.READY
    assert active_scene.status == SceneStatus.ACTIVE
    assert active_scene.current_snapshot_id == "snap_1"
    assert active_revision.status == RevisionStatus.ACTIVE


def test_update_active_scene_creates_new_draft_without_changing_active_pointer():
    service = _service()
    service.create(
        scene_id="contracts",
        name="Contracts",
        description="Contract analysis",
        data_source_name="ecology",
        view_name="dbo.vw_contracts",
        semantic_md="draft",
        created_by="admin",
    )
    service.mark_validating("contracts", 1)
    service.mark_ready(
        "contracts",
        1,
        parsed_config_json={},
        view_schema_json={},
        semantic_hash="sha256:semantic",
        schema_hash="sha256:schema",
        knowledge_hash="sha256:knowledge",
    )
    service.activate("contracts", 1, _snapshot())

    scene, draft = service.update_draft(
        "contracts",
        name="Contracts v2",
        description="Updated",
        data_source_name="ecology",
        view_name="dbo.vw_contracts",
        semantic_md="new draft",
        updated_by="admin",
    )

    assert scene.active_revision == 1
    assert scene.latest_revision == 2
    assert draft.revision == 2
    assert draft.status == RevisionStatus.DRAFT


def test_scene_disable_enable_delete_and_conflict_rules():
    service = _service()
    service.create(
        scene_id="contracts",
        name="Contracts",
        description="Contract analysis",
        data_source_name="ecology",
        view_name="dbo.vw_contracts",
        semantic_md="draft",
        created_by="admin",
    )
    with pytest.raises(SceneRepositoryError, match="SCENE_ID_CONFLICT"):
        service.create(
            scene_id="contracts",
            name="Duplicate",
            description="",
            data_source_name="ecology",
            view_name="dbo.vw_contracts",
            semantic_md="draft",
            created_by="admin",
        )

    with pytest.raises(SceneRepositoryError, match="SCENE_NOT_ACTIVE"):
        service.disable("contracts")

    service.delete("contracts")
    with pytest.raises(SceneRepositoryError, match="SCENE_NOT_FOUND"):
        service.update_draft(
            "contracts",
            name="Updated",
            description="",
            data_source_name="ecology",
            view_name="dbo.vw_contracts",
            semantic_md="draft",
            updated_by="admin",
        )
