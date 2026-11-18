"""Shared post-query Ontology retrieval for result bundles and Agent context."""

from __future__ import annotations

from typing import Any

from .context import relevant_ontology_projection, render_main_agent_ontology_context
from .schemas import OntologySnapshot


class OntologyAnalysisService:
    """Build one bounded, result-driven Ontology context consistently."""

    def build(
        self,
        snapshot: OntologySnapshot | None,
        question: str | None,
        *,
        scene_ids: list[str] | None = None,
        authorized_scene_ids: list[str] | None = None,
        is_platform_admin: bool = True,
        result_columns: list[dict[str, Any]] | None = None,
        result_lineage: list[dict[str, Any]] | None = None,
        result_profiles: list[dict[str, Any]] | None = None,
        max_nodes: int = 16,
        retrieval_source: str | None = None,
    ) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        return relevant_ontology_projection(
            snapshot,
            question,
            max_nodes=max_nodes,
            scene_ids=scene_ids,
            authorized_scene_ids=authorized_scene_ids,
            is_platform_admin=is_platform_admin,
            result_columns=result_columns,
            result_lineage=result_lineage,
            result_profiles=result_profiles,
            retrieval_source=retrieval_source,
        )

    def render(
        self,
        snapshot: OntologySnapshot | None,
        question: str | None,
        *,
        scene_ids: list[str] | None = None,
        authorized_scene_ids: list[str] | None = None,
        is_platform_admin: bool = True,
        result_columns: list[dict[str, Any]] | None = None,
        result_lineage: list[dict[str, Any]] | None = None,
        result_profiles: list[dict[str, Any]] | None = None,
        max_nodes: int = 16,
        retrieval_source: str | None = None,
    ) -> str:
        return render_main_agent_ontology_context(
            snapshot,
            question,
            scene_ids=scene_ids,
            authorized_scene_ids=authorized_scene_ids,
            is_platform_admin=is_platform_admin,
            result_columns=result_columns,
            result_lineage=result_lineage,
            result_profiles=result_profiles,
            max_nodes=max_nodes,
            retrieval_source=retrieval_source,
        )


__all__ = ["OntologyAnalysisService"]
