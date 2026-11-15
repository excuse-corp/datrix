"""Shared single-scene query service for M3 and the Scene API."""

from __future__ import annotations

import hashlib
from time import monotonic
from typing import Any

from .query.capabilities import (
    build_query_capabilities,
    metric_keys,
)
from .query.compiler import CompiledQuery, QuerySpecCompiler
from .query.executor import SafeSceneQueryExecutor
from .query.query_spec import QuerySpec, ResultColumn, SceneResult
from .rag.manager import SceneKnowledgeManager
from .result.sql_explanation import build_sql_explanation
from .schemas.snapshot import Snapshot, SnapshotStatus


class SceneQueryService:
    def __init__(
        self,
        compiler: QuerySpecCompiler | None = None,
        executor: SafeSceneQueryExecutor | None = None,
        knowledge_manager: SceneKnowledgeManager | None = None,
    ):
        self.compiler = compiler or QuerySpecCompiler()
        self.executor = executor or SafeSceneQueryExecutor()
        self.knowledge_manager = knowledge_manager

    def execute(
        self,
        *,
        task_id: str,
        spec: QuerySpec,
        snapshot: Snapshot,
        engine: Any,
        rag: dict[str, Any] | None = None,
    ) -> SceneResult:
        if snapshot.status != SnapshotStatus.ACTIVE:
            raise ValueError("SCENE_NOT_ACTIVE")
        rag_result = self._resolve_rag(snapshot, rag or {})
        started = monotonic()
        compiled = self.compiler.compile(spec, snapshot)
        keys, rows, truncated, query_duration = self.executor.execute(
            engine, compiled, snapshot
        )
        columns = [self._result_column(snapshot, key) for key in keys]
        capabilities = build_query_capabilities(snapshot)
        return SceneResult(
            task_id=task_id,
            scene_id=snapshot.scene_id,
            scene_revision=snapshot.revision_id,
            snapshot_id=snapshot.snapshot_id,
            status="succeeded",
            grain=list(spec.dimensions),
            time_grain=spec.time_grain,
            time_basis=(
                str(capabilities.time.get("field"))
                if capabilities.time.get("field")
                else None
            ),
            time_range=spec.time_range,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            rag=rag_result,
            sql=compiled.sql,
            sql_explanation=build_sql_explanation(compiled.sql, snapshot),
            query_spec_hash=compiled.query_spec_hash,
            compiler_version=compiled.compiler_version,
            sql_hash=compiled.sql_hash,
            duration_ms=max(query_duration, int((monotonic() - started) * 1000)),
        )

    def execute_sql(
        self,
        *,
        task_id: str,
        sql: str,
        snapshot: Snapshot,
        engine: Any,
        rag: dict[str, Any] | None = None,
    ) -> SceneResult:
        if snapshot.status != SnapshotStatus.ACTIVE:
            raise ValueError("SCENE_NOT_ACTIVE")
        rag_result = self._resolve_rag(snapshot, rag or {})
        started = monotonic()
        sql_hash = f"sha256:{hashlib.sha256(sql.encode('utf-8')).hexdigest()}"
        compiled = CompiledQuery(
            sql,
            {},
            [],
            "llm-sql-1",
            f"sha256:{hashlib.sha256((task_id + sql).encode('utf-8')).hexdigest()}",
        )
        keys, rows, truncated, query_duration = self.executor.execute(
            engine,
            compiled,
            snapshot,
            enforce_row_limit=False,
        )
        sql_explanation = build_sql_explanation(sql, snapshot)
        sql_metrics = {
            str(item.get("key")): item
            for item in sql_explanation.get("metrics", [])
            if isinstance(item, dict) and item.get("key")
        }
        columns = [
            self._result_column(snapshot, key, sql_metric=sql_metrics.get(key))
            for key in keys
        ]
        capabilities = build_query_capabilities(snapshot)
        return SceneResult(
            task_id=task_id,
            scene_id=snapshot.scene_id,
            scene_revision=snapshot.revision_id,
            snapshot_id=snapshot.snapshot_id,
            status="succeeded",
            time_basis=(
                str(capabilities.time.get("field"))
                if capabilities.time.get("field")
                else None
            ),
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            rag=rag_result,
            sql=sql,
            sql_explanation=sql_explanation,
            query_spec_hash=compiled.query_spec_hash,
            compiler_version=compiled.compiler_version,
            sql_hash=sql_hash,
            duration_ms=max(query_duration, int((monotonic() - started) * 1000)),
        )

    @staticmethod
    def _result_column(
        snapshot: Snapshot, key: str, *, sql_metric: dict[str, Any] | None = None
    ) -> ResultColumn:
        """Attach stable semantic metadata to an executed result column.

        The query compiler already knows which business capability produced a
        result key. Keeping that metadata on the result avoids asking the
        Ontology retriever to infer aggregation or meaning from SQL text.
        """
        capabilities = build_query_capabilities(snapshot)
        metric = next(
            (item for item in capabilities.metrics if str(item.get("key")) == key),
            None,
        )
        dimension = next(
            (item for item in capabilities.dimensions if str(item.get("key")) == key),
            None,
        )
        if metric is None and key.endswith("_label"):
            dimension_key = key[: -len("_label")]
            dimension = next(
                (
                    item
                    for item in capabilities.dimensions
                    if str(item.get("key")) == dimension_key
                ),
                None,
            )
        item = metric or dimension or {}
        is_metric = (
            metric is not None or key in metric_keys(snapshot) or sql_metric is not None
        )
        return ResultColumn(
            key=key,
            type="metric" if is_metric else "dimension",
            label_key=(
                str(item.get("label_field")) if item.get("label_field") else None
            ),
            label=str(item.get("name") or (sql_metric or {}).get("label") or key),
            semantic_key=str(item.get("semantic_key") or item.get("key") or key),
            source_field=(str(item.get("field")) if item.get("field") else None),
            aggregation=(
                str(item.get("aggregation") or (sql_metric or {}).get("aggregation"))
                if item.get("aggregation") or (sql_metric or {}).get("aggregation")
                else None
            ),
            unit=(
                str(item.get("unit") or (sql_metric or {}).get("unit"))
                if item.get("unit") or (sql_metric or {}).get("unit")
                else None
            ),
        )

    def _verify_knowledge(self, snapshot: Snapshot) -> None:
        if self.knowledge_manager is None:
            return
        rag = snapshot.runtime_config.get("rag", {})
        space_name = rag.get("knowledge_space")
        knowledge_hash = rag.get("knowledge_hash")
        if (
            space_name
            and knowledge_hash
            and not self.knowledge_manager.verify(space_name, knowledge_hash)
        ):
            raise ValueError("RAG_KNOWLEDGE_DRIFT")

    def _resolve_rag(
        self, snapshot: Snapshot, request: dict[str, Any]
    ) -> dict[str, Any]:
        result = {
            "degraded": False,
            "references": list(request.get("references", [])),
        }
        if self.knowledge_manager is None:
            return result
        self._verify_knowledge(snapshot)
        question = str(request.get("question", "")).strip()
        if not question:
            return result
        rag_config = snapshot.runtime_config.get("rag", {})
        space_name = rag_config.get("knowledge_space")
        if not space_name:
            return result
        try:
            result["references"].extend(
                self.knowledge_manager.retrieve(space_name, question)
            )
        except Exception as exc:
            if request.get("fixed_context_sufficient", True):
                result["degraded"] = True
                result["warning"] = "RAG_DEGRADED"
            else:
                raise ValueError("RAG_DEGRADED") from exc
        result["references"] = list(dict.fromkeys(result["references"]))
        return result


__all__ = ["SceneQueryService"]
