"""Normalize the compiled Ontology graph into the three governed layers.

The Markdown compiler deliberately remains backwards compatible with the
existing ``OntologyGraph`` shape.  This module is the publish-time boundary:
it adds the explicit layer and mapping-governance objects that Neo4j needs,
without inventing business metrics or changing the source graph semantics.
"""

from __future__ import annotations

from typing import Any

_ANALYSIS_NODE_TYPES = {
    "rule",
    "analysis_dimension",
    "analysis_path",
    "analysis_rule",
    "clarification_rule",
    "alignment_condition",
}
_MAPPING_NODE_TYPES = {
    "scene",
    "scene_snapshot",
    "result_binding",
    "evidence",
    "review_record",
    "source_document",
}
_ANALYSIS_EDGE_TYPES = {
    "cross_scene_join",
    "metric_relation",
    "clarification_rule",
    "applies_to_metric",
    "applies_to_entity",
    "from_metric",
    "to_metric",
    "requires",
}
_MAPPING_EDGE_TYPES = {
    "scene_binding",
    "provided_by",
    "has_snapshot",
    "declares_binding",
    "maps_to",
    "supported_by",
    "from_document",
    "reviews",
}
_NEO4J_RELATION_TYPES = {
    "relation": "related_to",
    "belongs_to": "belongs_to",
    "provided_by": "provided_by",
    "scene_binding": "scene_binding",
    "cross_scene_join": "cross_scene_join",
    "clarification_rule": "clarification_rule",
    "metric_relation": "related_to_metric",
    "has_snapshot": "has_snapshot",
    "declares_binding": "declares_binding",
    "maps_to": "maps_to",
    "supported_by": "supported_by",
    "from_document": "from_document",
    "reviews": "reviews",
    "applies_to_metric": "applies_to_metric",
    "applies_to_entity": "applies_to_entity",
    "from_metric": "from_metric",
    "to_metric": "to_metric",
    "requires": "requires",
}
_NEO4J_NODE_LABELS = {
    "entity": "Entity",
    "metric": "Metric",
    "scene": "Scene",
    "rule": "AnalysisRule",
    "analysis_dimension": "AnalysisDimension",
    "analysis_path": "AnalysisPath",
    "analysis_rule": "AnalysisRule",
    "clarification_rule": "ClarificationRule",
    "alignment_condition": "AlignmentCondition",
    "scene_snapshot": "SceneSnapshot",
    "result_binding": "ResultBinding",
    "evidence": "Evidence",
    "review_record": "ReviewRecord",
    "source_document": "SourceDocument",
}


def normalize_ontology_graph(
    graph: dict[str, Any],
    *,
    snapshot_id: str,
    ontology_id: str,
    revision: int,
    source_scene_snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a stable, layer-aware write manifest.

    ``source_scene_snapshots`` is metadata already recorded by the lifecycle
    service.  It is used only to materialize ``SceneSnapshot`` and
    ``ResultBinding`` governance objects; it never creates a Metric.  A result
    binding is materialized only when the compiled graph contains an explicit
    ``scene_binding`` edge with a semantic key.
    """
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    normalized_nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()

    def add_node(node: dict[str, Any], *, object_type: str | None = None) -> None:
        object_id = str(node.get("id") or "")
        if not object_id or object_id in node_ids:
            return
        node_type = object_type or str(node.get("type") or "")
        layer = _node_layer(node_type)
        data = node.get("data") or {}
        properties = {
            "snapshot_id": snapshot_id,
            "ontology_id": ontology_id,
            "revision": revision,
            "object_id": object_id,
            "object_type": node_type,
            "layer": layer,
            "name": str(node.get("name") or object_id),
            "aliases": list(node.get("aliases") or []),
            "description": str(node.get("description") or ""),
            "data_json": _json_value(data),
            "provenance_json": _json_value(node.get("provenance") or []),
            "source_scene_ids": _source_scenes(node),
            "status": str(data.get("status") or "candidate"),
        }
        for field in (
            "scene_id",
            "scene_snapshot_id",
            "semantic_key",
            "target_object_id",
            "document_type",
            "semantic_md_hash",
            "source_metric_id",
            "section",
            "quote",
        ):
            value = data.get(field)
            if value is not None:
                properties[field] = str(value)
        normalized_nodes.append(
            {
                "object_id": object_id,
                "layer": layer,
                "object_type": node_type,
                "neo4j_label": _NEO4J_NODE_LABELS.get(node_type, "OntologyObject"),
                "properties": properties,
            }
        )
        node_ids.add(object_id)

    for node in nodes:
        add_node(node)

    source_refs = [
        item for item in (source_scene_snapshots or []) if item.get("scene_id")
    ]
    snapshot_node_by_scene: dict[str, str] = {}
    snapshot_version_by_scene: dict[str, str] = {}
    source_document_by_scene: dict[str, str] = {}
    for ref in source_refs:
        scene_id = str(ref["scene_id"])
        scene_snapshot_id = str(ref.get("snapshot_id") or "")
        if not scene_snapshot_id:
            continue
        add_node(
            {
                "id": scene_id,
                "type": "scene",
                "name": scene_id,
                "data": {"source_scene": scene_id},
                "provenance": [{"scene_id": scene_id}],
            }
        )
        object_id = f"{scene_id}__scene_snapshot__{scene_snapshot_id}"
        snapshot_node_by_scene[scene_id] = object_id
        snapshot_version_by_scene[scene_id] = scene_snapshot_id
        add_node(
            {
                "id": object_id,
                "type": "scene_snapshot",
                "name": scene_snapshot_id,
                "description": "场景发布快照",
                "data": {
                    **ref,
                    "scene_id": scene_id,
                    "scene_snapshot_id": scene_snapshot_id,
                },
                "provenance": [
                    {"scene_id": scene_id, "snapshot_id": scene_snapshot_id}
                ],
            }
        )
        document_id = f"{scene_id}__semantic_document__{scene_snapshot_id}"
        source_document_by_scene[scene_id] = document_id
        add_node(
            {
                "id": document_id,
                "type": "source_document",
                "name": f"{scene_id} 语义文档",
                "description": "场景快照中的语义文档来源记录",
                "data": {
                    "scene_id": scene_id,
                    "scene_snapshot_id": scene_snapshot_id,
                    "document_type": "semantic",
                    "semantic_md_hash": str(ref.get("semantic_md_hash") or ""),
                },
                "provenance": [
                    {"scene_id": scene_id, "snapshot_id": scene_snapshot_id}
                ],
            }
        )

    normalized_edges: list[dict[str, Any]] = []
    edge_ids: set[str] = set()

    def add_edge(
        relation_id: str,
        source: str,
        target: str,
        edge_type: str,
        *,
        name: str = "",
        data: dict[str, Any] | None = None,
        provenance: list[dict[str, Any]] | None = None,
        source_scene_ids: list[str] | None = None,
    ) -> None:
        if not relation_id or relation_id in edge_ids:
            return
        if source not in node_ids or target not in node_ids:
            raise ValueError(
                f"本体关系 {relation_id} 引用了不存在的对象：{source} -> {target}"
            )
        layer = _edge_layer(edge_type)
        edge_data = data or {}
        properties = {
            "snapshot_id": snapshot_id,
            "relation_id": relation_id,
            "relation_type": edge_type,
            "layer": layer,
            "name": name or edge_type,
            "description": str(edge_data.get("description") or ""),
            "data_json": _json_value(edge_data),
            "provenance_json": _json_value(provenance or []),
            "source_scene_ids": source_scene_ids or [],
            "status": str(edge_data.get("status") or "candidate"),
        }
        normalized_edges.append(
            {
                "relation_id": relation_id,
                "source_id": source,
                "target_id": target,
                "neo4j_type": _NEO4J_RELATION_TYPES.get(edge_type, "related_to"),
                "layer": layer,
                "properties": properties,
            }
        )
        edge_ids.add(relation_id)

    # Preserve all explicitly authored/compiled graph edges.
    binding_edges: list[tuple[str, str, str, dict[str, Any], list[dict[str, Any]]]] = []
    for edge in edges:
        relation_id = str(edge.get("id") or "")
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        edge_type = str(edge.get("type") or "")
        if not relation_id or not source or not target:
            continue
        edge_data = edge.get("data") or {}
        provenance = edge.get("provenance") or []
        add_edge(
            relation_id,
            source,
            target,
            edge_type,
            name=str(edge.get("name") or edge_type),
            data=edge_data,
            provenance=provenance,
            source_scene_ids=_source_scenes(edge),
        )
        if edge_type == "scene_binding":
            binding_edges.append((source, target, relation_id, edge_data, provenance))

    # Add governance objects around explicit scene bindings and scene snapshots.
    for scene_id, scene_snapshot_object_id in snapshot_node_by_scene.items():
        add_edge(
            f"has_snapshot_{scene_id}_{scene_snapshot_object_id}",
            scene_id,
            scene_snapshot_object_id,
            "has_snapshot",
            name="场景拥有快照",
            source_scene_ids=[scene_id],
        )

    for node in nodes:
        if str(node.get("type") or "") != "metric":
            continue
        metric_id = str(node.get("id") or "")
        for index, source_ref in enumerate(
            (node.get("data") or {}).get("source_metric_refs") or []
        ):
            if not isinstance(source_ref, dict):
                continue
            scene_id = str(source_ref.get("scene_id") or "")
            source_metric_id = str(source_ref.get("metric_id") or "")
            quote = str(source_ref.get("evidence") or "")
            section = str(source_ref.get("section") or "")
            if not scene_id or not source_metric_id or not quote or not section:
                raise ValueError(
                    f"指标 {metric_id} 的来源追溯缺少场景级指标、区段或证据。"
                )
            evidence_id = (
                f"{metric_id}__evidence__{scene_id}__{source_metric_id}__{index}"
            )
            add_node(
                {
                    "id": evidence_id,
                    "type": "evidence",
                    "name": f"{metric_id} 来源证据 {index + 1}",
                    "description": quote,
                    "data": {
                        "scene_id": scene_id,
                        "source_metric_id": source_metric_id,
                        "semantic_key": str(source_ref.get("semantic_key") or ""),
                        "section": section,
                        "quote": quote,
                    },
                    "provenance": [{"scene_id": scene_id}],
                }
            )
            add_edge(
                f"supported_by_{metric_id}_{evidence_id}",
                metric_id,
                evidence_id,
                "supported_by",
                name="指标有来源证据",
                data={"scene_metric_id": source_metric_id},
                source_scene_ids=[scene_id],
            )
            source_document_id = source_document_by_scene.get(scene_id)
            if source_document_id:
                add_edge(
                    f"evidence_from_document_{evidence_id}_{source_document_id}",
                    evidence_id,
                    source_document_id,
                    "from_document",
                    name="证据来自语义文档",
                    data={"section": section},
                    source_scene_ids=[scene_id],
                )

    for scene_id, metric_id, _, data, provenance in binding_edges:
        semantic_key = str(data.get("semantic_key") or "")
        if not semantic_key:
            continue
        binding_id = f"{scene_id}__result_binding__{semantic_key}__{metric_id}"
        scene_snapshot_object_id = snapshot_node_by_scene.get(scene_id)
        add_node(
            {
                "id": binding_id,
                "type": "result_binding",
                "name": semantic_key,
                "description": "场景结果列与本体对象的显式语义绑定",
                "data": {
                    "scene_id": scene_id,
                    "semantic_key": semantic_key,
                    "scene_snapshot_id": snapshot_version_by_scene.get(scene_id),
                    "target_object_id": metric_id,
                },
                "provenance": provenance,
            }
        )
        add_edge(
            f"declares_binding_{binding_id}",
            scene_snapshot_object_id or scene_id,
            binding_id,
            "declares_binding",
            name="场景声明绑定",
            data={"semantic_key": semantic_key},
            provenance=provenance,
            source_scene_ids=[scene_id],
        )
        add_edge(
            f"maps_to_{binding_id}_{metric_id}",
            binding_id,
            metric_id,
            "maps_to",
            name="绑定到本体对象",
            data={"semantic_key": semantic_key},
            provenance=provenance,
            source_scene_ids=[scene_id],
        )

    layer_counts = {
        layer: {
            "nodes": sum(item["layer"] == layer for item in normalized_nodes),
            "relationships": sum(item["layer"] == layer for item in normalized_edges),
        }
        for layer in (
            "business_semantics",
            "analysis_explanations",
            "mapping_governance",
        )
    }
    return {
        "snapshot_id": snapshot_id,
        "ontology_id": ontology_id,
        "revision": revision,
        "source_scene_snapshots": source_refs,
        "nodes": normalized_nodes,
        "relationships": normalized_edges,
        "node_count": len(normalized_nodes),
        "relationship_count": len(normalized_edges),
        "layer_counts": layer_counts,
    }


def _node_layer(node_type: str) -> str:
    if node_type in _MAPPING_NODE_TYPES:
        return "mapping_governance"
    if node_type in _ANALYSIS_NODE_TYPES:
        return "analysis_explanations"
    return "business_semantics"


def _edge_layer(edge_type: str) -> str:
    if edge_type in _MAPPING_EDGE_TYPES:
        return "mapping_governance"
    if edge_type in _ANALYSIS_EDGE_TYPES:
        return "analysis_explanations"
    return "business_semantics"


def _source_scenes(item: dict[str, Any]) -> list[str]:
    values: set[str] = set()
    data = item.get("data") or {}
    for raw in (
        item.get("source_scenes"),
        item.get("scenes"),
        data.get("source_scene"),
        data.get("source_scenes"),
        data.get("scenes"),
        data.get("scene_id"),
    ):
        if isinstance(raw, str):
            values.update(part.strip() for part in raw.split(",") if part.strip())
        elif isinstance(raw, (list, tuple, set)):
            values.update(str(value) for value in raw if value)
    for entry in item.get("provenance") or []:
        if isinstance(entry, dict) and entry.get("scene_id"):
            values.add(str(entry["scene_id"]))
    return sorted(values)


def _json_value(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = ["normalize_ontology_graph"]
