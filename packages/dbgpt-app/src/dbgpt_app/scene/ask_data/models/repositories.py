"""Repository boundary for M5 domain entities.

The in-memory implementation is deliberately small and replaceable by the
project's SQLAlchemy metadata database in the HTTP integration phase.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock

from dbgpt.storage.metadata import DatabaseManager

from ..schemas.snapshot import Snapshot, SnapshotStatus
from .entities import RevisionStatus, Scene, SceneRevision, SceneStatus
from .sql_entities import (
    AskDataSceneEntity,
    AskDataSceneRevisionEntity,
)


class SceneRepositoryError(RuntimeError):
    """Raised for repository conflicts or missing entities."""


class InMemorySceneRepository:
    def __init__(self) -> None:
        self._scenes: dict[str, Scene] = {}
        self._revisions: dict[tuple[str, int], SceneRevision] = {}
        self._lock = RLock()

    def create_scene(
        self,
        *,
        scene_id: str,
        name: str,
        description: str,
        data_source_name: str,
        view_name: str,
        semantic_md: str,
        created_by: str,
    ) -> tuple[Scene, SceneRevision]:
        with self._lock:
            if (
                scene_id in self._scenes
                and self._scenes[scene_id].soft_deleted_at is None
            ):
                raise SceneRepositoryError("SCENE_ID_CONFLICT")
            scene = Scene(
                scene_id=scene_id,
                name=name,
                description=description,
                latest_revision=1,
                created_by=created_by,
            )
            revision = SceneRevision(
                scene_id=scene_id,
                revision=1,
                data_source_name=data_source_name,
                view_name=view_name,
                semantic_md=semantic_md,
                created_by=created_by,
            )
            self._scenes[scene_id] = scene
            self._revisions[(scene_id, 1)] = revision
            return scene, revision

    def get_scene(self, scene_id: str, *, include_deleted: bool = False) -> Scene:
        with self._lock:
            scene = self._scenes.get(scene_id)
            if not scene or (scene.soft_deleted_at is not None and not include_deleted):
                raise SceneRepositoryError("SCENE_NOT_FOUND")
            return scene

    def list_scenes(self, *, include_deleted: bool = False) -> list[Scene]:
        with self._lock:
            scenes = list(self._scenes.values())
            if not include_deleted:
                scenes = [scene for scene in scenes if scene.soft_deleted_at is None]
            return sorted(scenes, key=lambda scene: scene.scene_id)

    def get_revision(self, scene_id: str, revision: int) -> SceneRevision:
        with self._lock:
            try:
                return self._revisions[(scene_id, revision)]
            except KeyError as exc:
                raise SceneRepositoryError("REVISION_NOT_FOUND") from exc

    def latest_revision(self, scene_id: str) -> SceneRevision:
        scene = self.get_scene(scene_id, include_deleted=True)
        return self.get_revision(scene_id, scene.latest_revision)

    def update_draft(
        self,
        scene_id: str,
        *,
        name: str,
        description: str,
        data_source_name: str,
        view_name: str,
        semantic_md: str,
        updated_by: str,
    ) -> tuple[Scene, SceneRevision]:
        with self._lock:
            scene = self.get_scene(scene_id)
            latest = self.get_revision(scene_id, scene.latest_revision)
            if latest.status == RevisionStatus.DRAFT:
                revision = latest.model_copy(
                    update={
                        "data_source_name": data_source_name,
                        "view_name": view_name,
                        "semantic_md": semantic_md,
                        "created_by": updated_by,
                    }
                )
            else:
                revision_number = scene.latest_revision + 1
                revision = SceneRevision(
                    scene_id=scene_id,
                    revision=revision_number,
                    data_source_name=data_source_name,
                    view_name=view_name,
                    semantic_md=semantic_md,
                    created_by=updated_by,
                )
                scene = scene.model_copy(update={"latest_revision": revision_number})
            scene = scene.model_copy(
                update={
                    "name": name,
                    "description": description,
                    "updated_at": revision.created_at,
                }
            )
            self._scenes[scene_id] = scene
            self._revisions[(scene_id, revision.revision)] = revision
            return scene, revision

    def save_revision(self, revision: SceneRevision) -> SceneRevision:
        with self._lock:
            self.get_scene(revision.scene_id)
            key = (revision.scene_id, revision.revision)
            existing = self._revisions.get(key)
            if (
                existing
                and existing.status
                in {
                    RevisionStatus.ACTIVE,
                    RevisionStatus.SUPERSEDED,
                }
                and existing != revision
            ):
                raise SceneRepositoryError("REVISION_IMMUTABLE")
            self._revisions[key] = revision
            return revision

    def activate(
        self,
        scene_id: str,
        revision: int,
        snapshot: Snapshot,
    ) -> tuple[Scene, SceneRevision]:
        with self._lock:
            scene = self.get_scene(scene_id)
            current = self.get_revision(scene_id, revision)
            if snapshot.scene_id != scene_id or snapshot.revision_id != str(revision):
                raise SceneRepositoryError("SNAPSHOT_REVISION_MISMATCH")
            if snapshot.status != SnapshotStatus.ACTIVE:
                raise SceneRepositoryError("SNAPSHOT_NOT_ACTIVE")
            if current.status not in {RevisionStatus.READY, RevisionStatus.ACTIVE}:
                raise SceneRepositoryError("REVISION_NOT_READY")
            for key, previous in list(self._revisions.items()):
                if key[0] == scene_id and previous.status == RevisionStatus.ACTIVE:
                    self._revisions[key] = previous.model_copy(
                        update={"status": RevisionStatus.SUPERSEDED}
                    )
            active_revision = current.model_copy(
                update={"status": RevisionStatus.ACTIVE}
            )
            active_scene = scene.model_copy(
                update={
                    "status": SceneStatus.ACTIVE,
                    "active_revision": revision,
                    "current_snapshot_id": snapshot.snapshot_id,
                }
            )
            self._revisions[(scene_id, revision)] = active_revision
            self._scenes[scene_id] = active_scene
            return active_scene, active_revision

    def set_revision_status(
        self,
        scene_id: str,
        revision: int,
        status: RevisionStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> SceneRevision:
        with self._lock:
            current = self.get_revision(scene_id, revision)
            if current.status in {RevisionStatus.ACTIVE, RevisionStatus.SUPERSEDED}:
                raise SceneRepositoryError("REVISION_IMMUTABLE")
            updated = current.model_copy(
                update={
                    "status": status,
                    "error_code": error_code,
                    "error_message": error_message,
                }
            )
            self._revisions[(scene_id, revision)] = updated
            return updated

    def disable(self, scene_id: str) -> Scene:
        with self._lock:
            scene = self.get_scene(scene_id)
            if scene.status != SceneStatus.ACTIVE:
                raise SceneRepositoryError("SCENE_NOT_ACTIVE")
            updated = scene.model_copy(update={"status": SceneStatus.INACTIVE})
            self._scenes[scene_id] = updated
            return updated

    def set_scene_status(self, scene_id: str, status: SceneStatus) -> Scene:
        with self._lock:
            scene = self.get_scene(scene_id)
            updated = scene.model_copy(update={"status": status})
            self._scenes[scene_id] = updated
            return updated

    def enable(self, scene_id: str) -> Scene:
        with self._lock:
            scene = self.get_scene(scene_id)
            if scene.current_snapshot_id is None or scene.active_revision is None:
                raise SceneRepositoryError("SCENE_HAS_NO_ACTIVE_SNAPSHOT")
            if scene.active_revision != scene.latest_revision:
                raise SceneRepositoryError("SCENE_HAS_UNVALIDATED_DRAFT")
            updated = scene.model_copy(update={"status": SceneStatus.ACTIVE})
            self._scenes[scene_id] = updated
            return updated

    def soft_delete(self, scene_id: str) -> Scene:
        with self._lock:
            scene = self.get_scene(scene_id)
            updated = scene.model_copy(
                update={
                    "status": SceneStatus.INACTIVE,
                    "soft_deleted_at": datetime.now(timezone.utc),
                }
            )
            self._scenes[scene_id] = updated
            return updated


class SqlSceneRepository:
    """SQLAlchemy-backed Scene repository with transactional activation."""

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def create_scene(self, **values):
        with self.db_manager.session() as session:
            existing = (
                session.query(AskDataSceneEntity)
                .filter_by(scene_id=values["scene_id"])
                .first()
            )
            if existing and existing.soft_deleted_at is None:
                raise SceneRepositoryError("SCENE_ID_CONFLICT")
            scene = Scene(
                scene_id=values["scene_id"],
                name=values["name"],
                description=values["description"],
                latest_revision=1,
                created_by=values["created_by"],
            )
            revision = SceneRevision(
                scene_id=scene.scene_id,
                revision=1,
                data_source_name=values["data_source_name"],
                view_name=values["view_name"],
                semantic_md=values["semantic_md"],
                created_by=values["created_by"],
            )
            if existing:
                for key, value in scene.model_dump().items():
                    setattr(existing, key, value)
            else:
                session.add(AskDataSceneEntity(**scene.model_dump()))
            session.add(AskDataSceneRevisionEntity(**revision.model_dump()))
            return scene, revision

    def get_scene(self, scene_id: str, *, include_deleted: bool = False) -> Scene:
        with self.db_manager.session(commit=False) as session:
            query = session.query(AskDataSceneEntity).filter_by(scene_id=scene_id)
            if not include_deleted:
                query = query.filter(AskDataSceneEntity.soft_deleted_at.is_(None))
            entity = query.first()
            if not entity:
                raise SceneRepositoryError("SCENE_NOT_FOUND")
            return self._scene(entity)

    def list_scenes(self, *, include_deleted: bool = False) -> list[Scene]:
        with self.db_manager.session(commit=False) as session:
            query = session.query(AskDataSceneEntity)
            if not include_deleted:
                query = query.filter(AskDataSceneEntity.soft_deleted_at.is_(None))
            return [
                self._scene(entity)
                for entity in query.order_by(AskDataSceneEntity.scene_id).all()
            ]

    def get_revision(self, scene_id: str, revision: int) -> SceneRevision:
        with self.db_manager.session(commit=False) as session:
            entity = (
                session.query(AskDataSceneRevisionEntity)
                .filter_by(scene_id=scene_id, revision=revision)
                .first()
            )
            if not entity:
                raise SceneRepositoryError("REVISION_NOT_FOUND")
            return self._revision(entity)

    def latest_revision(self, scene_id: str) -> SceneRevision:
        scene = self.get_scene(scene_id, include_deleted=True)
        return self.get_revision(scene_id, scene.latest_revision)

    def update_draft(self, scene_id: str, **values):
        values = {**values, "scene_id": scene_id}
        with self.db_manager.session() as session:
            scene_entity = (
                session.query(AskDataSceneEntity)
                .filter_by(scene_id=values["scene_id"])
                .first()
            )
            if not scene_entity or scene_entity.soft_deleted_at is not None:
                raise SceneRepositoryError("SCENE_NOT_FOUND")
            revision_entity = (
                session.query(AskDataSceneRevisionEntity)
                .filter_by(
                    scene_id=values["scene_id"], revision=scene_entity.latest_revision
                )
                .first()
            )
            if revision_entity.status == RevisionStatus.DRAFT.value:
                revision_number = revision_entity.revision
                for key in ("data_source_name", "view_name", "semantic_md"):
                    setattr(revision_entity, key, values[key])
                revision_entity.created_by = values["updated_by"]
            else:
                revision_number = scene_entity.latest_revision + 1
                revision_entity = AskDataSceneRevisionEntity(
                    scene_id=values["scene_id"],
                    revision=revision_number,
                    status=RevisionStatus.DRAFT.value,
                    data_source_name=values["data_source_name"],
                    view_name=values["view_name"],
                    semantic_md=values["semantic_md"],
                    created_by=values["updated_by"],
                    created_at=datetime.now(timezone.utc),
                )
                session.add(revision_entity)
                scene_entity.latest_revision = revision_number
            scene_entity.name = values["name"]
            scene_entity.description = values["description"]
            scene = self._scene(scene_entity)
            revision = self._revision(revision_entity)
            return scene, revision

    def save_revision(self, revision: SceneRevision) -> SceneRevision:
        with self.db_manager.session() as session:
            entity = (
                session.query(AskDataSceneRevisionEntity)
                .filter_by(scene_id=revision.scene_id, revision=revision.revision)
                .first()
            )
            if (
                entity
                and entity.status
                in {
                    RevisionStatus.ACTIVE.value,
                    RevisionStatus.SUPERSEDED.value,
                }
                and self._revision(entity) != revision
            ):
                raise SceneRepositoryError("REVISION_IMMUTABLE")
            values = revision.model_dump()
            if entity:
                for key, value in values.items():
                    setattr(entity, key, value)
            else:
                session.add(AskDataSceneRevisionEntity(**values))
            return revision

    def activate(self, scene_id: str, revision: int, snapshot: Snapshot):
        with self.db_manager.session() as session:
            scene_entity = (
                session.query(AskDataSceneEntity).filter_by(scene_id=scene_id).first()
            )
            revision_entity = (
                session.query(AskDataSceneRevisionEntity)
                .filter_by(scene_id=scene_id, revision=revision)
                .first()
            )
            if not scene_entity or not revision_entity:
                raise SceneRepositoryError("SCENE_NOT_FOUND")
            if snapshot.scene_id != scene_id or snapshot.revision_id != str(revision):
                raise SceneRepositoryError("SNAPSHOT_REVISION_MISMATCH")
            if snapshot.status != SnapshotStatus.ACTIVE:
                raise SceneRepositoryError("SNAPSHOT_NOT_ACTIVE")
            if revision_entity.status not in {
                RevisionStatus.READY.value,
                RevisionStatus.ACTIVE.value,
            }:
                raise SceneRepositoryError("REVISION_NOT_READY")
            session.query(AskDataSceneRevisionEntity).filter(
                AskDataSceneRevisionEntity.scene_id == scene_id,
                AskDataSceneRevisionEntity.status == RevisionStatus.ACTIVE.value,
            ).update({"status": RevisionStatus.SUPERSEDED.value})
            revision_entity.status = RevisionStatus.ACTIVE.value
            scene_entity.status = SceneStatus.ACTIVE.value
            scene_entity.active_revision = revision
            scene_entity.current_snapshot_id = snapshot.snapshot_id
            return self._scene(scene_entity), self._revision(revision_entity)

    def activate_snapshot_atomically(
        self,
        scene_id: str,
        revision: int,
        snapshot_id: str,
        expected_schema_hash: str | None = None,
    ) -> Snapshot:
        from .sql_entities import AskDataSnapshotEntity

        with self.db_manager.session() as session:
            scene_entity = (
                session.query(AskDataSceneEntity)
                .filter_by(scene_id=scene_id)
                .with_for_update()
                .first()
            )
            revision_entity = session.query(AskDataSceneRevisionEntity).filter_by(
                scene_id=scene_id, revision=revision
            ).first()
            snapshot_entity = session.query(AskDataSnapshotEntity).filter_by(
                snapshot_id=snapshot_id
            ).first()
            if not scene_entity or not revision_entity or not snapshot_entity:
                raise SceneRepositoryError("SNAPSHOT_NOT_FOUND")
            if (
                snapshot_entity.scene_id != scene_id
                or snapshot_entity.revision_id != str(revision)
            ):
                raise SceneRepositoryError("SNAPSHOT_REVISION_MISMATCH")
            if snapshot_entity.status != SnapshotStatus.READY.value:
                raise SceneRepositoryError("SNAPSHOT_NOT_READY")
            if (
                expected_schema_hash
                and snapshot_entity.schema_hash != expected_schema_hash
            ):
                raise SceneRepositoryError("SNAPSHOT_SCHEMA_DRIFT")
            if revision_entity.status not in {
                RevisionStatus.READY.value,
                RevisionStatus.ACTIVE.value,
            }:
                raise SceneRepositoryError("REVISION_NOT_READY")
            session.query(AskDataSnapshotEntity).filter(
                AskDataSnapshotEntity.scene_id == scene_id,
                AskDataSnapshotEntity.status == SnapshotStatus.ACTIVE.value,
            ).update({"status": SnapshotStatus.SUPERSEDED.value})
            session.query(AskDataSceneRevisionEntity).filter(
                AskDataSceneRevisionEntity.scene_id == scene_id,
                AskDataSceneRevisionEntity.status == RevisionStatus.ACTIVE.value,
            ).update({"status": RevisionStatus.SUPERSEDED.value})
            snapshot_entity.status = SnapshotStatus.ACTIVE.value
            revision_entity.status = RevisionStatus.ACTIVE.value
            scene_entity.status = SceneStatus.ACTIVE.value
            scene_entity.active_revision = revision
            scene_entity.current_snapshot_id = snapshot_id
        from .dao import AskDataSnapshotDao

        loaded = AskDataSnapshotDao(self.db_manager).get_snapshot(snapshot_id)
        if loaded is None:
            raise SceneRepositoryError("SNAPSHOT_NOT_FOUND")
        return loaded

    def set_revision_status(
        self, scene_id, revision, status, *, error_code=None, error_message=None
    ):
        with self.db_manager.session() as session:
            entity = (
                session.query(AskDataSceneRevisionEntity)
                .filter_by(scene_id=scene_id, revision=revision)
                .first()
            )
            if not entity:
                raise SceneRepositoryError("REVISION_NOT_FOUND")
            if entity.status in {
                RevisionStatus.ACTIVE.value,
                RevisionStatus.SUPERSEDED.value,
            }:
                raise SceneRepositoryError("REVISION_IMMUTABLE")
            entity.status = status.value
            entity.error_code = error_code
            entity.error_message = error_message
            return self._revision(entity)

    def disable(self, scene_id: str) -> Scene:
        return self.set_scene_status(scene_id, SceneStatus.INACTIVE)

    def set_scene_status(self, scene_id: str, status: SceneStatus) -> Scene:
        with self.db_manager.session() as session:
            entity = (
                session.query(AskDataSceneEntity).filter_by(scene_id=scene_id).first()
            )
            if not entity:
                raise SceneRepositoryError("SCENE_NOT_FOUND")
            entity.status = status.value
            return self._scene(entity)

    def enable(self, scene_id: str) -> Scene:
        scene = self.get_scene(scene_id)
        if scene.current_snapshot_id is None or scene.active_revision is None:
            raise SceneRepositoryError("SCENE_HAS_NO_ACTIVE_SNAPSHOT")
        if scene.active_revision != scene.latest_revision:
            raise SceneRepositoryError("SCENE_HAS_UNVALIDATED_DRAFT")
        return self.set_scene_status(scene_id, SceneStatus.ACTIVE)

    def soft_delete(self, scene_id: str) -> Scene:
        with self.db_manager.session() as session:
            entity = (
                session.query(AskDataSceneEntity).filter_by(scene_id=scene_id).first()
            )
            if not entity:
                raise SceneRepositoryError("SCENE_NOT_FOUND")
            entity.status = SceneStatus.INACTIVE.value
            entity.soft_deleted_at = datetime.now(timezone.utc)
            return self._scene(entity)

    @staticmethod
    def _scene(entity: AskDataSceneEntity) -> Scene:
        return Scene.model_validate(
            {
                column.name: getattr(entity, column.name)
                for column in AskDataSceneEntity.__table__.columns
                if column.name != "id"
            }
        )

    @staticmethod
    def _revision(entity: AskDataSceneRevisionEntity) -> SceneRevision:
        return SceneRevision.model_validate(
            {
                column.name: getattr(entity, column.name)
                for column in AskDataSceneRevisionEntity.__table__.columns
                if column.name != "id"
            }
        )


__all__ = ["InMemorySceneRepository", "SceneRepositoryError", "SqlSceneRepository"]
