"""Derive safe, local Ontology fragments from active Scene Snapshots."""

from __future__ import annotations

from typing import Any

from ..scene.markdown import SemanticMarkdownParser
from ..query.capabilities import build_query_capabilities
from ..query.query_spec import build_field_ref
from .schemas import OntologyEdge, OntologyGraph, OntologyNode


def scene_snapshot(source: Any) -> Any:
    return source.get("snapshot") if isinstance(source, dict) else source


def build_scene_fragment(snapshot: Any) -> OntologyGraph:
    """Expose declared semantic metrics, never inferred query capabilities."""
    snapshot = scene_snapshot(snapshot)
    projection = getattr(snapshot, "routing_projection", {}) or {}
    runtime_config = getattr(snapshot, "runtime_config", {}) or {}
    documents = runtime_config.get("documents", {})
    semantic_md = str(documents.get("semantic_md") or "")
    try:
        semantic_config = SemanticMarkdownParser().parse(semantic_md).config
        declared_metric_keys = {item.key for item in semantic_config.metrics}
    except Exception:
        declared_metric_keys = set()
    scene_id = str(getattr(snapshot, "scene_id", ""))
    snapshot_id = str(getattr(snapshot, "snapshot_id", ""))
    if not scene_id:
        return OntologyGraph()
    provenance = [{"scene_id": scene_id, "snapshot_id": snapshot_id}]
    capabilities = build_query_capabilities(snapshot)
    metrics_by_key = {
        str(item.get("key")): item
        for item in capabilities.metrics
        if item.get("key")
    }
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
    for metric in capabilities.metrics:
        if not metric.get("key") or str(metric["key"]) not in declared_metric_keys:
            continue
        result_key = str(metric["key"])
        semantic_key = str(metric.get("semantic_key") or result_key)
        metric_id = f"{scene_id}__{semantic_key}"
        capability = metrics_by_key.get(result_key, {})
        source_field = str(capability.get("field") or "").strip() or None
        metric_data = {
            "source_scene": scene_id,
            "semantic_key": semantic_key,
            "result_key": result_key,
            "source_field": source_field,
            "field_ref": (
                build_field_ref(scene_id, snapshot_id, source_field)
                if source_field
                else None
            ),
            "aggregation": capability.get("aggregation"),
            "unit": capability.get("unit"),
            "scene_snapshot_id": snapshot_id,
        }
        metric_data = {key: value for key, value in metric_data.items() if value is not None}
        nodes.append(
            OntologyNode(
                id=metric_id,
                type="metric",
                name=str(metric.get("name") or semantic_key),
                aliases=[str(item) for item in metric.get("aliases", [])],
                data=metric_data,
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
                    id=f"binding_{scene_id}_{result_key}_{metric_id}",
                    source=scene_id,
                    target=metric_id,
                    type="scene_binding",
                    name="场景语义绑定",
                    data=metric_data,
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
                "semantic_md_hash": str(snapshot.source_hashes.semantic_hash),
            }
            for source in snapshots
            if (snapshot := scene_snapshot(source)) is not None
        ],
        key=lambda item: item["scene_id"],
    )


__all__ = [
    "build_scene_fragment",
    "merge_graphs",
    "scene_snapshot",
    "source_snapshot_refs",
]
