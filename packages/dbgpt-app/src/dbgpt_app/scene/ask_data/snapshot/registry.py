"""Fail-closed registry for active Scene Snapshots."""

from __future__ import annotations

from ..schemas.snapshot import Snapshot, SnapshotStatus
from .service import InMemorySnapshotService


class RegistryLookupError(LookupError):
    """Raised when a Scene is not eligible for dispatch."""


class SceneAgentRegistry:
    """Cache routing and runtime Snapshot projections by active Scene."""

    def __init__(
        self,
        service: InMemorySnapshotService,
        *,
        query_spec_version: str = "1",
        compiler_version: str = "1",
    ):
        self.service = service
        self.query_spec_version = query_spec_version
        self.compiler_version = compiler_version
        self._version = -1
        self._entries: dict[str, Snapshot] = {}

    @property
    def version(self) -> int:
        self.refresh()
        return self._version

    def refresh(self) -> int:
        if self._version == self.service.registry_version:
            return self._version
        entries: dict[str, Snapshot] = {}
        for snapshot in self.service.active_snapshots():
            if self._eligible(snapshot, snapshot.scene_id):
                entries[snapshot.scene_id] = snapshot
        self._entries = entries
        self._version = self.service.registry_version
        return self._version

    def get(self, scene_id: str) -> Snapshot:
        self.refresh()
        try:
            return self._entries[scene_id]
        except KeyError as exc:
            raise RegistryLookupError(f"Scene is not active: {scene_id}") from exc

    def active_snapshots(self) -> list[Snapshot]:
        self.refresh()
        return list(self._entries.values())

    def routing_projection(self, scene_id: str) -> dict:
        return self.get(scene_id).routing_projection

    def _eligible(self, snapshot: Snapshot, scene_id: str) -> bool:
        return (
            snapshot.scene_id == scene_id
            and snapshot.status == SnapshotStatus.ACTIVE
            and snapshot.query_spec_version == self.query_spec_version
            and snapshot.compiler_version == self.compiler_version
            and snapshot.source_hashes.semantic_hash.startswith("sha256:")
            and snapshot.source_hashes.schema_hash.startswith("sha256:")
            and snapshot.source_hashes.knowledge_hash.startswith("sha256:")
        )


__all__ = ["RegistryLookupError", "SceneAgentRegistry"]
