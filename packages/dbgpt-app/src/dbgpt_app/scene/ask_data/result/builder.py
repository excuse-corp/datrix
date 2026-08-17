"""Build ResultBundle values from independent SceneResults."""

from __future__ import annotations

from typing import Any

from ..combine import ResultCombiner
from ..ontology.context import relevant_ontology_projection
from ..ontology.schemas import OntologySnapshot
from ..query.query_spec import SceneResult
from ..schemas.result import ResultBundle, SceneResultSummary
from ..schemas.snapshot import Snapshot
from ..visualization import ChartSpecBuilder


class ResultBundleBuilder:
    def __init__(
        self,
        combiner: ResultCombiner | None = None,
        chart_builder: ChartSpecBuilder | None = None,
        max_rows: int = 5000,
        max_bytes: int = 8 * 1024 * 1024,
    ):
        self.combiner = combiner or ResultCombiner()
        self.chart_builder = chart_builder or ChartSpecBuilder()
        self.max_rows = max_rows
        self.max_bytes = max_bytes

    def build(
        self,
        *,
        results: list[SceneResult],
        snapshots: dict[str, Snapshot],
        mode: str = "separate",
        keys: list[str] | None = None,
        derived_metrics: list[str] | None = None,
        status: str = "succeeded",
        ontology_snapshot: OntologySnapshot | None = None,
        question: str | None = None,
    ) -> ResultBundle:
        warnings: list[dict[str, str]] = []
        for result in results:
            warnings.extend(
                {"code": "SCENE_RESULT_WARNING", "message": warning}
                for warning in result.warnings
            )
            if result.truncated:
                warnings.append(
                    {
                        "code": "TRUNCATED_RESULT",
                        "message": f"Result {result.task_id} was truncated",
                    }
                )
            if result.rag.get("degraded"):
                warnings.append(
                    {
                        "code": "RAG_DEGRADED",
                        "message": f"RAG retrieval degraded for {result.scene_id}",
                    }
                )
        combined = None
        charts = []
        if results:
            combined = self.combiner.combine(
                results=results,
                snapshots=snapshots,
                mode=mode,
                keys=keys,
                derived_metrics=derived_metrics,
            )
            warnings.extend(combined.warnings)
            charts = self.chart_builder.build(combined)
        references = [
            reference
            for result in results
            for reference in result.rag.get("references", [])
            if isinstance(reference, str)
        ]
        ontology_context = self._ontology_context(ontology_snapshot, question, results)
        answer = self._answer(status, results, warnings, ontology_context)
        return ResultBundle(
            status=status,
            answer=answer,
            results=[result.model_dump(mode="json") for result in results],
            scene_summaries=[
                SceneResultSummary(
                    task_id=result.task_id,
                    scene_id=result.scene_id,
                    status=result.status,
                    snapshot_id=result.snapshot_id,
                    row_count=result.row_count,
                    truncated=result.truncated,
                    warnings=result.warnings,
                )
                for result in results
            ],
            combined=combined,
            charts=charts,
            warnings=warnings,
            rag_references=references,
            ontology_context=ontology_context,
        )

    @staticmethod
    def _ontology_context(
        ontology_snapshot: OntologySnapshot | None,
        question: str | None,
        results: list[SceneResult],
    ) -> dict[str, Any] | None:
        if ontology_snapshot is None:
            return None
        scene_ids = sorted({result.scene_id for result in results if result.scene_id})
        result_columns = [
            column.model_dump(mode="json")
            for result in results
            for column in result.columns
        ]
        return relevant_ontology_projection(
            ontology_snapshot,
            question,
            max_nodes=16,
            scene_ids=scene_ids,
            result_columns=result_columns,
        )

    @staticmethod
    def _answer(
        status: str,
        results: list[SceneResult],
        warnings: list[dict[str, str]],
        ontology_context: dict[str, Any] | None = None,
    ) -> str:
        if status != "succeeded":
            return "查询未完成，请根据返回的错误或澄清信息继续操作。"
        row_count = sum(result.row_count for result in results)
        scene_count = len({result.scene_id for result in results})
        suffixes = []
        if ontology_context:
            suffixes.append(
                f"已结合 Ontology v{ontology_context.get('revision')} 的业务语义。"
            )
        if warnings:
            suffixes.append("结果包含警告，请查看 warnings。")
        suffix = "".join(suffixes)
        return f"查询完成，返回 {scene_count} 个场景、{row_count} 行结果。{suffix}"


__all__ = ["ResultBundleBuilder"]
