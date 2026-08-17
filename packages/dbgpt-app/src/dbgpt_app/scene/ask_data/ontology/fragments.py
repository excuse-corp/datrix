"""Derive safe, local Ontology fragments from active Scene Snapshots."""

from __future__ import annotations

from typing import Any

from .schemas import OntologyEdge, OntologyGraph, OntologyNode


def build_scene_fragment(snapshot: Any) -> OntologyGraph:
    """Expose Scene capabilities, never its Markdown, schema or physical fields."""
    projection = getattr(snapshot, "routing_projection", {}) or {}
    scene_id = str(getattr(snapshot, "scene_id", ""))
    snapshot_id = str(getattr(snapshot, "snapshot_id", ""))
    if not scene_id:
        return OntologyGraph()
    provenance = [{"scene_id": scene_id, "snapshot_id": snapshot_id}]
    nodes = [
        OntologyNode(
            id=scene_id,
            type="scene",
            name=str(projection.get("name") or scene_id),
            description=str(projection.get("description") or ""),
            aliases=[str(item) for item in projection.get("keywords", [])],
            provenance=provenance,
        )
    ]
    edges = []
    for metric in projection.get("metrics", []):
        if not isinstance(metric, dict) or not metric.get("key"):
            continue
        semantic_key = str(metric["key"])
        metric_id = f"{scene_id}__{semantic_key}"
        nodes.append(
            OntologyNode(
                id=metric_id,
                type="metric",
                name=str(metric.get("name") or semantic_key),
                aliases=[str(item) for item in metric.get("aliases", [])],
                data={"source_scene": scene_id, "semantic_key": semantic_key},
                provenance=provenance,
            )
        )
        edges.extend(
            [
                OntologyEdge(
                    id=f"provided_by_{metric_id}_{scene_id}",
                    source=metric_id,
                    target=scene_id,
                    type="provided_by",
                    name="由场景提供",
                    provenance=provenance,
                ),
                OntologyEdge(
                    id=f"binding_{scene_id}_{metric_id}",
                    source=scene_id,
                    target=metric_id,
                    type="scene_binding",
                    name="场景语义绑定",
                    data={"semantic_key": semantic_key},
                    provenance=provenance,
                ),
            ]
        )
    return OntologyGraph(nodes=nodes, edges=edges)


def merge_graphs(*graphs: OntologyGraph) -> OntologyGraph:
    """Manual Ontology wins on duplicate IDs; generated fragments fill gaps."""
    nodes: dict[str, OntologyNode] = {}
    edges: dict[str, OntologyEdge] = {}
    for graph in graphs:
        for node in graph.nodes:
            nodes.setdefault(node.id, node)
        for edge in graph.edges:
            edges.setdefault(edge.id, edge)
    return OntologyGraph(nodes=list(nodes.values()), edges=list(edges.values()))


def source_snapshot_refs(snapshots: list[Any]) -> list[dict[str, str]]:
    return sorted(
        [
            {
                "scene_id": str(snapshot.scene_id),
                "snapshot_id": str(snapshot.snapshot_id),
                "revision_id": str(snapshot.revision_id),
                "semantic_hash": str(snapshot.source_hashes.semantic_hash),
            }
            for snapshot in snapshots
        ],
        key=lambda item: item["scene_id"],
    )


__all__ = ["build_scene_fragment", "merge_graphs", "source_snapshot_refs"]
