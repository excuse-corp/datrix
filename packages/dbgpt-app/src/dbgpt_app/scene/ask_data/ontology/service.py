"""Draft, validation, immutable Snapshot and activation lifecycle."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from ..query.capabilities import semantic_keys
from ..scene.markdown import SemanticMarkdownParser
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
from .neo4j_store import Neo4jOntologyError, Neo4jOntologyStore
from .neo4j_reader import Neo4jOntologyReader
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


logger = logging.getLogger(__name__)


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "0").lower() in {"1", "true", "yes", "on"}


def _collection_difference_counts(left: Any, right: Any) -> dict[str, int]:
    def fingerprints(value: Any) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {
            json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            for item in value
        }

    left_set = fingerprints(left)
    right_set = fingerprints(right)
    return {
        "neo4j_only": len(left_set - right_set),
        "json_only": len(right_set - left_set),
    }


class OntologyLifecycleService:
    compiler_version = "2"
    review_required_node_types = {"metric", "analysis_path"}
    review_required_edge_types = {"scene_binding", "cross_scene_join"}

    def __init__(
        self,
        repository: InMemoryOntologyRepository | SqlOntologyRepository,
        parser: OntologyMarkdownParser | None = None,
        llm_generator: OntologyLLMGenerator | None = None,
        generation_mode: Literal["llm", "rules"] | None = None,
        neo4j_store: Neo4jOntologyStore | None = None,
    ):
        self.repository = repository
        self.parser = parser or OntologyMarkdownParser()
        self.llm_generator = llm_generator
        self.generation_mode = generation_mode
        self.neo4j_store = neo4j_store or Neo4jOntologyStore()
        self.neo4j_reader = Neo4jOntologyReader(self.neo4j_store)

    def analysis_snapshot(self) -> OntologySnapshot | None:
        return self.analysis_snapshot_with_source()[0]

    def analysis_snapshot_with_source(
        self,
        *,
        scene_ids: list[str] | None = None,
        result_columns: list[dict[str, Any]] | None = None,
        result_lineage: list[dict[str, Any]] | None = None,
        result_profiles: list[dict[str, Any]] | None = None,
        authorized_scene_ids: list[str] | None = None,
        is_platform_admin: bool = False,
    ) -> tuple[OntologySnapshot | None, str]:
        """Read one active snapshot for post-query analysis.

        Neo4j is the runtime source when enabled.  The relational JSON
        snapshot remains an explicit fallback so a graph outage cannot make a
        successful query unusable.
        """
        requested_scene_ids = {str(item) for item in (scene_ids or []) if str(item)}
        if not requested_scene_ids:
            requested_scene_ids = {
                str(column.get("scene_id"))
                for column in (result_columns or [])
                if isinstance(column, dict) and column.get("scene_id")
            }
        if is_platform_admin:
            visible_scene_ids = requested_scene_ids
        else:
            authorized = {
                str(item) for item in (authorized_scene_ids or []) if str(item)
            }
            visible_scene_ids = requested_scene_ids & authorized
            # An unscoped or unauthorized result must never trigger the
            # reader's full-snapshot mode. The post-query analysis context has
            # no safe scene boundary in that case, so omit ontology context.
            if not visible_scene_ids:
                return None, "no_authorized_scene"
        if self.neo4j_store.enabled:
            try:
                snapshot_id = self.neo4j_reader.active_snapshot_id()
                if snapshot_id:
                    semantic_keys = [
                        str(value)
                        for column in (result_columns or [])
                        for value in (column.get("key"), column.get("semantic_key"))
                        if value
                    ]
                    field_refs = [
                        str(item.get("field_ref"))
                        for item in (result_lineage or [])
                        if item.get("field_ref") and item.get("resolved", True)
                    ]
                    scene_snapshot_ids = [
                        str(column.get("scene_snapshot_id"))
                        for column in (result_columns or [])
                        if column.get("scene_snapshot_id")
                    ]
                    graph_snapshot = self.neo4j_reader.read_snapshot(
                        snapshot_id,
                        scene_ids=sorted(visible_scene_ids),
                        scene_snapshot_ids=sorted(set(scene_snapshot_ids)),
                        semantic_keys=sorted(set(semantic_keys)),
                        field_refs=sorted(set(field_refs)),
                    )
                    if _env_enabled("DATAMAN_NEO4J_COMPARE_JSON"):
                        try:
                            self._log_analysis_snapshot_comparison(
                                graph_snapshot,
                                snapshot_id=snapshot_id,
                                scene_ids=sorted(visible_scene_ids),
                                result_columns=result_columns or [],
                                result_lineage=result_lineage or [],
                                result_profiles=result_profiles or [],
                                authorized_scene_ids=sorted(
                                    set(authorized_scene_ids or [])
                                ),
                                is_platform_admin=is_platform_admin,
                            )
                        except Exception as exc:
                            # Comparison is an opt-in shadow check. A missing
                            # JSON copy must not break successful analysis.
                            logger.warning(
                                "Ontology Neo4j/JSON comparison failed (%s)",
                                type(exc).__name__,
                            )
                    return graph_snapshot, "neo4j"
            except Neo4jOntologyError:
                if os.getenv("DATAMAN_NEO4J_FALLBACK_JSON", "1").lower() not in {
                    "1", "true", "yes", "on"
                }:
                    raise
                return self.active_snapshot(), "metadata_json_fallback"
        return self.active_snapshot(), "metadata_json"

    def _log_analysis_snapshot_comparison(
        self,
        neo4j_snapshot: OntologySnapshot,
        *,
        snapshot_id: str,
        scene_ids: list[str],
        result_columns: list[dict[str, Any]],
        result_lineage: list[dict[str, Any]],
        result_profiles: list[dict[str, Any]],
        authorized_scene_ids: list[str],
        is_platform_admin: bool,
    ) -> None:
        from .context import relevant_ontology_projection

        json_snapshot = self.repository.get_snapshot(snapshot_id)
        common = {
            "scene_ids": scene_ids,
            "result_columns": result_columns,
            "result_lineage": result_lineage,
            "result_profiles": result_profiles,
            "authorized_scene_ids": authorized_scene_ids,
            "is_platform_admin": is_platform_admin,
        }
        neo4j_projection = relevant_ontology_projection(
            neo4j_snapshot,
            retrieval_source="neo4j",
            **common,
        )
        json_projection = relevant_ontology_projection(
            json_snapshot,
            retrieval_source="metadata_json",
            **common,
        )
        categories = (
            "nodes",
            "edges",
            "entities",
            "metrics",
            "analysis_dimensions",
            "metric_relations",
            "cross_scene_analysis_paths",
            "result_analysis_rules",
            "matched_bindings",
            "unmatched_columns",
            "retrieval_trace",
        )
        differences = {
            category: _collection_difference_counts(
                neo4j_projection.get(category, []),
                json_projection.get(category, []),
            )
            for category in categories
        }
        logger.info(
            "Ontology Neo4j/JSON shadow comparison: %s",
            json.dumps(
                {
                    "event": "ontology_dual_read_comparison",
                    "snapshot_id": snapshot_id,
                    "snapshot_id_matches": (
                        neo4j_snapshot.snapshot_id == json_snapshot.snapshot_id
                    ),
                    "equivalent": (
                        neo4j_snapshot.snapshot_id == json_snapshot.snapshot_id
                        and all(
                            not counts["neo4j_only"]
                            and not counts["json_only"]
                            for counts in differences.values()
                        )
                    ),
                    "differences": differences,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

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
            self._append_event(
                saved.revision,
                event_type="draft_saved",
                actor_id=user_id,
                content_hash=self._hash(saved.ontology_md),
                source_scene_snapshots=saved.source_scene_snapshots,
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
            compilation.issues.extend(
                self._validate_metric_sources(
                    compilation.graph, current.ontology_md, scene_snapshots
                )
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

    def clean_invalid_metric_sources(
        self,
        revision: int,
        *,
        actor_id: str,
        scene_snapshots: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Remove draft metrics without an explicit scene semantic source.

        This operation only edits the draft Markdown. It does not modify an
        active snapshot or any Scene document. Bindings, metric relations and
        analysis-dimension references to removed metrics are removed together
        so the resulting draft cannot retain dangling semantic references.
        """
        current = self._revision(revision)
        sources = list(scene_snapshots or [])
        compilation = self.parser.compile(current.ontology_md)
        valid_metric_ids = self._valid_metric_ids(
            compilation.graph, current.ontology_md, sources
        )
        metric_ids = {
            str(node.id) for node in compilation.graph.nodes if node.type == "metric"
        }
        referenced_metric_ids = _metric_reference_ids(current.ontology_md)
        invalid_metric_ids = sorted(
            (metric_ids | referenced_metric_ids) - valid_metric_ids
        )
        cleaned_markdown = _remove_invalid_metric_references(
            current.ontology_md, set(invalid_metric_ids)
        )
        if cleaned_markdown == current.ontology_md:
            review = self.review_state(
                revision, user_id=actor_id, scene_snapshots=sources
            )
            return {
                "revision": current,
                "removed_metric_ids": [],
                "removed_count": 0,
                "review_state": review,
            }
        saved = self.save_draft(
            markdown=cleaned_markdown,
            user_id=actor_id,
            expected_revision=revision,
        )
        if current.source_scene_snapshots:
            saved = self.repository.save_source_scene_snapshots(
                saved.revision, current.source_scene_snapshots
            )
        self._append_event(
            saved.revision,
            event_type="invalid_metric_sources_cleaned",
            actor_id=actor_id,
            content_hash=self._hash(saved.ontology_md),
            source_scene_snapshots=saved.source_scene_snapshots,
            metadata={
                "removed_metric_count": len(invalid_metric_ids),
                "removed_metric_ids": invalid_metric_ids,
            },
        )
        review = self.review_state(
            saved.revision, user_id=actor_id, scene_snapshots=sources
        )
        return {
            "revision": saved,
            "removed_metric_ids": invalid_metric_ids,
            "removed_count": len(invalid_metric_ids),
            "review_state": review,
        }

    def build_snapshot(
        self,
        revision: int,
        scene_snapshots: list[Any] | None = None,
        *,
        actor_id: str = "system",
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
        graph, prompt_projection, content, source, review_state = self._publication_parts(
            current, scene_snapshots or []
        )
        content_hash = self._hash(content)
        pending = [item for item in review_state["items"] if item["required"] and item["status"] != "confirmed"]
        if pending:
            identifiers = ", ".join(item["target_id"] for item in pending[:8])
            raise OntologyServiceError(
                "ONTOLOGY_REVIEW_INCOMPLETE",
                f"仍有 {len(pending)} 个关键对象未审核：{identifiers}。",
            )
        approval = self._latest_revision_approval(current.revision)
        if not approval or approval.get("content_hash") != content_hash:
            raise OntologyServiceError(
                "ONTOLOGY_APPROVAL_REQUIRED",
                "当前修订尚未通过审核，或审核后内容/来源已变化，请重新批准后发布。",
            )
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
        try:
            self.neo4j_store.sync_snapshot(snapshot)
        except Neo4jOntologyError as exc:
            raise OntologyServiceError("ONTOLOGY_NEO4J_SYNC_FAILED", str(exc)) from exc
        self._append_event(
            current.revision,
            event_type="snapshot_built",
            actor_id=actor_id,
            target_type="snapshot",
            target_id=snapshot.snapshot_id,
            target_fingerprint=content_hash,
            decision="built",
            content_hash=content_hash,
            source_scene_snapshots=source,
        )
        return snapshot

    def review_state(
        self,
        revision: int,
        *,
        user_id: str,
        scene_snapshots: list[Any] | None = None,
    ) -> dict[str, Any]:
        current = self._revision(revision)
        try:
            _, _, content, _, state = self._publication_parts(
                current, scene_snapshots or []
            )
            current_content_hash = self._hash(content)
        except OntologyServiceError as exc:
            graph_data = self.graph(
                revision=revision,
                user_id=user_id,
                scene_snapshots=scene_snapshots,
            )["graph"]
            state = self._review_state_for_graph(
                revision, self._graph_from_json(graph_data)
            )
            current_content_hash = None
            state["blocking_issue"] = {"code": exc.code, "message": str(exc)}
        approval = self._latest_revision_approval(revision)
        state.update(
            {
                "revision": revision,
                "current_content_hash": current_content_hash,
                "approved": bool(
                    current_content_hash
                    and approval
                    and approval.get("content_hash") == current_content_hash
                ),
                "approved_by": approval.get("actor_id") if approval else None,
                "approved_at": approval.get("created_at") if approval else None,
                "events": self.repository.list_events(revision),
            }
        )
        return state

    def review_object(
        self,
        revision: int,
        *,
        target_type: Literal["node", "edge"],
        target_id: str,
        decision: Literal["confirmed", "rejected", "candidate"],
        comment: str,
        actor_id: str,
        expected_fingerprint: str,
        scene_snapshots: list[Any] | None = None,
    ) -> dict[str, Any]:
        if decision == "rejected" and not comment.strip():
            raise OntologyServiceError(
                "ONTOLOGY_REVIEW_COMMENT_REQUIRED", "驳回对象时请填写审核意见。"
            )
        state = self.review_state(
            revision,
            user_id=actor_id,
            scene_snapshots=scene_snapshots,
        )
        item = next(
            (
                item
                for item in state["items"]
                if item["target_type"] == target_type
                and item["target_id"] == target_id
            ),
            None,
        )
        if item is None:
            raise OntologyServiceError(
                "ONTOLOGY_REVIEW_TARGET_NOT_FOUND", "审核对象不存在或已不在当前图谱中。"
            )
        if item["fingerprint"] != expected_fingerprint:
            raise OntologyConflictError(
                "ONTOLOGY_REVIEW_TARGET_CHANGED",
                "该对象在审核期间已发生变化，请刷新对象后重新审核。",
            )
        if decision == "confirmed" and item["object_type"] in {
            "analysis_path",
            "cross_scene_join",
        }:
            graph = self.graph(
                revision=revision,
                user_id=actor_id,
                scene_snapshots=scene_snapshots,
            )["graph"]
            objects = graph["nodes"] if target_type == "node" else graph["edges"]
            target = next(
                (value for value in objects if value["id"] == target_id), {}
            )
            missing = self._missing_bridge_review_fields(
                target_type, item["object_type"], target.get("data") or {}
            )
            if missing:
                raise OntologyServiceError(
                    "ONTOLOGY_REVIEW_EVIDENCE_INCOMPLETE",
                    f"跨场景桥接缺少必要的分析契约信息：{'、'.join(missing)}。",
                )
        event = self._append_event(
            revision,
            event_type="object_review",
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            target_fingerprint=expected_fingerprint,
            decision=decision,
            comment=comment,
            source_scene_snapshots=self._revision(revision).source_scene_snapshots,
        )
        return {"event": event, "review_state": self.review_state(
            revision, user_id=actor_id, scene_snapshots=scene_snapshots
        )}

    def exclude_review_object(
        self,
        revision: int,
        *,
        target_type: Literal["node", "edge"],
        target_id: str,
        comment: str,
        actor_id: str,
        expected_fingerprint: str,
        scene_snapshots: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Remove a review candidate from the current draft.

        Exclusion is deliberately separate from rejection.  Rejection keeps
        the object in the draft and blocks approval; exclusion removes the
        Markdown row and all known references, while also recording a durable
        audit event.  Generated scene objects that have no Markdown row are
        suppressed by that event until the next draft regeneration.
        """
        if not comment.strip():
            raise OntologyServiceError(
                "ONTOLOGY_REVIEW_COMMENT_REQUIRED", "不纳入本体时请填写原因。"
            )
        current = self._revision(revision)
        state = self.review_state(
            revision,
            user_id=actor_id,
            scene_snapshots=scene_snapshots,
        )
        item = next(
            (
                value
                for value in state["items"]
                if value["target_type"] == target_type
                and value["target_id"] == target_id
            ),
            None,
        )
        if item is None:
            raise OntologyServiceError(
                "ONTOLOGY_REVIEW_TARGET_NOT_FOUND", "审核对象不存在或已不在当前图谱中。"
            )
        if item["fingerprint"] != expected_fingerprint:
            raise OntologyConflictError(
                "ONTOLOGY_REVIEW_TARGET_CHANGED",
                "该对象在审核期间已发生变化，请刷新对象后重新审核。",
            )
        cleaned_markdown = _remove_review_object_references(
            current.ontology_md,
            target_type=target_type,
            target_id=target_id,
            object_type=str(item.get("object_type") or ""),
            object_value=item.get("object") or {},
        )
        # Saving even when the Markdown has no matching row invalidates any
        # prior approval and lets the exclusion event suppress generated
        # fragments in graph/publication views.
        saved = self.save_draft(
            markdown=cleaned_markdown,
            user_id=actor_id,
            expected_revision=revision,
        )
        if current.source_scene_snapshots:
            saved = self.repository.save_source_scene_snapshots(
                saved.revision, current.source_scene_snapshots
            )
        event = self._append_event(
            saved.revision,
            event_type="object_excluded",
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            target_fingerprint=expected_fingerprint,
            decision="excluded",
            comment=comment,
            source_scene_snapshots=saved.source_scene_snapshots,
            metadata={
                "object_type": item.get("object_type"),
                "object": item.get("object"),
                "markdown_changed": cleaned_markdown != current.ontology_md,
            },
        )
        review = self.review_state(
            saved.revision,
            user_id=actor_id,
            scene_snapshots=scene_snapshots,
        )
        return {
            "event": event,
            "revision": saved,
            "removed_target_type": target_type,
            "removed_target_id": target_id,
            "review_state": review,
        }

    @staticmethod
    def _missing_bridge_review_fields(
        target_type: str, object_type: str, data: dict[str, Any]
    ) -> list[str]:
        if object_type == "analysis_path":
            scenes = data.get("scenes") or []
            required = {
                "恰好两个不同的涉及场景": len(set(scenes)) == 2,
                "关系类型": bool(data.get("relation_type")),
                "关联键": bool(data.get("join_keys")),
                "映射依据": bool(data.get("mapping_source")),
                "左右时间依据": bool(
                    data.get("left_time_basis") and data.get("right_time_basis")
                ),
                "时间对齐条件": bool(data.get("time_alignment_conditions")),
                "粒度对齐条件": bool(data.get("grain_alignment")),
                "允许分析": bool(data.get("allowed_analyses")),
                "禁止推断": bool(data.get("forbidden_inferences")),
                "证据": bool(data.get("evidence")),
            }
        elif target_type == "edge" and object_type == "cross_scene_join":
            scenes = data.get("scenes") or []
            required = {
                "恰好两个不同的涉及场景": len(set(scenes)) == 2,
                "业务实体": bool(data.get("entity")),
                "关系类型": bool(data.get("relation_type")),
                "关联键": bool(data.get("business_key")),
                "映射依据": bool(data.get("mapping_source")),
                "左右时间依据": bool(
                    data.get("left_time_basis") and data.get("right_time_basis")
                ),
                "时间对齐条件": bool(data.get("time_alignment_conditions")),
                "粒度对齐条件": bool(data.get("grain_alignment")),
                "允许分析": bool(data.get("allowed_analyses")),
                "禁止推断": bool(data.get("forbidden_inferences")),
                "证据": bool(data.get("evidence")),
            }
        else:
            return []
        return [label for label, present in required.items() if not present]

    def approve_revision(
        self,
        revision: int,
        *,
        actor_id: str,
        expected_content_hash: str,
        scene_snapshots: list[Any] | None = None,
    ) -> dict[str, Any]:
        validated = self.validate(revision, scene_snapshots)
        if not validated["valid"]:
            raise OntologyServiceError(
                "ONTOLOGY_VALIDATION_FAILED", "本体文档存在检查问题，暂不能批准。"
            )
        current = validated["revision"]
        graph, _, content, source, state = self._publication_parts(
            current, scene_snapshots or []
        )
        content_hash = self._hash(content)
        if content_hash != expected_content_hash:
            raise OntologyConflictError(
                "ONTOLOGY_REVISION_CHANGED",
                "本体或来源版本已变化，请刷新后重新审核。",
            )
        pending = [item for item in state["items"] if item["required"] and item["status"] != "confirmed"]
        if pending:
            identifiers = ", ".join(item["target_id"] for item in pending[:8])
            raise OntologyServiceError(
                "ONTOLOGY_REVIEW_INCOMPLETE",
                f"仍有 {len(pending)} 个关键对象未审核：{identifiers}。",
            )
        event = self._append_event(
            revision,
            event_type="revision_approved",
            actor_id=actor_id,
            target_type="revision",
            target_id=str(revision),
            target_fingerprint=content_hash,
            decision="approved",
            content_hash=content_hash,
            source_scene_snapshots=source,
        )
        return {"event": event, "review_state": self.review_state(
            revision, user_id=actor_id, scene_snapshots=scene_snapshots
        )}

    def _publication_parts(
        self, current: OntologyRevision, scene_snapshots: list[Any]
    ) -> tuple[Any, dict[str, Any], dict[str, Any], list[dict[str, str]], dict[str, Any]]:
        source = list(current.source_scene_snapshots)
        active_source = source_snapshot_refs(scene_snapshots)
        if active_source and source != active_source:
            raise OntologyServiceError(
                "ONTOLOGY_SOURCE_REFRESH_REQUIRED",
                "已发布场景语义文档版本发生变化，请先重新生成本体草稿后再发布 Ontology。",
            )
        active_by_id = {
            str(snapshot.snapshot_id): item
            for item in scene_snapshots
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
        base_graph = current.graph_json
        if base_graph is None:
            base_graph = self.preview(current.ontology_md)["graph"]
        generated_graphs = [
            build_scene_fragment(item) for item in selected_scene_snapshots
        ]
        raw_graph = merge_graphs(self._graph_from_json(base_graph), *generated_graphs)
        raw_graph = self._without_excluded_objects(current.revision, raw_graph)
        state = self._review_state_for_graph(current.revision, raw_graph)
        graph = self._graph_with_review_status(raw_graph, state["items"])
        prompt_projection = build_prompt_projection(graph)
        graph_json = graph.model_dump(mode="json")
        content = {
            "ontology_revision": current.revision,
            "graph": graph_json,
            "prompt_projection": prompt_projection,
            "source_scene_snapshots": source,
            "compiler_version": self.compiler_version,
        }
        return graph, prompt_projection, content, source, state

    def _review_state_for_graph(self, revision: int, graph: Any) -> dict[str, Any]:
        events = self.repository.list_events(revision)
        latest: dict[tuple[str, str, str], dict[str, Any]] = {}
        for event in events:
            if event.get("event_type") != "object_review":
                continue
            key = (
                str(event.get("target_type") or ""),
                str(event.get("target_id") or ""),
                str(event.get("target_fingerprint") or ""),
            )
            latest[key] = event
        items: list[dict[str, Any]] = []
        for target_type, objects in (("node", graph.nodes), ("edge", graph.edges)):
            for item in objects:
                value = item.model_dump(mode="json")
                fingerprint = self._hash(value)
                event = latest.get((target_type, item.id, fingerprint))
                decision = str(event.get("decision")) if event else "candidate"
                required = (
                    item.type in self.review_required_node_types
                    if target_type == "node"
                    else item.type in self.review_required_edge_types
                )
                items.append(
                    {
                        "target_type": target_type,
                        "target_id": item.id,
                        "object_type": item.type,
                        "name": item.name,
                        "object": value,
                        "fingerprint": fingerprint,
                        "status": decision,
                        "required": required,
                        "comment": event.get("comment", "") if event else "",
                        "reviewer_id": event.get("actor_id") if event else None,
                        "reviewed_at": event.get("created_at") if event else None,
                    }
                )
        required = [item for item in items if item["required"]]
        return {
            "items": items,
            "required_count": len(required),
            "confirmed_required_count": sum(
                item["status"] == "confirmed" for item in required
            ),
            "pending_count": sum(
                item["status"] != "confirmed" for item in required
            ),
        }

    def _without_excluded_objects(self, revision: int, graph: Any) -> Any:
        excluded_events = _excluded_review_events(self.repository.list_events(revision))
        if not excluded_events:
            return graph
        excluded = {
            (str(event.get("target_type") or ""), str(event.get("target_id") or ""))
            for event in excluded_events
        }
        excluded_nodes = {
            node.id
            for node in graph.nodes
            if ("node", node.id) in excluded
            or _matches_excluded_generated_metric(node, excluded_events)
        }
        return graph.model_copy(
            update={
                "nodes": [
                    node
                    for node in graph.nodes
                    if node.id not in excluded_nodes
                ],
                "edges": [
                    edge
                    for edge in graph.edges
                    if (
                        ("edge", edge.id) not in excluded
                        and not _matches_excluded_generated_edge(
                            edge, excluded_events
                        )
                        and edge.source not in excluded_nodes
                        and edge.target not in excluded_nodes
                    )
                ],
            }
        )

    @staticmethod
    def _graph_with_review_status(graph: Any, items: list[dict[str, Any]]) -> Any:
        from .schemas import OntologyGraph

        statuses = {
            (item["target_type"], item["target_id"]): item for item in items
        }
        value = graph.model_dump(mode="json")
        for node in value.get("nodes", []):
            item = statuses.get(("node", node["id"]), {})
            decision = item.get("status", "candidate")
            data = node.setdefault("data", {})
            data["review_status"] = decision
            data["review_required"] = bool(item.get("required"))
            if node.get("type") == "analysis_path":
                data["status"] = decision
        for edge in value.get("edges", []):
            item = statuses.get(("edge", edge["id"]), {})
            decision = item.get("status", "candidate")
            data = edge.setdefault("data", {})
            data["review_status"] = decision
            data["review_required"] = bool(item.get("required"))
            if edge.get("type") == "cross_scene_join":
                data["status"] = decision
        return OntologyGraph.model_validate(value)

    def _latest_revision_approval(self, revision: int) -> dict[str, Any] | None:
        return next(
            (
                event
                for event in reversed(self.repository.list_events(revision))
                if event.get("event_type") == "revision_approved"
                and event.get("decision") == "approved"
            ),
            None,
        )

    def _append_event(
        self,
        revision: int,
        *,
        event_type: str,
        actor_id: str,
        target_type: str | None = None,
        target_id: str | None = None,
        target_fingerprint: str | None = None,
        decision: str | None = None,
        comment: str = "",
        content_hash: str | None = None,
        source_scene_snapshots: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.repository.append_event(
            {
                "event_id": uuid4().hex,
                "ontology_id": "global_business",
                "revision": revision,
                "event_type": event_type,
                "target_type": target_type,
                "target_id": target_id,
                "target_fingerprint": target_fingerprint,
                "decision": decision,
                "comment": comment,
                "actor_id": actor_id,
                "content_hash": content_hash,
                "source_scene_snapshots": source_scene_snapshots or [],
                "metadata": metadata or {},
                "created_at": datetime.now(timezone.utc),
            }
        )

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
        self._append_event(
            draft.revision,
            event_type="draft_generated",
            actor_id=user_id,
            content_hash=self._hash(draft.ontology_md),
            source_scene_snapshots=source,
            comment=selected_mode,
        )
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
        self._append_event(
            draft.revision,
            event_type="draft_generated",
            actor_id=user_id,
            content_hash=self._hash(draft.ontology_md),
            source_scene_snapshots=source,
            comment=selected_mode,
        )
        result["revision"] = draft
        result["generation"] = self._generation_summary_payload(generation)
        return result

    def activate(
        self, snapshot_id: str, *, actor_id: str = "system"
    ) -> OntologySnapshot:
        previous_snapshot_id = None
        try:
            previous = self.repository.active_snapshot()
            previous_snapshot_id = self.neo4j_store.active_snapshot_id()
            self.neo4j_store.activate_snapshot(snapshot_id)
            activated = self.repository.activate(snapshot_id)
            self._append_event(
                activated.revision,
                event_type=(
                    "snapshot_rollback"
                    if previous and previous.revision > activated.revision
                    else "snapshot_activated"
                ),
                actor_id=actor_id,
                target_type="snapshot",
                target_id=snapshot_id,
                target_fingerprint=activated.content_hash,
                decision="rollback"
                if previous and previous.revision > activated.revision
                else "activated",
                content_hash=activated.content_hash,
                source_scene_snapshots=activated.source_scene_snapshots,
                metadata={"previous_snapshot_id": previous.snapshot_id}
                if previous
                else {},
            )
            return activated
        except OntologyRepositoryError as exc:
            if previous_snapshot_id:
                try:
                    self.neo4j_store.activate_snapshot(previous_snapshot_id)
                except Neo4jOntologyError:
                    pass
            else:
                try:
                    self.neo4j_store.deactivate_snapshot(snapshot_id)
                except Neo4jOntologyError:
                    pass
            raise OntologyServiceError(str(exc), str(exc)) from exc
        except Neo4jOntologyError as exc:
            raise OntologyServiceError(
                "ONTOLOGY_NEO4J_ACTIVATE_FAILED", str(exc)
            ) from exc

    def neo4j_health(self) -> dict[str, Any]:
        try:
            return self.neo4j_store.health()
        except Neo4jOntologyError as exc:
            raise OntologyServiceError("ONTOLOGY_NEO4J_UNAVAILABLE", str(exc)) from exc

    def neo4j_snapshot_status(self, snapshot_id: str) -> dict[str, Any]:
        try:
            return self.neo4j_store.snapshot_status(snapshot_id)
        except Neo4jOntologyError as exc:
            raise OntologyServiceError("ONTOLOGY_NEO4J_UNAVAILABLE", str(exc)) from exc

    def sync_snapshot_to_neo4j(
        self, snapshot_id: str, *, actor_id: str = "system"
    ) -> dict[str, Any]:
        try:
            snapshot = self.repository.get_snapshot(snapshot_id)
            result = self.neo4j_store.sync_snapshot(snapshot)
            self._append_event(
                snapshot.revision,
                event_type="snapshot_synced",
                actor_id=actor_id,
                target_type="snapshot",
                target_id=snapshot_id,
                target_fingerprint=snapshot.content_hash,
                decision="synced",
                content_hash=snapshot.content_hash,
                source_scene_snapshots=snapshot.source_scene_snapshots,
                metadata={
                    "node_count": result.get("node_count"),
                    "relationship_count": result.get("relationship_count"),
                },
            )
            return result
        except OntologyRepositoryError as exc:
            raise OntologyServiceError(str(exc), str(exc)) from exc
        except Neo4jOntologyError as exc:
            raise OntologyServiceError("ONTOLOGY_NEO4J_SYNC_FAILED", str(exc)) from exc

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
        merged_graph = self._without_excluded_objects(current.revision, merged_graph)
        review_state = self._review_state_for_graph(current.revision, merged_graph)
        rendered_graph = self._graph_with_review_status(
            merged_graph, review_state["items"]
        )
        return {
            "revision": current.revision,
            "valid": not issues,
            "graph": rendered_graph.model_dump(mode="json"),
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
    ) -> list[Any]:
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
            elif (snapshot := scene_snapshot(source)) is not None:
                keys.update(semantic_keys(snapshot))
            if semantic_key not in keys:
                issues.append(
                    OntologyValidationIssue(
                        code="UNKNOWN_SCENE_SEMANTIC_KEY",
                        message=f"场景 {edge.source} 不存在语义键：{semantic_key}。",
                        path=edge.id,
                    )
                )
        return issues

    @classmethod
    def _validate_metric_sources(
        cls, graph, markdown: str, scene_snapshots: list[Any]
    ) -> list[dict[str, Any]]:
        from .schemas import OntologyValidationIssue

        valid_ids = cls._valid_metric_ids(graph, markdown, scene_snapshots)
        return [
            OntologyValidationIssue(
                code="ONTOLOGY_METRIC_SOURCE_INVALID",
                message=(
                    f"指标 {node.id} 没有在来源场景语义文档的 metrics 声明中找到明确来源，"
                    "不能进入本体。"
                ),
                path=node.id,
            )
            for node in graph.nodes
            if node.type == "metric" and node.id not in valid_ids
        ]

    @classmethod
    def _valid_metric_ids(
        cls, graph, markdown: str, scene_snapshots: list[Any]
    ) -> set[str]:
        if not scene_snapshots:
            return {
                str(node.id) for node in graph.nodes if node.type == "metric"
            }
        declared = _declared_scene_metric_tokens(scene_snapshots)
        source_refs = _metric_source_refs(markdown)
        valid: set[str] = set()
        for node in graph.nodes:
            if node.type != "metric":
                continue
            references = source_refs.get(str(node.id), [])
            if not references:
                data = node.data or {}
                scene_values = data.get("source_scene") or data.get("source_scenes")
                if isinstance(scene_values, str):
                    scene_values = _split_source_values(scene_values)
                elif not isinstance(scene_values, (list, tuple, set)):
                    scene_values = []
                tokens = {
                    str(value).strip()
                    for value in (
                        node.name,
                        data.get("semantic_key"),
                        data.get("result_key"),
                        data.get("source_metric_key"),
                    )
                    if str(value or "").strip()
                }
                references = [
                    (str(scene_id), tokens) for scene_id in scene_values if scene_id
                ]
            for scene_id, tokens in references:
                if scene_id in declared and tokens & declared[scene_id]:
                    valid.add(str(node.id))
                    break
        return valid

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


def _declared_scene_metric_tokens(scene_snapshots: list[Any]) -> dict[str, set[str]]:
    declared: dict[str, set[str]] = {}
    parser = SemanticMarkdownParser()
    for source in scene_snapshots:
        snapshot = scene_snapshot(source)
        if snapshot is None:
            continue
        scene_id = str(getattr(snapshot, "scene_id", "") or "")
        if not scene_id:
            continue
        semantic_md = source.get("semantic_md") if isinstance(source, dict) else None
        if not semantic_md:
            runtime = getattr(snapshot, "runtime_config", {}) or {}
            documents = runtime.get("documents", {}) if isinstance(runtime, dict) else {}
            semantic_md = documents.get("semantic_md") if isinstance(documents, dict) else None
        if not isinstance(semantic_md, str) or not semantic_md.strip():
            continue
        try:
            config = parser.parse(semantic_md).config
        except Exception:
            continue
        tokens = declared.setdefault(scene_id, set())
        # A declared metric can have a scene binding alias (for example
        # ``project.count``) generated from its entity and key.  Keep those
        # aliases as source tokens, but only when the semantic document has
        # an explicit ``metrics`` declaration.  Dictionary fields alone must
        # never make a metric eligible.
        for metric in config.metrics:
            for value in (metric.key, metric.name):
                if value:
                    tokens.add(str(value).strip())
        if config.metrics:
            from .generator import scene_semantic_keys_from_source

            tokens.update(scene_semantic_keys_from_source(source))
    return declared


def _metric_source_refs(markdown: str) -> dict[str, list[tuple[str, set[str]]]]:
    refs: dict[str, list[tuple[str, set[str]]]] = {}
    for section in ("指标来源追溯", "场景绑定"):
        for row in _markdown_table_records(markdown, section):
            global_id = row.get("全局指标") or row.get("ontology对象") or row.get("对象")
            scene_id = row.get("场景") or row.get("来源场景")
            if not global_id or not scene_id:
                continue
            tokens = {
                str(value).strip()
                for value in (
                    row.get("场景级指标"),
                    row.get("语义键"),
                    row.get("场景语义键"),
                )
                if str(value or "").strip()
            }
            refs.setdefault(str(global_id), []).append((str(scene_id), tokens))
    return refs


def _markdown_table_records(markdown: str, section: str) -> list[dict[str, str]]:
    match = re.search(
        rf"(?ms)^##(?:#)?\s+{re.escape(section)}\s*\n(.*?)(?=^##(?:#)?\s+|\Z)",
        markdown,
    )
    if not match:
        return []
    rows = [line.strip() for line in match.group(1).splitlines() if line.strip().startswith("|")]
    if len(rows) < 2:
        return []
    headers = [_normalize_markdown_cell(value) for value in _split_markdown_row(rows[0])]
    result: list[dict[str, str]] = []
    for line in rows[2:]:
        cells = _split_markdown_row(line)
        if len(cells) != len(headers):
            continue
        result.append(dict(zip(headers, cells)))
    return result


def _split_markdown_row(line: str) -> list[str]:
    return [item.strip().replace("\\|", "|") for item in line.strip().strip("|").split("|")]


def _normalize_markdown_cell(value: str) -> str:
    return re.sub(r"[\s_（）()：:]", "", value).lower()


def _split_source_values(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，、;；]", value) if item.strip()]


def _metric_reference_ids(markdown: str) -> set[str]:
    references: set[str] = set()
    for section in ("指标关系", "场景绑定", "指标来源追溯"):
        for row in _markdown_table_records(markdown, section):
            values = (
                row.get("主指标"),
                row.get("关联指标"),
                row.get("ontology对象"),
                row.get("对象"),
                row.get("全局指标"),
            )
            references.update(
                str(value).strip()
                for value in values
                if _looks_like_metric_id(value)
            )
    for row in _markdown_table_records(markdown, "分析维度"):
        references.update(
            value
            for value in _split_source_values(row.get("适用指标") or "")
            if _looks_like_metric_id(value)
        )
    return references


def _looks_like_metric_id(value: Any) -> bool:
    return bool(re.match(r"^(?:m_|met_|metric_)", str(value or "").strip()))


def _remove_invalid_metric_references(markdown: str, invalid_ids: set[str]) -> str:
    if not invalid_ids:
        return markdown
    table_filters: dict[str, Any] = {
        "指标": lambda row: (row.get("id") or "") not in invalid_ids,
        "指标来源追溯": lambda row: (row.get("全局指标") or "") not in invalid_ids,
        "场景绑定": lambda row: (row.get("ontology对象") or row.get("对象") or "") not in invalid_ids,
        "指标关系": lambda row: (
            row.get("主指标") or ""
        ) not in invalid_ids
        and (row.get("关联指标") or "") not in invalid_ids,
        "分析维度": lambda row: True,
    }
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    section = ""
    headers: list[str] | None = None
    filtering = False
    for line in lines:
        heading = re.match(r"^###{0,1}\s+(.+?)\s*$", line)
        if heading:
            section = heading.group(1).strip()
            headers = None
            filtering = section in table_filters
            output.append(line)
            continue
        if filtering and line.strip().startswith("|"):
            cells = _split_markdown_row(line)
            if headers is None:
                headers = [_normalize_markdown_cell(value) for value in cells]
                output.append(line)
                continue
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                output.append(line)
                continue
            if len(cells) == len(headers):
                row = dict(zip(headers, cells))
                if not table_filters[section](row):
                    continue
        if section == "分析维度" and line.strip().startswith("|"):
            cells = _split_markdown_row(line)
            if headers is not None and len(cells) == len(headers):
                dimension_index = headers.index("适用指标") if "适用指标" in headers else -1
                if dimension_index >= 0:
                    cells[dimension_index] = "、".join(
                        value
                        for value in _split_source_values(cells[dimension_index])
                        if value not in invalid_ids
                    )
                    line = "| " + " | ".join(cells) + " |"
        if invalid_ids and line.strip() and not line.strip().startswith("|"):
            for metric_id in invalid_ids:
                line = re.sub(rf"(?<![\w.-]){re.escape(metric_id)}(?![\w.-])", "", line)
        output.append(line)
    return "\n".join(output)


def _remove_review_object_references(
    markdown: str,
    *,
    target_type: str,
    target_id: str,
    object_type: str,
    object_value: dict[str, Any],
) -> str:
    """Remove one review object and the Markdown references it owns."""
    if target_type == "node" and object_type == "metric":
        return _remove_invalid_metric_references(markdown, {target_id})
    if target_type == "node" and object_type == "analysis_path":
        return _remove_markdown_table_rows(
            markdown,
            "跨场景分析路径",
            lambda row: (row.get("id") or "") == target_id,
        )
    if target_type == "edge" and object_type == "cross_scene_join":
        return _remove_markdown_table_rows(
            markdown,
            "跨场景关联",
            lambda row: (row.get("id") or "") == target_id,
        )
    if target_type == "edge" and object_type == "scene_binding":
        source = str(object_value.get("source") or "")
        target = str(object_value.get("target") or "")
        return _remove_markdown_table_rows(
            markdown,
            "场景绑定",
            lambda row: (
                (row.get("场景") or "") == source
                and (row.get("ontology对象") or row.get("对象") or "") == target
            ),
        )
    # These object types are currently informational and are not required
    # review targets. Keep the document untouched if an older client sends
    # an unsupported exclusion request.
    return markdown


def _remove_markdown_table_rows(
    markdown: str,
    section: str,
    predicate: Any,
) -> str:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    current_section = ""
    headers: list[str] | None = None
    for line in lines:
        heading = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
        if heading:
            current_section = heading.group(1).strip()
            headers = None
            output.append(line)
            continue
        if current_section == section and line.strip().startswith("|"):
            cells = _split_markdown_row(line)
            if headers is None:
                headers = [_normalize_markdown_cell(value) for value in cells]
                output.append(line)
                continue
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                output.append(line)
                continue
            if len(cells) == len(headers) and predicate(dict(zip(headers, cells))):
                continue
        output.append(line)
    return "\n".join(output)


def _excluded_review_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return exclusions after the most recent full draft regeneration."""
    last_generation = -1
    for index, event in enumerate(events):
        if event.get("event_type") == "draft_generated":
            last_generation = index
    excluded: list[dict[str, Any]] = []
    for event in events[last_generation + 1 :]:
        if event.get("event_type") != "object_excluded":
            continue
        target_type = str(event.get("target_type") or "")
        target_id = str(event.get("target_id") or "")
        if target_type and target_id:
            excluded.append(event)
    return excluded


def _matches_excluded_generated_metric(
    node: Any, excluded_events: list[dict[str, Any]]
) -> bool:
    """Hide a generated metric equivalent to an excluded manual metric."""
    if getattr(node, "type", None) != "metric":
        return False
    node_data = node.data or {}
    node_scene = str(node_data.get("source_scene") or "")
    node_key = str(
        node_data.get("semantic_key") or node_data.get("result_key") or ""
    )
    for event in excluded_events:
        if event.get("target_type") != "node":
            continue
        metadata = event.get("metadata") or {}
        if metadata.get("object_type") != "metric":
            continue
        excluded_object = metadata.get("object") or {}
        excluded_data = excluded_object.get("data") or {}
        excluded_scene = str(excluded_data.get("source_scene") or "")
        excluded_key = str(
            excluded_data.get("semantic_key")
            or excluded_data.get("result_key")
            or ""
        )
        if not node_scene or not excluded_scene or node_scene != excluded_scene:
            continue
        if node_key and excluded_key and node_key != excluded_key:
            continue
        if str(getattr(node, "name", "")) == str(excluded_object.get("name") or ""):
            return True
    return False


def _matches_excluded_generated_edge(
    edge: Any, excluded_events: list[dict[str, Any]]
) -> bool:
    """Hide generated bindings that represent an excluded manual binding."""
    if getattr(edge, "type", None) != "scene_binding":
        return False
    for event in excluded_events:
        if event.get("target_type") != "edge":
            continue
        metadata = event.get("metadata") or {}
        if metadata.get("object_type") != "scene_binding":
            continue
        excluded_edge = metadata.get("object") or {}
        excluded_data = excluded_edge.get("data") or {}
        edge_data = getattr(edge, "data", {}) or {}
        if (
            str(edge.source) == str(excluded_edge.get("source") or "")
            and str(edge.target) == str(excluded_edge.get("target") or "")
        ):
            return True
        if (
            str(edge.source) == str(excluded_edge.get("source") or "")
            and str(edge_data.get("semantic_key") or "")
            == str(excluded_data.get("semantic_key") or "")
            and str(edge_data.get("semantic_key") or "")
        ):
            return True
    return False


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
            available_models = {item.model for item in models}
            # Keep Ontology generation aligned with the application's default LLM.
            # The explicit argument remains useful for tests and integrations.
            configured_model = None
            if model_name is None:
                try:
                    import json
                    from pathlib import Path

                    configured_model = json.loads(
                        Path("pilot/meta_data/default_model.json").read_text(
                            encoding="utf-8"
                        )
                    ).get("model_name")
                except Exception:
                    configured_model = None
                try:
                    from dbgpt_app.config import ApplicationConfig

                    app_config = cfg.parse_config(
                        ApplicationConfig, hook_section="hooks"
                    )
                    configured_model = configured_model or app_config.models.default_llm
                except Exception:
                    configured_model = None
            if model_name:
                candidate_models = [model_name]
            else:
                candidate_models = []
                if configured_model and configured_model in available_models:
                    candidate_models.append(configured_model)
                candidate_models.extend(
                    item.model for item in models if item.model not in candidate_models
                )
            if not candidate_models:
                raise RuntimeError("No models available for Ontology generator")
            last_error = None
            for selected_model in candidate_models:
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
                try:
                    response = await llm_client.generate(request)
                except Exception as exc:
                    last_error = exc
                    continue
                if response.success and response.has_text:
                    return response.text
                last_error = RuntimeError(
                    f"Ontology generator model {selected_model} generation failed"
                )
            raise last_error or RuntimeError(
                "Ontology generator model generation failed"
            )
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
