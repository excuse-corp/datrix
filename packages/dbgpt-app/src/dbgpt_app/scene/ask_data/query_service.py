"""Shared single-scene query service for M3 and the Scene API."""

from __future__ import annotations

import hashlib
from time import monotonic
from typing import Any

from .query.compiler import CompiledQuery, QuerySpecCompiler
from .query.executor import SafeSceneQueryExecutor
from .query.query_spec import QuerySpec, ResultColumn, SceneResult
from .rag.manager import SceneKnowledgeManager
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
            engine,
            compiled,
            snapshot,
            enforce_row_limit=False,
        )
        columns = [
            ResultColumn(
                key=key,
                type="metric"
                if key
                in {item["key"] for item in snapshot.runtime_config.get("metrics", [])}
                else "dimension",
                unit=next(
                    (
                        item.get("unit")
                        for item in snapshot.runtime_config.get("metrics", [])
                        if item["key"] == key
                    ),
                    None,
                ),
            )
            for key in keys
        ]
        return SceneResult(
            task_id=task_id,
            scene_id=snapshot.scene_id,
            scene_revision=snapshot.revision_id,
            snapshot_id=snapshot.snapshot_id,
            status="succeeded",
            grain=list(spec.dimensions),
            time_grain=spec.time_grain,
            time_range=spec.time_range,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            rag=rag_result,
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
            engine, compiled, snapshot
        )
        columns = [
            ResultColumn(
                key=key,
                type=self._result_column_type(snapshot, key),
            )
            for key in keys
        ]
        return SceneResult(
            task_id=task_id,
            scene_id=snapshot.scene_id,
            scene_revision=snapshot.revision_id,
            snapshot_id=snapshot.snapshot_id,
            status="succeeded",
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            rag=rag_result,
            query_spec_hash=compiled.query_spec_hash,
            compiler_version=compiled.compiler_version,
            sql_hash=sql_hash,
            duration_ms=max(query_duration, int((monotonic() - started) * 1000)),
        )

    @staticmethod
    def _result_column_type(snapshot: Snapshot, key: str) -> str:
        metric_keys = {item["key"] for item in snapshot.runtime_config.get("metrics", [])}
        if key in metric_keys:
            return "metric"
        schema = snapshot.runtime_config.get("schema", {})
        columns = schema.get("columns", []) if isinstance(schema, dict) else []
        for column in columns:
            if not isinstance(column, dict) or column.get("name") != key:
                continue
            if column.get("normalized_type") == "number":
                return "metric"
            return "dimension"
        return "dimension"

    def _verify_knowledge(self, snapshot: Snapshot) -> None:
        if self.knowledge_manager is None:
            return
        rag = snapshot.runtime_config.get("rag", {})
        space_name = rag.get("knowledge_space")
        knowledge_hash = rag.get("knowledge_hash")
        if space_name and knowledge_hash and not self.knowledge_manager.verify(
            space_name, knowledge_hash
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
