"""Deterministic construction of immutable ask-data Snapshots."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..scene.markdown import SemanticMarkdownParser, split_scene_documents
from ..scene.validator import SceneConfigValidator
from ..schemas.schema import ViewSchema
from ..schemas.semantic import ParsedSemanticDocument
from ..schemas.snapshot import Snapshot, SnapshotSourceHashes, SnapshotStatus


class SnapshotBuildError(ValueError):
    """Raised when a revision cannot become a Snapshot."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class SnapshotBuilder:
    """Compile M1's validated facts into routing and runtime projections."""

    snapshot_schema_version = "2"
    query_spec_version = "1"
    compiler_version = "1"

    def __init__(
        self,
        parser: SemanticMarkdownParser | None = None,
        validator: SceneConfigValidator | None = None,
    ):
        self.parser = parser or SemanticMarkdownParser()
        self.validator = validator or SceneConfigValidator()

    def build(
        self,
        *,
        scene_id: str,
        revision_id: str,
        markdown: str,
        view_schema: ViewSchema,
        knowledge_hash: str = "sha256:none",
        knowledge_space: str | None = None,
        run_rag_quality_gate: bool = True,
        rag_quality_ok: bool = True,
    ) -> Snapshot:
        parsed = self._parse(markdown)
        if parsed.config.scene_id != scene_id:
            raise SnapshotBuildError(
                "SCENE_ID_MISMATCH", "Semantic scene ID does not match revision"
            )
        result = self.validator.validate(
            parsed.config,
            view_schema,
            expected_scene_id=scene_id,
            expected_data_source=view_schema.data_source,
            expected_view=view_schema.view,
        )
        if not result.valid:
            issue = result.errors[0]
            raise SnapshotBuildError(issue.code, issue.message)
        del run_rag_quality_gate, rag_quality_ok

        source_hashes = SnapshotSourceHashes(
            semantic_hash=parsed.semantic_hash,
            schema_hash=view_schema.schema_hash,
            knowledge_hash=knowledge_hash,
        )
        routing_projection = self._routing_projection(parsed)
        runtime_config = self._runtime_config(
            parsed,
            view_schema,
            source_hashes,
            revision_id,
            knowledge_space,
            markdown,
        )
        content = {
            "scene_id": scene_id,
            "revision_id": revision_id,
            "source_hashes": source_hashes.model_dump(mode="json"),
            "routing_projection": routing_projection,
            "runtime_config": runtime_config,
            "schema_version": self.snapshot_schema_version,
            "query_spec_version": self.query_spec_version,
            "compiler_version": self.compiler_version,
        }
        content_hash = self._hash(content)
        return Snapshot(
            snapshot_id=f"snap_{content_hash[7:31]}",
            scene_id=scene_id,
            revision_id=revision_id,
            status=SnapshotStatus.READY,
            content_hash=content_hash,
            source_hashes=source_hashes,
            routing_projection=routing_projection,
            runtime_config=runtime_config,
            schema_version=self.snapshot_schema_version,
            query_spec_version=self.query_spec_version,
            compiler_version=self.compiler_version,
        )

    def _parse(self, markdown: str) -> ParsedSemanticDocument:
        try:
            return self.parser.parse(markdown)
        except ValueError as exc:
            code = getattr(exc, "code", "INVALID_SEMANTIC_CONFIG")
            raise SnapshotBuildError(code, str(exc)) from exc

    @staticmethod
    def _routing_projection(parsed: ParsedSemanticDocument) -> dict[str, Any]:
        config = parsed.config
        return {
            "scene_id": config.scene_id,
            "name": config.name,
            "description": config.description,
            "keywords": list(config.keywords),
            "query_api": f"/api/v1/ask-data/scenes/{config.scene_id}/query",
        }

    def _runtime_config(
        self,
        parsed: ParsedSemanticDocument,
        view_schema: ViewSchema,
        source_hashes: SnapshotSourceHashes,
        revision_id: str,
        knowledge_space: str | None,
        semantic_md: str,
    ) -> dict[str, Any]:
        config = parsed.config
        documents = split_scene_documents(parsed.body_markdown)
        documents.update(
            {
                "semantic_md": semantic_md,
                "semantic_md_hash": source_hashes.semantic_hash,
            }
        )
        return {
            "revision_id": revision_id,
            "data_source": config.data_source,
            "view": config.view,
            "schema": view_schema.model_dump(mode="json", exclude={"inspected_at"}),
            "named_filters": [
                item.model_dump(mode="json") for item in config.named_filters
            ],
            "value_mapping": config.value_mapping,
            "documents": documents,
            "query_limits": config.query_limits.model_dump(
                mode="json", exclude={"allow_detail"}
            ),
            "rag": {
                **config.rag.model_dump(mode="json"),
                "knowledge_space": knowledge_space,
                "knowledge_hash": source_hashes.knowledge_hash,
            },
            "schema_version": self.snapshot_schema_version,
            "query_spec_version": self.query_spec_version,
            "compiler_version": self.compiler_version,
        }

    @staticmethod
    def _hash(value: Any) -> str:
        serialized = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"


__all__ = ["SnapshotBuildError", "SnapshotBuilder"]
