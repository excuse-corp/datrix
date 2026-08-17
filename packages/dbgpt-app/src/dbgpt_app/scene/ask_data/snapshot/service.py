"""Snapshot lifecycle service with atomic in-memory persistence for MVP."""

from __future__ import annotations

import hashlib
from threading import RLock

from dbgpt.storage.metadata import DatabaseManager

from ..models.dao import AskDataSnapshotDao
from ..schemas.snapshot import Snapshot, SnapshotStatus


class SnapshotStateError(RuntimeError):
    """Raised for an invalid Snapshot lifecycle transition."""


class InMemorySnapshotService:
    """Reference repository used until M5 supplies database-backed storage."""

    def __init__(self) -> None:
        self._snapshots: dict[str, Snapshot] = {}
        self._active_by_scene: dict[str, str] = {}
        self._registry_version = 0
        self._lock = RLock()

    @property
    def registry_version(self) -> int:
        return self._registry_version

    def save_ready(self, snapshot: Snapshot) -> Snapshot:
        if snapshot.status != SnapshotStatus.READY:
            raise SnapshotStateError("Only ready Snapshots can be saved")
        with self._lock:
            if snapshot.snapshot_id in self._snapshots:
                existing = self._snapshots[snapshot.snapshot_id]
                if existing.model_dump(mode="json") != snapshot.model_dump(mode="json"):
                    raise SnapshotStateError(
                        "Snapshot ID already contains different content"
                    )
            self._snapshots[snapshot.snapshot_id] = snapshot
        return snapshot

    def get(self, snapshot_id: str) -> Snapshot:
        with self._lock:
            try:
                return self._snapshots[snapshot_id]
            except KeyError as exc:
                raise KeyError(f"Unknown Snapshot: {snapshot_id}") from exc

    def active(self, scene_id: str) -> Snapshot | None:
        with self._lock:
            snapshot_id = self._active_by_scene.get(scene_id)
            return self._snapshots.get(snapshot_id) if snapshot_id else None

    def active_snapshots(self) -> list[Snapshot]:
        with self._lock:
            return [
                self._snapshots[snapshot_id]
                for snapshot_id in self._active_by_scene.values()
            ]

    def list_snapshots(self) -> list[Snapshot]:
        with self._lock:
            return list(self._snapshots.values())

    def activate(
        self, snapshot_id: str, *, expected_schema_hash: str | None = None
    ) -> Snapshot:
        with self._lock:
            snapshot = self.get(snapshot_id)
            if snapshot.status == SnapshotStatus.ACTIVE:
                return snapshot
            if snapshot.status != SnapshotStatus.READY:
                raise SnapshotStateError("Only ready Snapshots can be activated")
            if (
                expected_schema_hash
                and snapshot.source_hashes.schema_hash != expected_schema_hash
            ):
                raise SnapshotStateError(
                    "Snapshot source hashes changed before activation"
                )
            old_id = self._active_by_scene.get(snapshot.scene_id)
            if old_id:
                old = self._snapshots[old_id]
                self._snapshots[old_id] = old.model_copy(
                    update={"status": SnapshotStatus.SUPERSEDED}
                )
            active = snapshot.model_copy(update={"status": SnapshotStatus.ACTIVE})
            self._snapshots[snapshot_id] = active
            self._active_by_scene[snapshot.scene_id] = snapshot_id
            self._registry_version += 1
            return active

    def deactivate_scene(self, scene_id: str) -> Snapshot | None:
        """Remove a Scene from the active routing registry without deleting it."""
        with self._lock:
            snapshot_id = self._active_by_scene.pop(scene_id, None)
            if not snapshot_id:
                return None
            snapshot = self._snapshots[snapshot_id]
            ready = snapshot.model_copy(update={"status": SnapshotStatus.READY})
            self._snapshots[snapshot_id] = ready
            self._registry_version += 1
            return ready

    def invalidate(self, snapshot_id: str, code: str, message: str) -> Snapshot:
        with self._lock:
            snapshot = self.get(snapshot_id)
            invalid = snapshot.model_copy(
                update={
                    "status": SnapshotStatus.INVALID,
                    "error_code": code,
                    "error_message": message,
                }
            )
            self._snapshots[snapshot_id] = invalid
            if self._active_by_scene.get(snapshot.scene_id) == snapshot_id:
                del self._active_by_scene[snapshot.scene_id]
                self._registry_version += 1
            return invalid

    def check_schema_drift(self, scene_id: str, schema_hash: str) -> bool:
        snapshot = self.active(scene_id)
        if not snapshot:
            return False
        if snapshot.source_hashes.schema_hash == schema_hash:
            return False
        self.invalidate(
            snapshot.snapshot_id, "SCHEMA_DRIFT", "Active view schema hash changed"
        )
        return True


class SqlSnapshotService:
    """SQLAlchemy-backed Snapshot lifecycle service."""

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
        self.dao = AskDataSnapshotDao(db_manager)

    @property
    def registry_version(self) -> int:
        return self._version()

    def save_ready(self, snapshot: Snapshot) -> Snapshot:
        if snapshot.status != SnapshotStatus.READY:
            raise SnapshotStateError("Only ready Snapshots can be saved")
        existing = self.dao.get_snapshot(snapshot.snapshot_id)
        if existing and existing.model_dump(mode="json") != snapshot.model_dump(
            mode="json"
        ):
            raise SnapshotStateError("Snapshot ID already contains different content")
        return self.dao.save_snapshot(snapshot)

    def get(self, snapshot_id: str) -> Snapshot:
        snapshot = self.dao.get_snapshot(snapshot_id)
        if snapshot is None:
            raise KeyError(f"Unknown Snapshot: {snapshot_id}")
        return snapshot

    def active(self, scene_id: str) -> Snapshot | None:
        with self.db_manager.session(commit=False) as session:
            from ..models.sql_entities import AskDataSnapshotEntity

            entity = (
                session.query(AskDataSnapshotEntity)
                .filter_by(scene_id=scene_id, status=SnapshotStatus.ACTIVE.value)
                .first()
            )
            return self.dao.get_snapshot(entity.snapshot_id) if entity else None

    def active_snapshots(self) -> list[Snapshot]:
        with self.db_manager.session(commit=False) as session:
            from ..models.sql_entities import AskDataSnapshotEntity

            entities = (
                session.query(AskDataSnapshotEntity)
                .filter_by(status=SnapshotStatus.ACTIVE.value)
                .all()
            )
            return [
                snapshot
                for entity in entities
                if (snapshot := self.dao.get_snapshot(entity.snapshot_id)) is not None
            ]

    def list_snapshots(self) -> list[Snapshot]:
        with self.db_manager.session(commit=False) as session:
            from ..models.sql_entities import AskDataSnapshotEntity

            entities = session.query(AskDataSnapshotEntity).all()
            return [
                snapshot
                for entity in entities
                if (snapshot := self.dao.get_snapshot(entity.snapshot_id)) is not None
            ]

    def activate(
        self, snapshot_id: str, *, expected_schema_hash: str | None = None
    ) -> Snapshot:
        with self.db_manager.session() as session:
            from ..models.sql_entities import AskDataSnapshotEntity

            entity = (
                session.query(AskDataSnapshotEntity)
                .filter_by(snapshot_id=snapshot_id)
                .first()
            )
            if not entity:
                raise KeyError(f"Unknown Snapshot: {snapshot_id}")
            if entity.status == SnapshotStatus.ACTIVE.value:
                return self.get(snapshot_id)
            if entity.status != SnapshotStatus.READY.value:
                raise SnapshotStateError("Only ready Snapshots can be activated")
            if expected_schema_hash and entity.schema_hash != expected_schema_hash:
                raise SnapshotStateError(
                    "Snapshot source hashes changed before activation"
                )
            session.query(AskDataSnapshotEntity).filter(
                AskDataSnapshotEntity.scene_id == entity.scene_id,
                AskDataSnapshotEntity.status == SnapshotStatus.ACTIVE.value,
            ).update({"status": SnapshotStatus.SUPERSEDED.value})
            entity.status = SnapshotStatus.ACTIVE.value
        return self.get(snapshot_id)

    def deactivate_scene(self, scene_id: str) -> Snapshot | None:
        with self.db_manager.session() as session:
            from ..models.sql_entities import AskDataSnapshotEntity

            entity = (
                session.query(AskDataSnapshotEntity)
                .filter_by(scene_id=scene_id, status=SnapshotStatus.ACTIVE.value)
                .first()
            )
            if not entity:
                return None
            entity.status = SnapshotStatus.READY.value
            snapshot_id = entity.snapshot_id
        return self.get(snapshot_id)

    def invalidate(self, snapshot_id: str, code: str, message: str) -> Snapshot:
        with self.db_manager.session() as session:
            from ..models.sql_entities import AskDataSnapshotEntity

            entity = (
                session.query(AskDataSnapshotEntity)
                .filter_by(snapshot_id=snapshot_id)
                .first()
            )
            if not entity:
                raise KeyError(f"Unknown Snapshot: {snapshot_id}")
            entity.status = SnapshotStatus.INVALID.value
            entity.error_code = code
            entity.error_message = message
        return self.get(snapshot_id)

    def check_schema_drift(self, scene_id: str, schema_hash: str) -> bool:
        snapshot = self.active(scene_id)
        if not snapshot or snapshot.source_hashes.schema_hash == schema_hash:
            return False
        self.invalidate(
            snapshot.snapshot_id, "SCHEMA_DRIFT", "Active view schema hash changed"
        )
        return True

    def _version(self) -> int:
        with self.db_manager.session(commit=False) as session:
            from ..models.sql_entities import AskDataSnapshotEntity

            rows = (
                session.query(
                    AskDataSnapshotEntity.scene_id,
                    AskDataSnapshotEntity.snapshot_id,
                    AskDataSnapshotEntity.content_hash,
                )
                .filter_by(status=SnapshotStatus.ACTIVE.value)
                .order_by(AskDataSnapshotEntity.scene_id)
                .all()
            )
        if not rows:
            return 0
        canonical = "|".join(
            f"{scene_id}:{snapshot_id}:{content_hash}"
            for scene_id, snapshot_id, content_hash in rows
        )
        return int(hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:15], 16)


__all__ = ["InMemorySnapshotService", "SnapshotStateError", "SqlSnapshotService"]
