"""Draft, validation, immutable Snapshot and activation lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Literal

from ..query.capabilities import semantic_keys
from .fragments import (
    build_scene_fragment,
    merge_graphs,
    scene_snapshot,
    source_snapshot_refs,
)
from .generator import (
    generate_ontology_markdown_from_scene_sources,
    scene_semantic_keys_from_source,
)
from .llm_generator import (
    OntologyLLMGenerationError,
    OntologyLLMGenerator,
    normalize_scene_sources,
)
from .llm_schemas import OntologyGenerationResult
from .llm_validator import OntologyLLMValidationError
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
    compiler_version = "2"

    def __init__(
        self,
        repository: InMemoryOntologyRepository | SqlOntologyRepository,
        parser: OntologyMarkdownParser | None = None,
        llm_generator: OntologyLLMGenerator | None = None,
        generation_mode: Literal["llm", "rules"] | None = None,
    ):
        self.repository = repository
        self.parser = parser or OntologyMarkdownParser()
        self.llm_generator = llm_generator
        self.generation_mode = generation_mode

    def draft(self, *, user_id: str) -> OntologyRevision:
        current = self.repository.ensure(created_by=user_id)
        normalized = with_managed_frontmatter(current.ontology_md, current.revision)
        if current.ontology_md == normalized:
            return current
        if current.status in {
            OntologyRevisionStatus.DRAFT,
            OntologyRevisionStatus.FAILED,
        }:
            try:
                return self.repository.save_draft(
                    markdown=normalized,
                    created_by=user_id,
                    expected_revision=current.revision,
                )
            except OntologyRepositoryError:
                pass
        return current.model_copy(update={"ontology_md": normalized})

    def save_draft(
        self, *, markdown: str, user_id: str, expected_revision: int | None = None
    ) -> OntologyRevision:
        try:
            managed = with_managed_frontmatter(markdown, expected_revision or 0)
            saved = self.repository.save_draft(
                markdown=managed,
                created_by=user_id,
                expected_revision=expected_revision,
            )
            managed = with_managed_frontmatter(saved.ontology_md, saved.revision)
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
                "ONTOLOGY_VALIDATION_FAILED", "本体文档存在格式提醒，暂不能发布。"
            )
        all_scene_snapshots = scene_snapshots or []
        source = list(current.source_scene_snapshots)
        active_source = source_snapshot_refs(all_scene_snapshots)
        if active_source and source != active_source:
            raise OntologyServiceError(
                "ONTOLOGY_SOURCE_REFRESH_REQUIRED",
                (
                    "已发布场景语义文档版本发生变化，"
                    "请先重新生成本体草稿后再发布 Ontology。"
                ),
            )
        active_by_id = {
            str(snapshot.snapshot_id): item
            for item in all_scene_snapshots
            if (snapshot := scene_snapshot(item)) is not None
        }
        missing = [
            item for item in source if str(item.get("snapshot_id")) not in active_by_id
        ]
        if missing:
            raise OntologyServiceError(
                "ONTOLOGY_SOURCE_REFRESH_REQUIRED",
                "关联场景语义文档版本已变化，请先重新生成本体草稿后再发布 Ontology。",
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

    def generate_from_active_scenes(
        self,
        *,
        scene_snapshots: list[Any],
        user_id: str,
        expected_revision: int | None = None,
        apply: bool = False,
        mode: Literal["llm", "rules"] | None = None,
    ) -> dict[str, Any]:
        """Generate a reviewable global Ontology draft from active Scene documents."""
        current, source, result = self._generation_base(
            scene_snapshots=scene_snapshots,
            user_id=user_id,
            expected_revision=expected_revision,
        )
        if not apply:
            return result
        selected_mode = self._selected_generation_mode(mode, default="rules")
        if selected_mode == "llm":
            raise OntologyServiceError(
                "ONTOLOGY_LLM_UNAVAILABLE",
                "LLM 生成需要使用异步服务入口。",
            )
        markdown = generate_ontology_markdown_from_scene_sources(
            scene_snapshots, revision=current.revision
        )
        draft = self.save_draft(
            markdown=markdown,
            user_id=user_id,
            expected_revision=current.revision,
        )
        draft = self.repository.save_source_scene_snapshots(draft.revision, source)
        result["revision"] = draft
        result["generation"] = self._generation_summary_payload(
            self._rules_generation_summary(scene_snapshots, markdown)
        )
        return result

    async def generate_from_active_scenes_async(
        self,
        *,
        scene_snapshots: list[Any],
        user_id: str,
        expected_revision: int | None = None,
        apply: bool = False,
        mode: Literal["llm", "rules"] | None = None,
    ) -> dict[str, Any]:
        """Async LLM-capable draft generation entrypoint."""
        current, source, result = self._generation_base(
            scene_snapshots=scene_snapshots,
            user_id=user_id,
            expected_revision=expected_revision,
        )
        if not apply:
            return result
        selected_mode = self._selected_generation_mode(mode, default="llm")
        try:
            if selected_mode == "llm":
                if self.llm_generator is None:
                    raise OntologyServiceError(
                        "ONTOLOGY_LLM_UNAVAILABLE",
                        "模型暂时不可用，未生成草稿。",
                    )
                generation = await self.llm_generator.generate(
                    normalize_scene_sources(scene_snapshots),
                    revision=current.revision,
                )
                markdown = generation.markdown
            else:
                markdown = generate_ontology_markdown_from_scene_sources(
                    scene_snapshots, revision=current.revision
                )
                generation = self._rules_generation_summary(scene_snapshots, markdown)
        except OntologyLLMGenerationError as exc:
            raise OntologyServiceError(exc.code, str(exc)) from exc
        except OntologyLLMValidationError as exc:
            raise OntologyServiceError(exc.code, str(exc)) from exc
        draft = self.save_draft(
            markdown=markdown,
            user_id=user_id,
            expected_revision=current.revision,
        )
        draft = self.repository.save_source_scene_snapshots(draft.revision, source)
        result["revision"] = draft
        result["generation"] = self._generation_summary_payload(generation)
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

    def _generation_base(
        self,
        *,
        scene_snapshots: list[Any],
        user_id: str,
        expected_revision: int | None,
    ) -> tuple[OntologyRevision, list[dict[str, str]], dict[str, Any]]:
        current = self.draft(user_id=user_id)
        if expected_revision is not None and current.revision != expected_revision:
            raise OntologyConflictError(
                "ONTOLOGY_REVISION_CONFLICT",
                "本体文档已被其他用户更新，请刷新后再生成本体草稿。",
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
        return (
            current,
            source,
            {
                "pending_count": len(changes),
                "changes": changes,
                "revision": current,
            },
        )

    def _selected_generation_mode(
        self,
        requested: Literal["llm", "rules"] | None,
        *,
        default: Literal["llm", "rules"],
    ) -> Literal["llm", "rules"]:
        if requested:
            return requested
        if self.generation_mode:
            return self.generation_mode
        env_mode = os.getenv("DATAMAN_ONTOLOGY_GENERATION_MODE", "").strip().lower()
        if env_mode in {"llm", "rules"}:
            return env_mode  # type: ignore[return-value]
        return default

    def _rules_generation_summary(
        self, scene_snapshots: list[Any], markdown: str
    ) -> OntologyGenerationResult:
        compilation = self.parser.compile(markdown)
        nodes = compilation.graph.nodes
        edges = compilation.graph.edges
        return OntologyGenerationResult(
            markdown=markdown,
            mode="rules",
            source_count=len(scene_snapshots),
            entity_count=len([node for node in nodes if node.type == "entity"]),
            metric_count=len([node for node in nodes if node.type == "metric"]),
            relation_count=len([edge for edge in edges if edge.type == "relation"]),
            analysis_rule_count=len(
                [node for node in nodes if node.type == "analysis_rule"]
            ),
            warnings=[],
        )

    @staticmethod
    def _generation_summary_payload(
        generation: OntologyGenerationResult,
    ) -> dict[str, Any]:
        return {
            "mode": generation.mode,
            "source_count": generation.source_count,
            "entity_count": generation.entity_count,
            "metric_count": generation.metric_count,
            "relation_count": generation.relation_count,
            "analysis_rule_count": generation.analysis_rule_count,
            "warnings": generation.warnings,
        }

    @staticmethod
    def _validate_scene_bindings(
        graph, scene_snapshots: list[Any]
    ) -> list[dict[str, Any]]:
        """Validate semantic keys without exposing physical Scene runtime data."""
        from .schemas import OntologyValidationIssue

        available = {
            str(snapshot.scene_id): source
            for source in scene_snapshots
            if (snapshot := scene_snapshot(source)) is not None
        }
        issues: list[OntologyValidationIssue] = []
        for edge in graph.edges:
            if edge.type != "scene_binding":
                continue
            source = available.get(edge.source)
            if source is None:
                issues.append(
                    OntologyValidationIssue(
                        code="INACTIVE_SOURCE_SCENE",
                        message=f"场景绑定引用的场景未处于 active：{edge.source}。",
                        path=edge.id,
                    )
                )
                continue
            semantic_key = str((edge.data or {}).get("semantic_key") or "")
            keys = scene_semantic_keys_from_source(source)
            if not keys and (snapshot := scene_snapshot(source)) is not None:
                keys = semantic_keys(snapshot)
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
    llm_generator = _default_ontology_llm_generator()
    try:
        from dbgpt.storage.metadata import db

        if db.is_initialized:
            return OntologyLifecycleService(
                SqlOntologyRepository(db), llm_generator=llm_generator
            )
    except Exception:
        pass
    return OntologyLifecycleService(
        InMemoryOntologyRepository(), llm_generator=llm_generator
    )


def _default_ontology_llm_generator(
    model_name: str | None = None,
) -> OntologyLLMGenerator | None:
    """Build the default DB-GPT LLM-backed Ontology generator when available."""
    try:
        from dbgpt._private.config import Config
        from dbgpt.component import ComponentType
        from dbgpt.core import ModelMessage, ModelMessageRoleType, ModelRequest
        from dbgpt.model.cluster import WorkerManagerFactory
        from dbgpt.model.cluster.client import DefaultLLMClient
    except Exception:
        return None

    async def generate(prompt: str) -> str:
        try:
            cfg = Config()
            llm_client = DefaultLLMClient(
                cfg.SYSTEM_APP.get_component(
                    ComponentType.WORKER_MANAGER_FACTORY, WorkerManagerFactory
                ).create(),
                auto_convert_message=True,
            )
            models = await llm_client.models()
            selected_model = model_name or (models[0].model if models else None)
            if not selected_model:
                raise RuntimeError("No models available for Ontology generator")
            request = ModelRequest.build_request(
                selected_model,
                messages=[
                    ModelMessage(
                        role=ModelMessageRoleType.HUMAN,
                        content=prompt,
                    )
                ],
                temperature=0,
            )
            response = await llm_client.generate(request)
            if not response.success or not response.has_text:
                raise RuntimeError("Ontology generator model generation failed")
            return response.text
        except Exception as exc:
            raise OntologyLLMGenerationError(
                "ONTOLOGY_LLM_UNAVAILABLE", f"模型暂时不可用，未生成草稿：{exc}"
            ) from exc

    return OntologyLLMGenerator(generate)


__all__ = [
    "OntologyConflictError",
    "OntologyLifecycleService",
    "OntologyServiceError",
    "create_default_ontology_service",
]
