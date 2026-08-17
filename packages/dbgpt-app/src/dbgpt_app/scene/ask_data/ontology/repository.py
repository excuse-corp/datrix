"""Persistence for the single global business Ontology."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text, UniqueConstraint

from dbgpt.storage.metadata import DatabaseManager, Model

from .markdown import DEFAULT_ONTOLOGY_MARKDOWN, is_legacy_default_ontology
from .schemas import OntologyRevision, OntologyRevisionStatus, OntologySnapshot


class OntologyRepositoryError(RuntimeError):
    pass


class AskDataOntologyEntity(Model):
    __tablename__ = "ask_data_ontology"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ontology_id = Column(String(128), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False, default="企业业务本体")
    latest_revision = Column(Integer, nullable=False, default=1)
    active_revision = Column(Integer, nullable=True)
    current_snapshot_id = Column(String(128), nullable=True)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class AskDataOntologyRevisionEntity(Model):
    __tablename__ = "ask_data_ontology_revision"
    __table_args__ = (
        UniqueConstraint(
            "ontology_id", "revision", name="uq_ask_data_ontology_revision"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    ontology_id = Column(String(128), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="draft", index=True)
    ontology_md = Column(Text, nullable=False)
    graph_json = Column(JSON, nullable=True)
    prompt_projection_json = Column(JSON, nullable=True)
    source_scene_snapshots_json = Column(JSON, nullable=False, default=list)
    validation_issues_json = Column(JSON, nullable=False, default=list)
    content_hash = Column(String(128), nullable=True)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class AskDataOntologySnapshotEntity(Model):
    __tablename__ = "ask_data_ontology_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "ontology_id", "revision", name="uq_ask_data_ontology_snapshot_revision"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_id = Column(String(128), nullable=False, unique=True, index=True)
    ontology_id = Column(String(128), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="ready", index=True)
    content_hash = Column(String(128), nullable=False)
    graph_json = Column(JSON, nullable=False)
    prompt_projection_json = Column(JSON, nullable=False)
    source_scene_snapshots_json = Column(JSON, nullable=False, default=list)
    compiler_version = Column(String(32), nullable=False, default="1")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    activated_at = Column(DateTime, nullable=True)


class AskDataOntologyLayoutEntity(Model):
    __tablename__ = "ask_data_ontology_layout"
    __table_args__ = (
        UniqueConstraint(
            "ontology_id", "revision", name="uq_ask_data_ontology_layout_revision"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    ontology_id = Column(String(128), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    layout_json = Column(JSON, nullable=False, default=dict)
    updated_by = Column(String(128), nullable=False)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


def create_ontology_tables(db_manager: DatabaseManager) -> None:
    db_manager.metadata.create_all(
        db_manager.engine,
        tables=[
            AskDataOntologyEntity.__table__,
            AskDataOntologyRevisionEntity.__table__,
            AskDataOntologySnapshotEntity.__table__,
            AskDataOntologyLayoutEntity.__table__,
        ],
    )


class InMemoryOntologyRepository:
    """A deterministic fallback for tests and uninitialized metadata databases."""

    def __init__(self) -> None:
        self._revisions: dict[int, OntologyRevision] = {}
        self._snapshots: dict[str, OntologySnapshot] = {}
        self._active_revision: int | None = None
        self._layout: dict[int, dict[str, Any]] = {}
        self._lock = RLock()

    def ensure(self, *, created_by: str) -> OntologyRevision:
        with self._lock:
            if not self._revisions:
                self._revisions[1] = OntologyRevision(
                    ontology_id="global_business",
                    revision=1,
                    ontology_md=DEFAULT_ONTOLOGY_MARKDOWN,
                    created_by=created_by,
                )
            latest_revision = max(self._revisions)
            latest = self._revisions[latest_revision]
            if (
                latest.status == OntologyRevisionStatus.DRAFT
                and is_legacy_default_ontology(latest.ontology_md)
            ):
                latest = latest.model_copy(
                    update={
                        "ontology_md": DEFAULT_ONTOLOGY_MARKDOWN,
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
                self._revisions[latest_revision] = latest
            return latest

    def get_revision(self, revision: int) -> OntologyRevision:
        try:
            return self._revisions[revision]
        except KeyError as exc:
            raise OntologyRepositoryError("ONTOLOGY_REVISION_NOT_FOUND") from exc

    def save_draft(
        self, *, markdown: str, created_by: str, expected_revision: int | None
    ) -> OntologyRevision:
        with self._lock:
            latest = self.ensure(created_by=created_by)
            if expected_revision is not None and expected_revision != latest.revision:
                raise OntologyRepositoryError("ONTOLOGY_REVISION_CONFLICT")
            revision = (
                latest.revision
                if latest.status
                in {OntologyRevisionStatus.DRAFT, OntologyRevisionStatus.FAILED}
                else latest.revision + 1
            )
            now = datetime.now(timezone.utc)
            current = OntologyRevision(
                ontology_id=latest.ontology_id,
                revision=revision,
                status=OntologyRevisionStatus.DRAFT,
                ontology_md=markdown,
                created_by=created_by,
                created_at=latest.created_at if revision == latest.revision else now,
                updated_at=now,
            )
            self._revisions[revision] = current
            return current

    def save_compilation(self, revision: OntologyRevision) -> OntologyRevision:
        with self._lock:
            self._revisions[revision.revision] = revision
            return revision

    def save_snapshot(self, snapshot: OntologySnapshot) -> OntologySnapshot:
        with self._lock:
            self._snapshots[snapshot.snapshot_id] = snapshot
            return snapshot

    def get_snapshot(self, snapshot_id: str) -> OntologySnapshot:
        try:
            return self._snapshots[snapshot_id]
        except KeyError as exc:
            raise OntologyRepositoryError("ONTOLOGY_SNAPSHOT_NOT_FOUND") from exc

    def activate(self, snapshot_id: str) -> OntologySnapshot:
        with self._lock:
            snapshot = self.get_snapshot(snapshot_id)
            if snapshot.status not in {"ready", "active"}:
                raise OntologyRepositoryError("ONTOLOGY_SNAPSHOT_NOT_READY")
            if self._active_revision is not None:
                previous = self._revisions[self._active_revision]
                self._revisions[self._active_revision] = previous.model_copy(
                    update={"status": OntologyRevisionStatus.SUPERSEDED}
                )
                for previous_id, previous_snapshot in self._snapshots.items():
                    if previous_snapshot.status == "active":
                        self._snapshots[previous_id] = previous_snapshot.model_copy(
                            update={"status": "ready"}
                        )
            active = snapshot.model_copy(
                update={"status": "active", "activated_at": datetime.now(timezone.utc)}
            )
            self._snapshots[snapshot_id] = active
            revision = self._revisions[snapshot.revision]
            self._revisions[snapshot.revision] = revision.model_copy(
                update={"status": OntologyRevisionStatus.ACTIVE}
            )
            self._active_revision = snapshot.revision
            return active

    def active_snapshot(self) -> OntologySnapshot | None:
        if self._active_revision is None:
            return None
        return next(
            (
                item
                for item in self._snapshots.values()
                if item.revision == self._active_revision and item.status == "active"
            ),
            None,
        )

    def list_revisions(self) -> list[OntologyRevision]:
        return [self._revisions[key] for key in sorted(self._revisions, reverse=True)]

    def list_snapshots(self) -> list[OntologySnapshot]:
        return sorted(
            self._snapshots.values(),
            key=lambda snapshot: (snapshot.revision, snapshot.created_at),
            reverse=True,
        )

    def save_layout(
        self, revision: int, layout: dict[str, Any], updated_by: str
    ) -> dict[str, Any]:
        self.get_revision(revision)
        self._layout[revision] = layout
        return layout

    def get_layout(self, revision: int) -> dict[str, Any]:
        return self._layout.get(revision, {})

    def save_source_scene_snapshots(
        self, revision: int, source_scene_snapshots: list[dict[str, Any]]
    ) -> OntologyRevision:
        with self._lock:
            current = self.get_revision(revision)
            updated = current.model_copy(
                update={
                    "source_scene_snapshots": source_scene_snapshots,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._revisions[revision] = updated
            return updated


class SqlOntologyRepository:
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
        create_ontology_tables(db_manager)

    def ensure(self, *, created_by: str) -> OntologyRevision:
        with self.db_manager.session() as session:
            ontology = (
                session.query(AskDataOntologyEntity)
                .filter_by(ontology_id="global_business")
                .first()
            )
            if ontology is None:
                ontology = AskDataOntologyEntity(
                    ontology_id="global_business",
                    created_by=created_by,
                    latest_revision=1,
                )
                revision = AskDataOntologyRevisionEntity(
                    ontology_id="global_business",
                    revision=1,
                    ontology_md=DEFAULT_ONTOLOGY_MARKDOWN,
                    created_by=created_by,
                )
                session.add_all([ontology, revision])
                session.flush()
                return self._revision(revision)
            revision = (
                session.query(AskDataOntologyRevisionEntity)
                .filter_by(
                    ontology_id=ontology.ontology_id, revision=ontology.latest_revision
                )
                .first()
            )
            if (
                ontology.active_revision is None
                and revision.status == OntologyRevisionStatus.DRAFT.value
                and is_legacy_default_ontology(revision.ontology_md)
            ):
                revision.ontology_md = DEFAULT_ONTOLOGY_MARKDOWN
                revision.graph_json = None
                revision.prompt_projection_json = None
                revision.validation_issues_json = []
                revision.content_hash = None
            return self._revision(revision)

    def get_revision(self, revision: int) -> OntologyRevision:
        with self.db_manager.session(commit=False) as session:
            entity = (
                session.query(AskDataOntologyRevisionEntity)
                .filter_by(ontology_id="global_business", revision=revision)
                .first()
            )
            if entity is None:
                raise OntologyRepositoryError("ONTOLOGY_REVISION_NOT_FOUND")
            return self._revision(entity)

    def save_draft(
        self, *, markdown: str, created_by: str, expected_revision: int | None
    ) -> OntologyRevision:
        with self.db_manager.session() as session:
            ontology = (
                session.query(AskDataOntologyEntity)
                .filter_by(ontology_id="global_business")
                .first()
            )
            if ontology is None:
                ontology = AskDataOntologyEntity(
                    ontology_id="global_business",
                    created_by=created_by,
                    latest_revision=1,
                )
                session.add(ontology)
                latest = None
            else:
                latest = (
                    session.query(AskDataOntologyRevisionEntity)
                    .filter_by(
                        ontology_id="global_business", revision=ontology.latest_revision
                    )
                    .first()
                )
            if (
                expected_revision is not None
                and expected_revision != ontology.latest_revision
            ):
                raise OntologyRepositoryError("ONTOLOGY_REVISION_CONFLICT")
            if latest is not None and latest.status in {"draft", "failed"}:
                latest.ontology_md = markdown
                latest.status = "draft"
                latest.graph_json = None
                latest.prompt_projection_json = None
                latest.validation_issues_json = []
                latest.content_hash = None
                latest.created_by = created_by
                return self._revision(latest)
            revision_number = ontology.latest_revision + 1 if latest is not None else 1
            revision = AskDataOntologyRevisionEntity(
                ontology_id="global_business",
                revision=revision_number,
                ontology_md=markdown,
                created_by=created_by,
            )
            ontology.latest_revision = revision_number
            session.add(revision)
            session.flush()
            return self._revision(revision)

    def save_compilation(self, revision: OntologyRevision) -> OntologyRevision:
        with self.db_manager.session() as session:
            entity = (
                session.query(AskDataOntologyRevisionEntity)
                .filter_by(ontology_id=revision.ontology_id, revision=revision.revision)
                .first()
            )
            if entity is None:
                raise OntologyRepositoryError("ONTOLOGY_REVISION_NOT_FOUND")
            entity.status = revision.status.value
            entity.graph_json = revision.graph_json
            entity.prompt_projection_json = revision.prompt_projection_json
            entity.source_scene_snapshots_json = revision.source_scene_snapshots
            entity.validation_issues_json = revision.validation_issues
            entity.content_hash = revision.content_hash
            return self._revision(entity)

    def save_snapshot(self, snapshot: OntologySnapshot) -> OntologySnapshot:
        with self.db_manager.session() as session:
            entity = (
                session.query(AskDataOntologySnapshotEntity)
                .filter_by(snapshot_id=snapshot.snapshot_id)
                .first()
            )
            values = {
                "snapshot_id": snapshot.snapshot_id,
                "ontology_id": snapshot.ontology_id,
                "revision": snapshot.revision,
                "status": snapshot.status,
                "content_hash": snapshot.content_hash,
                "graph_json": snapshot.graph_json,
                "prompt_projection_json": snapshot.prompt_projection_json,
                "source_scene_snapshots_json": snapshot.source_scene_snapshots,
                "compiler_version": snapshot.compiler_version,
                "created_at": snapshot.created_at,
                "activated_at": snapshot.activated_at,
            }
            if entity is None:
                session.add(AskDataOntologySnapshotEntity(**values))
            else:
                for key, value in values.items():
                    setattr(entity, key, value)
            return snapshot

    def get_snapshot(self, snapshot_id: str) -> OntologySnapshot:
        with self.db_manager.session(commit=False) as session:
            entity = (
                session.query(AskDataOntologySnapshotEntity)
                .filter_by(snapshot_id=snapshot_id)
                .first()
            )
            if entity is None:
                raise OntologyRepositoryError("ONTOLOGY_SNAPSHOT_NOT_FOUND")
            return self._snapshot(entity)

    def activate(self, snapshot_id: str) -> OntologySnapshot:
        with self.db_manager.session() as session:
            snapshot = (
                session.query(AskDataOntologySnapshotEntity)
                .filter_by(snapshot_id=snapshot_id)
                .first()
            )
            if snapshot is None:
                raise OntologyRepositoryError("ONTOLOGY_SNAPSHOT_NOT_FOUND")
            ontology = (
                session.query(AskDataOntologyEntity)
                .filter_by(ontology_id=snapshot.ontology_id)
                .first()
            )
            if ontology is None:
                raise OntologyRepositoryError("ONTOLOGY_NOT_FOUND")
            if ontology.active_revision is not None:
                previous = (
                    session.query(AskDataOntologyRevisionEntity)
                    .filter_by(
                        ontology_id=ontology.ontology_id,
                        revision=ontology.active_revision,
                    )
                    .first()
                )
                if previous is not None:
                    previous.status = "superseded"
            (
                session.query(AskDataOntologySnapshotEntity)
                .filter_by(ontology_id=snapshot.ontology_id, status="active")
                .update({"status": "ready"})
            )
            revision = (
                session.query(AskDataOntologyRevisionEntity)
                .filter_by(ontology_id=ontology.ontology_id, revision=snapshot.revision)
                .first()
            )
            if revision is None or revision.status not in {"ready", "active"}:
                raise OntologyRepositoryError("ONTOLOGY_SNAPSHOT_NOT_READY")
            revision.status = "active"
            snapshot.status = "active"
            snapshot.activated_at = datetime.now(timezone.utc)
            ontology.active_revision = snapshot.revision
            ontology.current_snapshot_id = snapshot.snapshot_id
            return self._snapshot(snapshot)

    def active_snapshot(self) -> OntologySnapshot | None:
        with self.db_manager.session(commit=False) as session:
            ontology = (
                session.query(AskDataOntologyEntity)
                .filter_by(ontology_id="global_business")
                .first()
            )
            if ontology is None or not ontology.current_snapshot_id:
                return None
            entity = (
                session.query(AskDataOntologySnapshotEntity)
                .filter_by(snapshot_id=ontology.current_snapshot_id, status="active")
                .first()
            )
            return self._snapshot(entity) if entity is not None else None

    def list_revisions(self) -> list[OntologyRevision]:
        with self.db_manager.session(commit=False) as session:
            return [
                self._revision(item)
                for item in session.query(AskDataOntologyRevisionEntity)
                .filter_by(ontology_id="global_business")
                .order_by(AskDataOntologyRevisionEntity.revision.desc())
                .all()
            ]

    def list_snapshots(self) -> list[OntologySnapshot]:
        with self.db_manager.session(commit=False) as session:
            return [
                self._snapshot(item)
                for item in session.query(AskDataOntologySnapshotEntity)
                .filter_by(ontology_id="global_business")
                .order_by(AskDataOntologySnapshotEntity.created_at.desc())
                .all()
            ]

    def save_layout(
        self, revision: int, layout: dict[str, Any], updated_by: str
    ) -> dict[str, Any]:
        with self.db_manager.session() as session:
            entity = (
                session.query(AskDataOntologyLayoutEntity)
                .filter_by(ontology_id="global_business", revision=revision)
                .first()
            )
            if entity is None:
                session.add(
                    AskDataOntologyLayoutEntity(
                        ontology_id="global_business",
                        revision=revision,
                        layout_json=layout,
                        updated_by=updated_by,
                    )
                )
            else:
                entity.layout_json = layout
                entity.updated_by = updated_by
            return layout

    def get_layout(self, revision: int) -> dict[str, Any]:
        with self.db_manager.session(commit=False) as session:
            entity = (
                session.query(AskDataOntologyLayoutEntity)
                .filter_by(ontology_id="global_business", revision=revision)
                .first()
            )
            return dict(entity.layout_json or {}) if entity is not None else {}

    def save_source_scene_snapshots(
        self, revision: int, source_scene_snapshots: list[dict[str, Any]]
    ) -> OntologyRevision:
        with self.db_manager.session() as session:
            entity = (
                session.query(AskDataOntologyRevisionEntity)
                .filter_by(ontology_id="global_business", revision=revision)
                .first()
            )
            if entity is None:
                raise OntologyRepositoryError("ONTOLOGY_REVISION_NOT_FOUND")
            entity.source_scene_snapshots_json = source_scene_snapshots
            return self._revision(entity)

    @staticmethod
    def _revision(entity: AskDataOntologyRevisionEntity) -> OntologyRevision:
        return OntologyRevision(
            ontology_id=entity.ontology_id,
            revision=entity.revision,
            status=OntologyRevisionStatus(entity.status),
            ontology_md=entity.ontology_md,
            graph_json=entity.graph_json,
            prompt_projection_json=entity.prompt_projection_json,
            source_scene_snapshots=list(entity.source_scene_snapshots_json or []),
            validation_issues=list(entity.validation_issues_json or []),
            content_hash=entity.content_hash,
            created_by=entity.created_by,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    @staticmethod
    def _snapshot(entity: AskDataOntologySnapshotEntity) -> OntologySnapshot:
        return OntologySnapshot(
            snapshot_id=entity.snapshot_id,
            ontology_id=entity.ontology_id,
            revision=entity.revision,
            status=entity.status,
            content_hash=entity.content_hash,
            graph_json=entity.graph_json,
            prompt_projection_json=entity.prompt_projection_json,
            source_scene_snapshots=list(entity.source_scene_snapshots_json or []),
            compiler_version=entity.compiler_version,
            created_at=entity.created_at,
            activated_at=entity.activated_at,
        )


__all__ = [
    "AskDataOntologyEntity",
    "AskDataOntologyLayoutEntity",
    "AskDataOntologyRevisionEntity",
    "AskDataOntologySnapshotEntity",
    "InMemoryOntologyRepository",
    "OntologyRepositoryError",
    "SqlOntologyRepository",
    "create_ontology_tables",
]
