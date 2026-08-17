"""DAO adapters between AskData protocol models and DB-GPT metadata tables."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import inspect, text

from dbgpt.storage.metadata import BaseDao, DatabaseManager

from ..schemas.snapshot import Snapshot, SnapshotSourceHashes, SnapshotStatus
from .audit import AuditEvent
from .entities import Scene, SceneRevision
from .idempotency import IdempotencyConflictError, IdempotencyRecord
from .runs import AgentRun, QueryRun, RunStatus
from .sql_entities import (
    AskDataAgentRunEntity,
    AskDataAuditEventEntity,
    AskDataIdempotencyEntity,
    AskDataQueryRunEntity,
    AskDataSceneEntity,
    AskDataSceneRevisionEntity,
    AskDataSnapshotEntity,
)


class AskDataSceneDao(BaseDao):
    def create_model(self, scene: Scene) -> AskDataSceneEntity:
        return AskDataSceneEntity(**scene.model_dump(exclude={"soft_deleted_at"}))

    def get_scene(
        self, scene_id: str, *, include_deleted: bool = False
    ) -> Scene | None:
        with self.session() as session:
            query = session.query(AskDataSceneEntity).filter_by(scene_id=scene_id)
            if not include_deleted:
                query = query.filter(AskDataSceneEntity.soft_deleted_at.is_(None))
            entity = query.first()
            return self._to_scene(entity) if entity else None

    def save_scene(self, scene: Scene) -> Scene:
        with self.session() as session:
            entity = (
                session.query(AskDataSceneEntity)
                .filter_by(scene_id=scene.scene_id)
                .first()
            )
            values = scene.model_dump()
            if entity:
                for key, value in values.items():
                    setattr(entity, key, value)
            else:
                session.add(AskDataSceneEntity(**values))
        return scene

    def _to_scene(self, entity: AskDataSceneEntity) -> Scene:
        return Scene.model_validate(
            {
                column.name: getattr(entity, column.name)
                for column in AskDataSceneEntity.__table__.columns
                if column.name != "id"
            }
        )


class AskDataSceneRevisionDao(BaseDao):
    def save_revision(self, revision: SceneRevision) -> SceneRevision:
        with self.session() as session:
            entity = (
                session.query(AskDataSceneRevisionEntity)
                .filter_by(scene_id=revision.scene_id, revision=revision.revision)
                .first()
            )
            values = revision.model_dump()
            if entity:
                for key, value in values.items():
                    setattr(entity, key, value)
            else:
                session.add(AskDataSceneRevisionEntity(**values))
        return revision

    def get_revision(self, scene_id: str, revision: int) -> SceneRevision | None:
        with self.session() as session:
            entity = (
                session.query(AskDataSceneRevisionEntity)
                .filter_by(scene_id=scene_id, revision=revision)
                .first()
            )
            if not entity:
                return None
            return SceneRevision.model_validate(
                {
                    column.name: getattr(entity, column.name)
                    for column in AskDataSceneRevisionEntity.__table__.columns
                    if column.name != "id"
                }
            )


class AskDataSnapshotDao(BaseDao):
    def save_snapshot(self, snapshot: Snapshot) -> Snapshot:
        with self.session() as session:
            entity = (
                session.query(AskDataSnapshotEntity)
                .filter_by(snapshot_id=snapshot.snapshot_id)
                .first()
            )
            if entity is None:
                entity = (
                    session.query(AskDataSnapshotEntity)
                    .filter_by(
                        scene_id=snapshot.scene_id,
                        revision_id=snapshot.revision_id,
                    )
                    .first()
                )
            values = {
                "snapshot_id": snapshot.snapshot_id,
                "scene_id": snapshot.scene_id,
                "revision_id": snapshot.revision_id,
                "status": snapshot.status.value,
                "content_hash": snapshot.content_hash,
                "semantic_hash": snapshot.source_hashes.semantic_hash,
                "schema_hash": snapshot.source_hashes.schema_hash,
                "knowledge_hash": snapshot.source_hashes.knowledge_hash,
                "routing_projection_json": snapshot.routing_projection,
                "runtime_config_json": snapshot.runtime_config,
                "schema_version": snapshot.schema_version,
                "query_spec_version": snapshot.query_spec_version,
                "compiler_version": snapshot.compiler_version,
                "error_code": snapshot.error_code,
                "error_message": snapshot.error_message,
                "created_at": snapshot.created_at,
            }
            if entity:
                for key, value in values.items():
                    setattr(entity, key, value)
            else:
                session.add(AskDataSnapshotEntity(**values))
        return snapshot

    def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        with self.session() as session:
            entity = (
                session.query(AskDataSnapshotEntity)
                .filter_by(snapshot_id=snapshot_id)
                .first()
            )
            if not entity:
                return None
            return Snapshot(
                snapshot_id=entity.snapshot_id,
                scene_id=entity.scene_id,
                revision_id=entity.revision_id,
                status=SnapshotStatus(entity.status),
                content_hash=entity.content_hash,
                source_hashes=SnapshotSourceHashes(
                    semantic_hash=entity.semantic_hash,
                    schema_hash=entity.schema_hash,
                    knowledge_hash=entity.knowledge_hash,
                ),
                routing_projection=entity.routing_projection_json,
                runtime_config=entity.runtime_config_json,
                schema_version=entity.schema_version,
                query_spec_version=entity.query_spec_version,
                compiler_version=entity.compiler_version,
                error_code=entity.error_code,
                error_message=entity.error_message,
                created_at=entity.created_at,
            )


class AskDataQueryRunDao(BaseDao):
    def save_query(self, run: QueryRun) -> QueryRun:
        with self.session() as session:
            entity = (
                session.query(AskDataQueryRunEntity)
                .filter_by(query_id=run.query_id)
                .first()
            )
            values = {
                "query_id": run.query_id,
                "request_id": run.request_id,
                "conversation_id": run.conversation_id,
                "user_id": run.user_id,
                "entry_type": run.entry_type,
                "question": run.question,
                "status": run.status.value,
                "plan_json": run.plan_json,
                "result_json": run.result_json,
                "combine_mode": run.combine_mode,
                "ontology_snapshot_id": run.ontology_snapshot_id,
                "snapshot_ids_json": run.snapshot_ids,
                "errors_json": run.errors,
                "warnings_json": run.warnings,
                "clarification_json": run.clarification,
                "clarification_round": run.clarification_round,
                "clarification_expires_at": run.clarification_expires_at,
                "idempotency_key": run.idempotency_key,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "duration_ms": run.duration_ms,
                "created_at": run.created_at,
            }
            if entity:
                for key, value in values.items():
                    setattr(entity, key, value)
            else:
                session.add(AskDataQueryRunEntity(**values))
        return run

    def get_query(self, query_id: str) -> QueryRun | None:
        with self.session() as session:
            entity = (
                session.query(AskDataQueryRunEntity)
                .filter_by(query_id=query_id)
                .first()
            )
            if not entity:
                return None
            return QueryRun(
                query_id=entity.query_id,
                request_id=entity.request_id,
                conversation_id=entity.conversation_id,
                user_id=entity.user_id,
                entry_type=entity.entry_type or "main_agent",
                question=entity.question,
                status=RunStatus(entity.status),
                plan_json=entity.plan_json,
                result_json=entity.result_json,
                combine_mode=entity.combine_mode,
                ontology_snapshot_id=entity.ontology_snapshot_id,
                snapshot_ids=entity.snapshot_ids_json or [],
                errors=entity.errors_json or [],
                warnings=entity.warnings_json or [],
                clarification=entity.clarification_json,
                clarification_round=entity.clarification_round or 0,
                clarification_expires_at=entity.clarification_expires_at,
                idempotency_key=entity.idempotency_key,
                started_at=entity.started_at,
                finished_at=entity.finished_at,
                duration_ms=entity.duration_ms,
                created_at=entity.created_at,
            )


class AskDataAgentRunDao(BaseDao):
    def save_agent(self, run: AgentRun) -> AgentRun:
        with self.session() as session:
            entity = (
                session.query(AskDataAgentRunEntity)
                .filter_by(agent_run_id=run.agent_run_id)
                .first()
            )
            values = run.model_dump()
            values.update({"status": run.status.value})
            if entity:
                for key, value in values.items():
                    setattr(entity, key, value)
            else:
                session.add(AskDataAgentRunEntity(**values))
        return run

    def get_agent(self, agent_run_id: str) -> AgentRun | None:
        with self.session() as session:
            entity = (
                session.query(AskDataAgentRunEntity)
                .filter_by(agent_run_id=agent_run_id)
                .first()
            )
            if not entity:
                return None
            values = {
                column.name: getattr(entity, column.name)
                for column in AskDataAgentRunEntity.__table__.columns
                if column.name != "id"
            }
            values["status"] = RunStatus(values["status"])
            return AgentRun.model_validate(values)


class AskDataAuditDao(BaseDao):
    def save_event(self, event: AuditEvent) -> AuditEvent:
        with self.session() as session:
            existing = (
                session.query(AskDataAuditEventEntity)
                .filter_by(event_id=event.event_id)
                .first()
            )
            values = {
                "event_id": event.event_id,
                "action": event.action,
                "resource_type": event.resource_type,
                "resource_id": event.resource_id,
                "user_id": event.user_id,
                "request_id": event.request_id,
                "outcome": event.outcome,
                "error_code": event.error_code,
                "details_json": event.details,
                "created_at": event.created_at,
            }
            if existing:
                for key, value in values.items():
                    setattr(existing, key, value)
            else:
                session.add(AskDataAuditEventEntity(**values))
        return event

    def get_event(self, event_id: str) -> AuditEvent | None:
        with self.session(commit=False) as session:
            entity = (
                session.query(AskDataAuditEventEntity)
                .filter_by(event_id=event_id)
                .first()
            )
            if not entity:
                return None
            return AuditEvent(
                event_id=entity.event_id,
                action=entity.action,
                resource_type=entity.resource_type,
                resource_id=entity.resource_id,
                user_id=entity.user_id,
                request_id=entity.request_id,
                outcome=entity.outcome,
                error_code=entity.error_code,
                details=entity.details_json or {},
                created_at=entity.created_at,
            )


class AskDataIdempotencyDao(BaseDao):
    def get(self, scope: str, key: str) -> IdempotencyRecord | None:
        with self.session(commit=False) as session:
            entity = (
                session.query(AskDataIdempotencyEntity)
                .filter_by(scope=scope, key=key)
                .first()
            )
            if not entity:
                return None
            expires_at = entity.expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                return None
            return IdempotencyRecord(
                scope=entity.scope,
                key=entity.key,
                fingerprint=entity.fingerprint,
                response=entity.response_json or {},
                created_at=entity.created_at.replace(tzinfo=timezone.utc),
                expires_at=expires_at,
            )

    def save(self, record: IdempotencyRecord) -> IdempotencyRecord:
        with self.session() as session:
            existing = (
                session.query(AskDataIdempotencyEntity)
                .filter_by(scope=record.scope, key=record.key)
                .first()
            )
            if existing and existing.fingerprint != record.fingerprint:
                raise IdempotencyConflictError("IDEMPOTENCY_KEY_CONFLICT")
            values = {
                "scope": record.scope,
                "key": record.key,
                "fingerprint": record.fingerprint,
                "response_json": record.response,
                "created_at": record.created_at,
                "expires_at": record.expires_at,
            }
            if existing:
                for key, value in values.items():
                    setattr(existing, key, value)
            else:
                session.add(AskDataIdempotencyEntity(**values))
        return record


def create_ask_data_tables(db_manager: DatabaseManager) -> None:
    db_manager.metadata.create_all(
        db_manager.engine,
        tables=[
            AskDataSceneEntity.__table__,
            AskDataSceneRevisionEntity.__table__,
            AskDataSnapshotEntity.__table__,
            AskDataQueryRunEntity.__table__,
            AskDataAgentRunEntity.__table__,
            AskDataAuditEventEntity.__table__,
            AskDataIdempotencyEntity.__table__,
        ],
    )
    inspector = inspect(db_manager.engine)
    query_run_columns = {
        column["name"]
        for column in inspector.get_columns(AskDataQueryRunEntity.__tablename__)
    }
    if "entry_type" not in query_run_columns:
        with db_manager.engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE ask_data_query_run "
                    "ADD entry_type VARCHAR(32) NOT NULL DEFAULT 'main_agent'"
                )
            )
    if "result_json" not in query_run_columns:
        with db_manager.engine.begin() as connection:
            result_type = (
                "NVARCHAR(MAX)"
                if db_manager.engine.dialect.name.lower() == "mssql"
                else "JSON"
            )
            connection.execute(
                text(
                    "ALTER TABLE ask_data_query_run "
                    f"ADD result_json {result_type}"
                )
            )
    if "ontology_snapshot_id" not in query_run_columns:
        with db_manager.engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE ask_data_query_run "
                    "ADD ontology_snapshot_id VARCHAR(128)"
                )
            )


__all__ = [
    "AskDataSceneDao",
    "AskDataSceneRevisionDao",
    "AskDataSnapshotDao",
    "AskDataQueryRunDao",
    "AskDataAgentRunDao",
    "AskDataAuditDao",
    "AskDataIdempotencyDao",
    "create_ask_data_tables",
]
