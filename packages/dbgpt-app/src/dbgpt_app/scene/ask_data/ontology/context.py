"""Bounded global-Ontology context for the main Agent only."""

from __future__ import annotations

import re
from typing import Any

from .schemas import OntologySnapshot


def relevant_ontology_projection(
    snapshot: OntologySnapshot, question: str | None = None, *, max_nodes: int = 24
) -> dict[str, Any]:
    """Select a small connected subgraph; never expose scene Markdown or SQL."""
    graph = snapshot.graph_json
    nodes = list(graph.get("nodes", []))
    edges = list(graph.get("edges", []))
    question_text = (question or "").strip().lower()
    terms = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", question_text))

    def score(node: dict[str, Any]) -> int:
        if not terms:
            return 0
        haystack = " ".join(
            [
                str(node.get("id", "")),
                str(node.get("name", "")),
                " ".join(str(item) for item in node.get("aliases", [])),
                str(node.get("description", "")),
            ]
        ).lower()
        return sum(1 for term in terms if term in haystack)

    matched = [node for node in nodes if score(node) > 0]
    if not matched:
        matched = [
            node for node in nodes if node.get("type") in {"entity", "metric", "scene"}
        ]
    selected_ids = {str(node.get("id")) for node in matched[:max_nodes]}
    selected_edges = [
        edge
        for edge in edges
        if edge.get("source") in selected_ids or edge.get("target") in selected_ids
    ]
    for edge in selected_edges:
        if len(selected_ids) >= max_nodes:
            break
        selected_ids.add(str(edge.get("source")))
        selected_ids.add(str(edge.get("target")))
    selected_nodes = [node for node in nodes if node.get("id") in selected_ids]
    return {
        "ontology_snapshot_id": snapshot.snapshot_id,
        "revision": snapshot.revision,
        "nodes": selected_nodes[:max_nodes],
        "edges": [
            edge
            for edge in edges
            if edge.get("source") in selected_ids and edge.get("target") in selected_ids
        ][: max_nodes * 2],
    }


def render_main_agent_ontology_context(
    snapshot: OntologySnapshot | None, question: str | None = None
) -> str:
    if snapshot is None:
        return ""
    projection = relevant_ontology_projection(snapshot, question)
    lines = [f"全局业务 Ontology（版本 {projection['revision']}）："]
    for node in projection["nodes"]:
        if node.get("type") == "rule":
            lines.append(f"- 澄清规则：{node.get('description')}")
            continue
        if node.get("type") not in {"entity", "metric", "scene"}:
            continue
        aliases = "、".join(node.get("aliases", []))
        details = node.get("data", {}) or {}
        suffix = f"；别名：{aliases}" if aliases else ""
        if node.get("type") == "metric" and details.get("source_scene"):
            suffix += f"；由场景 {details['source_scene']} 提供"
        lines.append(f"- {node.get('name')}（{node.get('id')}）{suffix}")
    for edge in projection["edges"]:
        if edge.get("type") in {"relation", "cross_scene_join"}:
            relation = f"{edge.get('source')} —{edge.get('name')}→ {edge.get('target')}"
            lines.append(f"- 关系：{relation}")
    return "\n".join(lines)


__all__ = ["relevant_ontology_projection", "render_main_agent_ontology_context"]
