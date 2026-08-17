"""Deterministic final-answer rendering from a ResultBundle only."""

from __future__ import annotations

from ..schemas.result import ResultBundle


class FinalAnswerBuilder:
    """Render a bounded answer without recalculating business metrics."""

    def build(self, bundle: ResultBundle) -> str:
        if bundle.answer:
            return bundle.answer
        if bundle.status not in {"succeeded", "partial_succeeded"}:
            return "查询未完成，请根据返回的错误或澄清信息继续操作。"
        row_count = sum(item.row_count for item in bundle.scene_summaries)
        scene_count = len(bundle.scene_summaries)
        warning = "结果包含警告，请查看 warnings。" if bundle.warnings else ""
        return f"查询完成，返回 {scene_count} 个场景、{row_count} 行结果。{warning}"


__all__ = ["FinalAnswerBuilder"]
