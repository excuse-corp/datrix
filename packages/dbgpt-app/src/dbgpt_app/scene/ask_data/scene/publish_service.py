"""Scene revision validation, Snapshot build and activation workflow."""

from __future__ import annotations

from typing import Callable

from ..models.entities import RevisionStatus, SceneStatus
from ..models.repositories import InMemorySceneRepository, SceneRepositoryError
from ..rag.manager import SceneKnowledgeManager
from ..schemas.schema import ViewSchema
from ..schemas.snapshot import Snapshot, SnapshotStatus
from ..snapshot.builder import SnapshotBuilder, SnapshotBuildError
from ..snapshot.service import InMemorySnapshotService, SnapshotStateError
from .markdown import SemanticMarkdownParser, split_scene_documents
from .validator import SceneConfigValidator, ValidationResult


class ScenePublishError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class ScenePublishService:
    def __init__(
        self,
        repository: InMemorySceneRepository,
        snapshot_service: InMemorySnapshotService,
        schema_resolver: Callable[[str, int], ViewSchema],
        *,
        parser: SemanticMarkdownParser | None = None,
        validator: SceneConfigValidator | None = None,
        builder: SnapshotBuilder | None = None,
        knowledge_manager: SceneKnowledgeManager | None = None,
    ):
        self.repository = repository
        self.snapshot_service = snapshot_service
        self.schema_resolver = schema_resolver
        self.parser = parser or SemanticMarkdownParser()
        self.validator = validator or SceneConfigValidator()
        self.builder = builder or SnapshotBuilder(self.parser, self.validator)
        self.knowledge_manager = knowledge_manager or SceneKnowledgeManager()

    def validate(self, scene_id: str, revision: int) -> ValidationResult:
        current = self._revision(scene_id, revision)
        try:
            parsed = self.parser.parse(current.semantic_md)
            view_schema = self.schema_resolver(scene_id, revision)
        except Exception as exc:
            code = getattr(exc, "code", "SCHEMA_SOURCE_UNAVAILABLE")
            raise ScenePublishError(code, str(exc)) from exc
        result = self.validator.validate(
            parsed.config,
            view_schema,
            data_dictionary_md=split_scene_documents(parsed.body_markdown).get(
                "data_dictionary_md"
            ),
            expected_scene_id=scene_id,
            expected_data_source=current.data_source_name,
            expected_view=current.view_name,
        )
        if result.valid:
            if current.status not in {
                RevisionStatus.ACTIVE,
                RevisionStatus.SUPERSEDED,
            }:
                self.repository.save_revision(
                    current.model_copy(
                        update={
                            "status": RevisionStatus.VALIDATING,
                            "parsed_config_json": parsed.config.model_dump(mode="json"),
                            "view_schema_json": view_schema.model_dump(mode="json"),
                            "semantic_hash": parsed.semantic_hash,
                            "schema_hash": view_schema.schema_hash,
                        }
                    )
                )
        else:
            if current.status in {RevisionStatus.ACTIVE, RevisionStatus.SUPERSEDED}:
                self.repository.set_scene_status(scene_id, SceneStatus.INVALID)
            else:
                self.repository.set_revision_status(
                    scene_id,
                    revision,
                    RevisionStatus.FAILED,
                    error_code=result.errors[0].code,
                    error_message=result.errors[0].message,
                )
        return result

    def build_snapshot(
        self,
        scene_id: str,
        revision: int,
        *,
        run_rag_quality_gate: bool = False,
        force_rebuild_knowledge: bool = False,
        build_knowledge: bool = False,
        persist_revision_state: bool = True,
    ) -> Snapshot:
        del force_rebuild_knowledge, build_knowledge
        current = self._revision(scene_id, revision)
        try:
            view_schema = self.schema_resolver(scene_id, revision)
            parsed = self.parser.parse(current.semantic_md)
            documents = split_scene_documents(parsed.body_markdown)
            validation = self.validator.validate(
                parsed.config,
                view_schema,
                data_dictionary_md=documents.get("data_dictionary_md"),
                expected_scene_id=scene_id,
                expected_data_source=current.data_source_name,
                expected_view=current.view_name,
            )
            if not validation.valid:
                issue = validation.errors[0]
                raise ScenePublishError(issue.code, issue.message)
            knowledge_hash = "sha256:none"
            knowledge_space = None
            snapshot = self.builder.build(
                scene_id=scene_id,
                revision_id=str(revision),
                markdown=current.semantic_md,
                view_schema=view_schema,
                knowledge_hash=knowledge_hash,
                knowledge_space=knowledge_space,
                run_rag_quality_gate=run_rag_quality_gate,
                rag_quality_ok=True,
            )
            self._save_ready_snapshot(snapshot)
            if persist_revision_state and self._revision_state_mutable(current):
                self.repository.save_revision(
                    current.model_copy(
                        update={
                            "status": RevisionStatus.READY,
                            "parsed_config_json": parsed.config.model_dump(mode="json"),
                            "view_schema_json": view_schema.model_dump(mode="json"),
                            "semantic_hash": parsed.semantic_hash,
                            "schema_hash": view_schema.schema_hash,
                            "knowledge_hash": knowledge_hash,
                            "knowledge_space_name": knowledge_space,
                        }
                    )
                )
            return snapshot
        except ScenePublishError as exc:
            if persist_revision_state and self._revision_state_mutable(current):
                self.repository.set_revision_status(
                    scene_id,
                    revision,
                    RevisionStatus.FAILED,
                    error_code=exc.code,
                    error_message=exc.message,
                )
            raise
        except (SnapshotBuildError, SceneRepositoryError) as exc:
            code = getattr(exc, "code", str(exc))
            if persist_revision_state and self._revision_state_mutable(current):
                self.repository.set_revision_status(
                    scene_id,
                    revision,
                    RevisionStatus.FAILED,
                    error_code=code,
                    error_message=str(exc),
                )
            raise ScenePublishError(code, str(exc)) from exc
        except Exception as exc:
            if persist_revision_state and self._revision_state_mutable(current):
                self.repository.set_revision_status(
                    scene_id,
                    revision,
                    RevisionStatus.FAILED,
                    error_code="SNAPSHOT_BUILD_FAILED",
                    error_message=str(exc),
                )
            raise ScenePublishError("SNAPSHOT_BUILD_FAILED", str(exc)) from exc

    def publish(
        self, scene_id: str, revision: int
    ) -> tuple[ValidationResult, Snapshot, int]:
        """Validate, build the internal Scene snapshot and activate it.

        This is the current lifecycle entry point used by Scene creation and the
        detail-page Validate action. It deliberately skips RAG indexing and
        quality gates: the immutable snapshot is a system routing/runtime object,
        not a separate user-triggered build artifact.
        """

        validation = self.validate(scene_id, revision)
        if not validation.valid:
            issue = validation.errors[0]
            raise ScenePublishError(issue.code, issue.message)

        try:
            scene = self.repository.get_scene(scene_id)
        except SceneRepositoryError as exc:
            raise ScenePublishError(str(exc), str(exc)) from exc

        if scene.active_revision == revision and scene.current_snapshot_id:
            try:
                existing = self.snapshot_service.get(scene.current_snapshot_id)
            except KeyError:
                existing = None
            if existing is not None and existing.status in {
                SnapshotStatus.ACTIVE,
                SnapshotStatus.READY,
            }:
                if existing.status == SnapshotStatus.ACTIVE:
                    return validation, existing, self.snapshot_service.registry_version
                active, registry_version = self.activate(scene_id, existing.snapshot_id)
                return validation, active, registry_version

        snapshot = self.build_snapshot(
            scene_id,
            revision,
            run_rag_quality_gate=False,
            build_knowledge=False,
        )
        active, registry_version = self.activate(scene_id, snapshot.snapshot_id)
        return validation, active, registry_version

    def rebuild_active_snapshots(
        self, scene_ids: list[str] | None = None
    ) -> tuple[list[dict[str, object]], int]:
        """Rebuild active Scene Snapshots with the current SnapshotBuilder.

        This supports Snapshot schema migrations without mutating the immutable
        Scene revision document. Old snapshots remain stored for audit/rollback.
        """
        allowed = set(scene_ids or [])
        scenes = [
            scene
            for scene in self.repository.list_scenes()
            if scene.status == SceneStatus.ACTIVE
            and scene.active_revision is not None
            and (not allowed or scene.scene_id in allowed)
        ]
        results: list[dict[str, object]] = []
        for scene in scenes:
            old_snapshot_id = scene.current_snapshot_id
            try:
                snapshot = self.build_snapshot(
                    scene.scene_id,
                    scene.active_revision,
                    run_rag_quality_gate=False,
                    build_knowledge=False,
                    persist_revision_state=False,
                )
                if old_snapshot_id == snapshot.snapshot_id:
                    active = self.snapshot_service.active(scene.scene_id)
                    status = "unchanged"
                else:
                    active, _ = self.activate(scene.scene_id, snapshot.snapshot_id)
                    status = "rebuilt"
                results.append(
                    {
                        "scene_id": scene.scene_id,
                        "revision": scene.active_revision,
                        "old_snapshot_id": old_snapshot_id,
                        "new_snapshot_id": snapshot.snapshot_id,
                        "snapshot_schema_version": snapshot.schema_version,
                        "status": status,
                        "active": active is not None,
                    }
                )
            except ScenePublishError as exc:
                results.append(
                    {
                        "scene_id": scene.scene_id,
                        "revision": scene.active_revision,
                        "old_snapshot_id": old_snapshot_id,
                        "status": "failed",
                        "error_code": exc.code,
                        "error_message": exc.message,
                    }
                )
        return results, self.snapshot_service.registry_version

    def activate(self, scene_id: str, snapshot_id: str) -> tuple[Snapshot, int]:
        try:
            snapshot = self.snapshot_service.get(snapshot_id)
            if snapshot.scene_id != scene_id:
                raise ScenePublishError(
                    "SNAPSHOT_SCENE_MISMATCH", "Snapshot does not belong to Scene"
                )
            atomic_activate = getattr(
                self.repository, "activate_snapshot_atomically", None
            )
            if atomic_activate is not None:
                active = atomic_activate(
                    scene_id, int(snapshot.revision_id), snapshot_id
                )
            else:
                active = self.snapshot_service.activate(snapshot_id)
                self.repository.activate(scene_id, int(active.revision_id), active)
            return active, self.snapshot_service.registry_version
        except (KeyError, SnapshotStateError, SceneRepositoryError) as exc:
            if isinstance(exc, KeyError):
                raise ScenePublishError(
                    "SNAPSHOT_NOT_FOUND", f"Snapshot does not exist: {snapshot_id}"
                ) from exc
            raise ScenePublishError(str(exc), str(exc)) from exc

    def disable(self, scene_id: str):
        try:
            scene = self.repository.disable(scene_id)
            deactivate = getattr(self.snapshot_service, "deactivate_scene", None)
            if deactivate is not None:
                deactivate(scene_id)
            return scene, self.snapshot_service.registry_version
        except (SnapshotStateError, SceneRepositoryError) as exc:
            raise ScenePublishError(str(exc), str(exc)) from exc

    def enable(self, scene_id: str):
        try:
            scene = self.repository.get_scene(scene_id)
            if not scene.current_snapshot_id:
                raise ScenePublishError(
                    "SCENE_HAS_NO_ACTIVE_SNAPSHOT",
                    "Scene has no validated snapshot to enable",
                )
            snapshot, registry_version = self.activate(
                scene_id, scene.current_snapshot_id
            )
            scene = self.repository.get_scene(scene_id)
            return scene, snapshot, registry_version
        except (SceneRepositoryError, ScenePublishError) as exc:
            if isinstance(exc, ScenePublishError):
                raise
            raise ScenePublishError(str(exc), str(exc)) from exc

    def _save_ready_snapshot(self, snapshot: Snapshot) -> Snapshot:
        try:
            existing = self.snapshot_service.get(snapshot.snapshot_id)
        except KeyError:
            return self.snapshot_service.save_ready(snapshot)
        if existing.status in {SnapshotStatus.READY, SnapshotStatus.ACTIVE} and (
            self._same_snapshot_content(existing, snapshot)
        ):
            return snapshot
        return self.snapshot_service.save_ready(snapshot)

    @staticmethod
    def _same_snapshot_content(left: Snapshot, right: Snapshot) -> bool:
        ignored = {"status", "error_code", "error_message", "created_at"}
        return left.model_dump(mode="json", exclude=ignored) == right.model_dump(
            mode="json", exclude=ignored
        )

    @staticmethod
    def _revision_state_mutable(revision) -> bool:
        return revision.status not in {RevisionStatus.ACTIVE, RevisionStatus.SUPERSEDED}

    def _revision(self, scene_id: str, revision: int):
        try:
            return self.repository.get_revision(scene_id, revision)
        except SceneRepositoryError as exc:
            raise ScenePublishError(str(exc), str(exc)) from exc


__all__ = ["ScenePublishError", "ScenePublishService"]
