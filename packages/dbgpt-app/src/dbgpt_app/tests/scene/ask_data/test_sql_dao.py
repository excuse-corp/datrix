from __future__ import annotations

from datetime import datetime, timezone

from dbgpt.storage.metadata import db
from dbgpt_app.scene.ask_data.models.audit import AuditEvent
from dbgpt_app.scene.ask_data.models.dao import (
    AskDataAgentRunDao,
    AskDataAuditDao,
    AskDataQueryRunDao,
    AskDataSceneDao,
    AskDataSceneRevisionDao,
    AskDataSnapshotDao,
    create_ask_data_tables,
)
from dbgpt_app.scene.ask_data.models.entities import (
    RevisionStatus,
    Scene,
    SceneRevision,
    SceneStatus,
)
from dbgpt_app.scene.ask_data.models.repositories import SqlSceneRepository
from dbgpt_app.scene.ask_data.models.run_repository import SqlRunRepository
from dbgpt_app.scene.ask_data.models.runs import AgentRun, QueryRun, RunStatus
from dbgpt_app.scene.ask_data.scene.service import SceneLifecycleService
from dbgpt_app.scene.ask_data.schemas.snapshot import (
    Snapshot,
    SnapshotSourceHashes,
    SnapshotStatus,
)
from dbgpt_app.scene.ask_data.snapshot.service import SqlSnapshotService


def test_sql_dao_round_trip(tmp_path):
    db.init_db(f"sqlite:///{tmp_path / 'askdata.db'}")
    create_ask_data_tables(db)

    scene = Scene(
        scene_id="contracts_sql",
        name="Contracts",
        description="Contract analysis",
        status=SceneStatus.DRAFT,
        latest_revision=1,
        created_by="test",
    )
    revision = SceneRevision(
        scene_id="contracts_sql",
        revision=1,
        status=RevisionStatus.READY,
        data_source_name="ecology",
        view_name="dbo.vw_contracts",
        semantic_md="---\nscene_id: contracts_sql\n---",
        parsed_config_json={"scene_id": "contracts_sql"},
        created_by="test",
    )
    snapshot = Snapshot(
        snapshot_id="snap_contracts_sql",
        scene_id="contracts_sql",
        revision_id="1",
        status=SnapshotStatus.READY,
        content_hash="sha256:content",
        source_hashes=SnapshotSourceHashes(
            semantic_hash="sha256:semantic",
            schema_hash="sha256:schema",
            knowledge_hash="sha256:knowledge",
        ),
        routing_projection={"scene_id": "contracts_sql"},
        runtime_config={"view": "dbo.vw_contracts"},
        created_at=datetime.now(timezone.utc),
    )

    AskDataSceneDao().save_scene(scene)
    AskDataSceneRevisionDao().save_revision(revision)
    AskDataSnapshotDao().save_snapshot(snapshot)

    assert AskDataSceneDao().get_scene("contracts_sql").scene_id == "contracts_sql"
    assert AskDataSceneRevisionDao().get_revision(
        "contracts_sql", 1
    ).parsed_config_json == {"scene_id": "contracts_sql"}
    loaded = AskDataSnapshotDao().get_snapshot("snap_contracts_sql")
    assert loaded.source_hashes.schema_hash == "sha256:schema"
    assert loaded.runtime_config["view"] == "dbo.vw_contracts"


def test_sql_run_dao_round_trip(tmp_path):
    db.init_db(f"sqlite:///{tmp_path / 'askdata-runs.db'}")
    create_ask_data_tables(db)
    query = QueryRun(
        query_id="qry_sql",
        request_id="req_sql",
        user_id="user_sql",
        question="total amount",
        status=RunStatus.SUCCEEDED,
        snapshot_ids=["snap_sql"],
        plan_json={"action": "execute"},
        combine_mode="separate",
    )
    agent = AgentRun(
        agent_run_id="agr_sql",
        query_id="qry_sql",
        task_id="task_sql",
        scene_id="contracts_sql",
        revision_id="1",
        snapshot_id="snap_sql",
        knowledge_space_name="askdata_contracts_sql_r1",
        status=RunStatus.SUCCEEDED,
        query_spec_hash="sha256:query",
        compiler_version="1",
        sql_hash="sha256:sql",
    )

    AskDataQueryRunDao().save_query(query)
    AskDataAgentRunDao().save_agent(agent)

    loaded_query = AskDataQueryRunDao().get_query("qry_sql")
    loaded_agent = AskDataAgentRunDao().get_agent("agr_sql")
    assert loaded_query and loaded_query.snapshot_ids == ["snap_sql"]
    assert loaded_agent and loaded_agent.sql_hash == "sha256:sql"


def test_sql_scene_repository_uses_transactional_metadata(tmp_path):
    db.init_db(f"sqlite:///{tmp_path / 'askdata-repository.db'}")
    create_ask_data_tables(db)
    repository = SqlSceneRepository(db)
    scene, revision = repository.create_scene(
        scene_id="contracts_repo",
        name="Contracts",
        description="Contract analysis",
        data_source_name="ecology",
        view_name="dbo.vw_contracts",
        semantic_md="---\nscene_id: contracts_repo\n---",
        created_by="test",
    )
    assert scene.latest_revision == 1
    assert revision.status == RevisionStatus.DRAFT
    service = SceneLifecycleService(repository)
    updated_scene, updated_revision = service.update_draft(
        "contracts_repo",
        name="Contracts v2",
        description="Updated",
        data_source_name="ecology",
        view_name="dbo.vw_contracts",
        semantic_md="---\nscene_id: contracts_repo\nname: Contracts v2\n---",
        updated_by="test",
    )
    assert updated_scene.name == "Contracts v2"
    assert updated_revision.revision == 1
    assert repository.get_scene("contracts_repo").name == "Contracts v2"


def test_sql_scene_repository_update_draft_creates_initialized_revision(tmp_path):
    db.init_db(f"sqlite:///{tmp_path / 'askdata-repository-new-draft.db'}")
    create_ask_data_tables(db)
    repository = SqlSceneRepository(db)
    service = SceneLifecycleService(repository)
    scene, revision = repository.create_scene(
        scene_id="contracts_repo_new_draft",
        name="Contracts",
        description="Contract analysis",
        data_source_name="ecology",
        view_name="dbo.vw_contracts",
        semantic_md="---\nscene_id: contracts_repo_new_draft\n---",
        created_by="test",
    )
    repository.save_revision(revision.model_copy(update={"status": RevisionStatus.READY}))

    updated_scene, updated_revision = service.update_draft(
        "contracts_repo_new_draft",
        name="Contracts v2",
        description="Updated",
        data_source_name="ecology",
        view_name="dbo.vw_contracts",
        semantic_md="---\nscene_id: contracts_repo_new_draft\nname: Contracts v2\n---",
        updated_by="test",
    )

    assert updated_scene.latest_revision == 2
    assert updated_revision.revision == 2
    assert updated_revision.status == RevisionStatus.DRAFT
    assert updated_revision.created_at is not None


def test_sql_run_repository_supports_idempotency_and_agent_lookup(tmp_path):
    db.init_db(f"sqlite:///{tmp_path / 'askdata-run-repository.db'}")
    create_ask_data_tables(db)
    repository = SqlRunRepository(db)
    query = QueryRun(
        query_id="qry_repo",
        request_id="req_repo",
        user_id="user_repo",
        question="total amount",
        status=RunStatus.RUNNING,
        idempotency_key="idem_repo",
    )
    agent = AgentRun(
        agent_run_id="agr_repo",
        query_id="qry_repo",
        task_id="task_repo",
        scene_id="contracts_repo",
        revision_id="1",
        snapshot_id="snap_repo",
        status=RunStatus.RUNNING,
    )
    repository.create_query(query)
    repository.create_agent(agent)
    assert (
        repository.find_by_idempotency("user_repo", "idem_repo").query_id
        == "qry_repo"
    )
    assert repository.agents_for_query("qry_repo")[0].agent_run_id == "agr_repo"


def test_sql_snapshot_service_activates_and_detects_drift(tmp_path):
    db.init_db(f"sqlite:///{tmp_path / 'askdata-snapshot-service.db'}")
    create_ask_data_tables(db)
    service = SqlSnapshotService(db)
    snapshot = Snapshot(
        snapshot_id="snap_service",
        scene_id="contracts_service",
        revision_id="1",
        status=SnapshotStatus.READY,
        content_hash="sha256:content",
        source_hashes=SnapshotSourceHashes(
            semantic_hash="sha256:semantic",
            schema_hash="sha256:schema",
            knowledge_hash="sha256:knowledge",
        ),
        routing_projection={"scene_id": "contracts_service"},
        runtime_config={"rag": {"knowledge_space": "askdata_contracts_service_r1"}},
        created_at=datetime.now(timezone.utc),
    )
    service.save_ready(snapshot)
    active = service.activate(snapshot.snapshot_id)
    assert active.status == SnapshotStatus.ACTIVE
    assert service.active("contracts_service").snapshot_id == snapshot.snapshot_id
    registry_version = service.registry_version

    replacement = snapshot.model_copy(
        update={
            "snapshot_id": "snap_service_rebuilt",
            "content_hash": "sha256:rebuilt",
            "source_hashes": SnapshotSourceHashes(
                semantic_hash="sha256:semantic",
                schema_hash="sha256:schema",
                knowledge_hash="sha256:none",
            ),
        }
    )
    service.save_ready(replacement)
    rebuilt = service.activate(replacement.snapshot_id)
    assert rebuilt.status == SnapshotStatus.ACTIVE
    assert service.active("contracts_service").snapshot_id == replacement.snapshot_id
    assert service.registry_version != registry_version

    assert service.check_schema_drift("contracts_service", "sha256:new-schema")
    assert service.get(replacement.snapshot_id).status == SnapshotStatus.INVALID


def test_sql_audit_dao_round_trip(tmp_path):
    db.init_db(f"sqlite:///{tmp_path / 'askdata-audit.db'}")
    create_ask_data_tables(db)
    event = AuditEvent(
        event_id="aud_sql",
        action="scene.create",
        resource_type="scene",
        resource_id="contracts_audit",
        user_id="admin",
        request_id="req_audit",
        outcome="succeeded",
        details={"revision": 1},
    )
    AskDataAuditDao().save_event(event)
    loaded = AskDataAuditDao().get_event("aud_sql")
    assert loaded and loaded.details["revision"] == 1


def test_sql_idempotency_round_trip_and_conflict(tmp_path):
    from dbgpt_app.scene.ask_data.models.idempotency import (
        IdempotencyConflictError,
        IdempotencyRecord,
        SqlIdempotencyRepository,
    )

    db.init_db(f"sqlite:///{tmp_path / 'askdata-idempotency.db'}")
    create_ask_data_tables(db)
    repository = SqlIdempotencyRepository(db)
    record = IdempotencyRecord(
        scope="user-1:build-snapshot",
        key="retry-1",
        fingerprint="sha256:payload",
        response={"status": "succeeded", "snapshot_id": "snap-1"},
    )

    repository.save(record)
    loaded = repository.get(record.scope, record.key)

    assert loaded is not None
    assert loaded.response["snapshot_id"] == "snap-1"
    try:
        repository.save(record.model_copy(update={"fingerprint": "sha256:other"}))
    except IdempotencyConflictError as exc:
        assert str(exc) == "IDEMPOTENCY_KEY_CONFLICT"
    else:
        raise AssertionError("expected an idempotency conflict")
