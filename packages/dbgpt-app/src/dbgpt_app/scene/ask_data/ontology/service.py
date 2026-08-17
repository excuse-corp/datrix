"""Draft, validation, immutable Snapshot and activation lifecycle."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .fragments import build_scene_fragment, merge_graphs, source_snapshot_refs
from .markdown import (
    OntologyMarkdownParser,
    build_prompt_projection,
    with_managed_frontmatter,
)
from .repository import (
    InMemoryOntologyRepository,
    OntologyRepositoryError,
    SqlOntologyRepository,
)
from .schemas import (
    OntologyRevision,
    OntologyRevisionStatus,
    OntologySnapshot,
)


class OntologyServiceError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class OntologyConflictError(OntologyServiceError):
    pass


class OntologyLifecycleService:
    compiler_version = "1"

    def __init__(
        self,
        repository: InMemoryOntologyRepository | SqlOntologyRepository,
        parser: OntologyMarkdownParser | None = None,
    ):
        self.repository = repository
        self.parser = parser or OntologyMarkdownParser()

    def draft(self, *, user_id: str) -> OntologyRevision:
        return self.repository.ensure(created_by=user_id)

    def save_draft(
        self, *, markdown: str, user_id: str, expected_revision: int | None = None
    ) -> OntologyRevision:
        try:
            saved = self.repository.save_draft(
                markdown=markdown,
                created_by=user_id,
                expected_revision=expected_revision,
            )
            managed = with_managed_frontmatter(markdown, saved.revision)
            if saved.ontology_md != managed:
                saved = self.repository.save_draft(
                    markdown=managed,
                    created_by=user_id,
                    expected_revision=saved.revision,
                )
            return saved
        except OntologyRepositoryError as exc:
            if str(exc) == "ONTOLOGY_REVISION_CONFLICT":
                raise OntologyConflictError(
                    "ONTOLOGY_REVISION_CONFLICT",
                    "本体文档已被其他用户更新，请刷新后再保存。",
                ) from exc
            raise OntologyServiceError(str(exc), str(exc)) from exc

    def preview(self, markdown: str) -> dict[str, Any]:
        compilation = self.parser.compile(markdown)
        return {
            "valid": compilation.valid,
            "graph": compilation.graph.model_dump(mode="json"),
            "prompt_projection": compilation.prompt_projection,
            "issues": [item.model_dump(mode="json") for item in compilation.issues],
        }

    def validate(
        self, revision: int, scene_snapshots: list[Any] | None = None
    ) -> dict[str, Any]:
        current = self._revision(revision)
        compilation = self.parser.compile(current.ontology_md)
        if scene_snapshots:
            compilation.issues.extend(
                self._validate_scene_bindings(compilation.graph, scene_snapshots)
            )
        status = (
            OntologyRevisionStatus.READY
            if compilation.valid
            else OntologyRevisionStatus.FAILED
        )
        stored = current.model_copy(
            update={
                "status": status,
                "graph_json": compilation.graph.model_dump(mode="json"),
                "prompt_projection_json": compilation.prompt_projection,
                "validation_issues": [
                    item.model_dump(mode="json") for item in compilation.issues
                ],
                "content_hash": self._hash(compilation.graph.model_dump(mode="json"))
                if compilation.valid
                else None,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self.repository.save_compilation(stored)
        return {
            "revision": stored,
            "valid": compilation.valid,
            "issues": stored.validation_issues,
        }

    def build_snapshot(
        self,
        revision: int,
        scene_snapshots: list[Any] | None = None,
    ) -> OntologySnapshot:
        current = self._revision(revision)
        validated = self.validate(revision, scene_snapshots)
        current = validated["revision"]
        if (
            not validated["valid"]
            or current.graph_json is None
            or current.prompt_projection_json is None
            or current.content_hash is None
        ):
            raise OntologyServiceError(
                "ONTOLOGY_VALIDATION_FAILED", "本体文档校验未通过，无法发布。"
            )
        all_scene_snapshots = scene_snapshots or []
        source = list(current.source_scene_snapshots)
        active_source = source_snapshot_refs(all_scene_snapshots)
        if active_source and source != active_source:
            raise OntologyServiceError(
                "ONTOLOGY_SCENE_SYNC_REQUIRED",
                "已发布场景存在未接受的变更，请先同步场景后再发布 Ontology。",
            )
        active_by_id = {str(item.snapshot_id): item for item in all_scene_snapshots}
        missing = [
            item for item in source if str(item.get("snapshot_id")) not in active_by_id
        ]
        if missing:
            raise OntologyServiceError(
                "ONTOLOGY_SCENE_SYNC_REQUIRED",
                "关联场景已变更，请先同步场景后再发布 Ontology。",
            )
        selected_scene_snapshots = [
            active_by_id[str(item["snapshot_id"])]
            for item in source
            if item.get("snapshot_id") in active_by_id
        ]
        manual_graph = current.graph_json
        generated_graphs = [
            build_scene_fragment(item) for item in selected_scene_snapshots
        ]
        graph = merge_graphs(self._graph_from_json(manual_graph), *generated_graphs)
        prompt_projection = build_prompt_projection(graph)
        content = {
            "ontology_revision": current.revision,
            "graph": graph.model_dump(mode="json"),
            "prompt_projection": prompt_projection,
            "source_scene_snapshots": source,
            "compiler_version": self.compiler_version,
        }
        content_hash = self._hash(content)
        snapshot = OntologySnapshot(
            snapshot_id=f"onto_{content_hash[7:31]}",
            ontology_id=current.ontology_id,
            revision=current.revision,
            content_hash=content_hash,
            graph_json=graph.model_dump(mode="json"),
            prompt_projection_json=prompt_projection,
            source_scene_snapshots=source,
            compiler_version=self.compiler_version,
        )
        self.repository.save_snapshot(snapshot)
        return snapshot

    def scene_sync(
        self,
        *,
        scene_snapshots: list[Any],
        user_id: str,
        expected_revision: int | None = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Compare active Scene Snapshots and explicitly accept their versions."""
        current = self.draft(user_id=user_id)
        if expected_revision is not None and current.revision != expected_revision:
            raise OntologyConflictError(
                "ONTOLOGY_REVISION_CONFLICT",
                "本体文档已被其他用户更新，请刷新后再同步。",
            )
        previous = {
            str(item.get("scene_id")): item
            for item in current.source_scene_snapshots
            if item.get("scene_id")
        }
        source = source_snapshot_refs(scene_snapshots)
        incoming = {item["scene_id"]: item for item in source}
        changes = []
        for scene_id in sorted(set(previous) | set(incoming)):
            before, after = previous.get(scene_id), incoming.get(scene_id)
            if before is None:
                changes.append({"scene_id": scene_id, "kind": "added", "after": after})
            elif after is None:
                changes.append(
                    {"scene_id": scene_id, "kind": "removed", "before": before}
                )
            elif before.get("snapshot_id") != after.get("snapshot_id"):
                changes.append(
                    {
                        "scene_id": scene_id,
                        "kind": "changed",
                        "before": before,
                        "after": after,
                    }
                )
        result: dict[str, Any] = {
            "pending_count": len(changes),
            "changes": changes,
            "revision": current,
        }
        if not apply or not changes:
            return result
        draft = self.save_draft(
            markdown=current.ontology_md,
            user_id=user_id,
            expected_revision=current.revision,
        )
        draft = self.repository.save_source_scene_snapshots(draft.revision, source)
        result["revision"] = draft
        return result

    def activate(self, snapshot_id: str) -> OntologySnapshot:
        try:
            return self.repository.activate(snapshot_id)
        except OntologyRepositoryError as exc:
            raise OntologyServiceError(str(exc), str(exc)) from exc

    def active_snapshot(self) -> OntologySnapshot | None:
        return self.repository.active_snapshot()

    def graph(
        self,
        *,
        revision: int | None,
        user_id: str,
        scene_snapshots: list[Any] | None = None,
    ) -> dict[str, Any]:
        current = (
            self._revision(revision)
            if revision is not None
            else self.draft(user_id=user_id)
        )
        if current.graph_json is None:
            preview = self.preview(current.ontology_md)
            graph = self._graph_from_json(preview["graph"])
            issues = preview["issues"]
        else:
            graph = self._graph_from_json(current.graph_json)
            issues = current.validation_issues
        generated_graphs = [
            build_scene_fragment(item) for item in (scene_snapshots or [])
        ]
        merged_graph = merge_graphs(graph, *generated_graphs)
        return {
            "revision": current.revision,
            "valid": not issues,
            "graph": merged_graph.model_dump(mode="json"),
            "prompt_projection": build_prompt_projection(merged_graph),
            "issues": issues,
            "layout": self.repository.get_layout(current.revision),
        }

    def save_layout(
        self, revision: int, layout: dict[str, Any], user_id: str
    ) -> dict[str, Any]:
        return self.repository.save_layout(revision, layout, user_id)

    def list_revisions(self) -> list[OntologyRevision]:
        return self.repository.list_revisions()

    def list_snapshots(self) -> list[OntologySnapshot]:
        return self.repository.list_snapshots()

    @staticmethod
    def _validate_scene_bindings(
        graph, scene_snapshots: list[Any]
    ) -> list[dict[str, Any]]:
        """Validate semantic keys without exposing physical Scene runtime data."""
        from .schemas import OntologyValidationIssue

        available = {str(snapshot.scene_id): snapshot for snapshot in scene_snapshots}
        issues: list[OntologyValidationIssue] = []
        for edge in graph.edges:
            if edge.type != "scene_binding":
                continue
            snapshot = available.get(edge.source)
            if snapshot is None:
                issues.append(
                    OntologyValidationIssue(
                        code="INACTIVE_SOURCE_SCENE",
                        message=f"场景绑定引用的场景未处于 active：{edge.source}。",
                        path=edge.id,
                    )
                )
                continue
            semantic_key = str((edge.data or {}).get("semantic_key") or "")
            projection = snapshot.routing_projection or {}
            keys = {
                str(item.get("key"))
                for group in ("metrics", "dimensions")
                for item in projection.get(group, [])
                if isinstance(item, dict) and item.get("key")
            }
            if semantic_key not in keys:
                issues.append(
                    OntologyValidationIssue(
                        code="UNKNOWN_SCENE_SEMANTIC_KEY",
                        message=f"场景 {edge.source} 不存在语义键：{semantic_key}。",
                        path=edge.id,
                    )
                )
        return issues

    def _revision(self, revision: int) -> OntologyRevision:
        try:
            return self.repository.get_revision(revision)
        except OntologyRepositoryError as exc:
            raise OntologyServiceError(str(exc), str(exc)) from exc

    @staticmethod
    def _graph_from_json(value: dict[str, Any]):
        from .schemas import OntologyGraph

        return OntologyGraph.model_validate(value)

    @staticmethod
    def _hash(value: Any) -> str:
        serialized = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"


def create_default_ontology_service() -> OntologyLifecycleService:
    """Use DB-GPT metadata persistence when initialized; otherwise stay testable."""
    try:
        from dbgpt.storage.metadata import db

        if db.is_initialized:
            return OntologyLifecycleService(SqlOntologyRepository(db))
    except Exception:
        pass
    return OntologyLifecycleService(InMemoryOntologyRepository())


__all__ = [
    "OntologyConflictError",
    "OntologyLifecycleService",
    "OntologyServiceError",
    "create_default_ontology_service",
]
