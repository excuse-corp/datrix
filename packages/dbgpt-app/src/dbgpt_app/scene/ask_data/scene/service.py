"""Scene lifecycle façade coordinating M2 and M5 domain services."""

from __future__ import annotations

from ..models.entities import RevisionStatus, Scene, SceneRevision, SceneStatus
from ..models.repositories import InMemorySceneRepository
from ..schemas.snapshot import Snapshot


class SceneLifecycleService:
    def __init__(self, repository: InMemorySceneRepository):
        self.repository = repository

    def create(self, **kwargs) -> tuple[Scene, SceneRevision]:
        return self.repository.create_scene(**kwargs)

    def update_draft(self, scene_id: str, **kwargs) -> tuple[Scene, SceneRevision]:
        return self.repository.update_draft(scene_id, **kwargs)

    def mark_validating(self, scene_id: str, revision: int) -> SceneRevision:
        return self.repository.set_revision_status(
            scene_id, revision, RevisionStatus.VALIDATING
        )

    def mark_ready(
        self,
        scene_id: str,
        revision: int,
        *,
        parsed_config_json: dict,
        view_schema_json: dict,
        semantic_hash: str,
        schema_hash: str,
        knowledge_hash: str,
        knowledge_space_name: str | None = None,
    ) -> SceneRevision:
        current = self.repository.get_revision(scene_id, revision)
        updated = current.model_copy(
            update={
                "status": RevisionStatus.READY,
                "parsed_config_json": parsed_config_json,
                "view_schema_json": view_schema_json,
                "semantic_hash": semantic_hash,
                "schema_hash": schema_hash,
                "knowledge_hash": knowledge_hash,
                "knowledge_space_name": knowledge_space_name,
            }
        )
        return self.repository.save_revision(updated)

    def activate(
        self, scene_id: str, revision: int, snapshot: Snapshot
    ) -> tuple[Scene, SceneRevision]:
        return self.repository.activate(scene_id, revision, snapshot)

    def disable(self, scene_id: str) -> Scene:
        return self.repository.disable(scene_id)

    def enable(self, scene_id: str) -> Scene:
        return self.repository.enable(scene_id)

    def delete(self, scene_id: str) -> Scene:
        return self.repository.soft_delete(scene_id)

    def invalidate(self, scene_id: str, *, reason: str) -> Scene:
        return self.repository.set_scene_status(scene_id, SceneStatus.INVALID)


__all__ = ["SceneLifecycleService"]
