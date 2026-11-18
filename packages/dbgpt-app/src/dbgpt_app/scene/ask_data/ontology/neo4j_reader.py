"""Read-only access to the published Ontology graph in Neo4j.

Cypher is kept here as fixed, parameterized queries.  Runtime callers provide
only a snapshot id; an LLM never supplies a query or relationship type.
"""

from __future__ import annotations

from typing import Any

from .neo4j_store import Neo4jOntologyError, Neo4jOntologyStore
from .schemas import OntologySnapshot


class Neo4jOntologyReader:
    def __init__(self, store: Neo4jOntologyStore | None = None):
        self.store = store or Neo4jOntologyStore()

    @property
    def enabled(self) -> bool:
        return self.store.enabled

    def active_snapshot_id(self) -> str | None:
        return self.store.active_snapshot_id()

    def read_snapshot(
        self,
        snapshot_id: str,
        *,
        scene_ids: list[str] | None = None,
        scene_snapshot_ids: list[str] | None = None,
        semantic_keys: list[str] | None = None,
        field_refs: list[str] | None = None,
    ) -> OntologySnapshot:
        if not snapshot_id:
            raise Neo4jOntologyError("本体快照 ID 不能为空。")
        if not self.enabled:
            raise Neo4jOntologyError("Neo4j 本体读取未启用。")
        driver = self.store._get_driver()
        try:
            with driver.session(database=self.store.config.database) as session:
                record = session.run(
                    """
                    MATCH (s:OntologySnapshot {snapshot_id: $snapshot_id})
                    RETURN s.snapshot_id AS snapshot_id, s.ontology_id AS ontology_id,
                      s.revision AS revision, s.status AS status,
                      s.content_hash AS content_hash, s.compiler_version AS compiler_version,
                      s.source_scene_snapshots_json AS source_scene_snapshots_json
                    """,
                    snapshot_id=snapshot_id,
                ).single()
            if record is None:
                raise Neo4jOntologyError("ONTOLOGY_SNAPSHOT_NOT_FOUND")
            status = str(record["status"] or "")
            if status not in {"ready", "active"}:
                raise Neo4jOntologyError("ONTOLOGY_SNAPSHOT_NOT_READY")
            nodes_by_id: dict[str, dict[str, Any]] = {}
            edges_by_id: dict[str, dict[str, Any]] = {}
            scenes = [str(item) for item in (scene_ids or []) if str(item)]
            scene_snapshots = [
                str(item) for item in (scene_snapshot_ids or []) if str(item)
            ]
            keys = [str(item) for item in (semantic_keys or []) if str(item)]
            refs = [str(item) for item in (field_refs or []) if str(item)]
            with driver.session(database=self.store.config.database) as session:
                if not scenes and not scene_snapshots and not keys and not refs:
                    full_record = session.run(
                        """
                        MATCH (n:OntologyObject {snapshot_id: $snapshot_id})
                        WITH collect(properties(n)) AS raw_nodes
                        OPTIONAL MATCH (a:OntologyObject {snapshot_id: $snapshot_id})
                          -[r]->(b:OntologyObject {snapshot_id: $snapshot_id})
                        WHERE type(r) <> 'CONTAINS'
                        RETURN raw_nodes,
                          collect({source_id: a.object_id, target_id: b.object_id,
                            relation_id: r.relation_id, relation_type: r.relation_type,
                            name: r.name, description: r.description,
                            data_json: r.data_json, provenance_json: r.provenance_json,
                            status: r.status}) AS raw_edges
                        """,
                        snapshot_id=snapshot_id,
                    ).single()
                    for item in (full_record["raw_nodes"] if full_record else []):
                        _put_node(nodes_by_id, item or {})
                    for item in (full_record["raw_edges"] if full_record else []):
                        _put_edge(edges_by_id, item or {})
                else:
                    binding_records = session.run(
                    """
                    MATCH (scene:OntologyObject {snapshot_id: $snapshot_id,
                      object_type: 'scene'})
                      -[:HAS_SNAPSHOT {snapshot_id: $snapshot_id}]->
                      (scene_snapshot:OntologyObject {snapshot_id: $snapshot_id,
                        object_type: 'scene_snapshot'})
                      -[:DECLARES_BINDING {snapshot_id: $snapshot_id}]->
                      (binding:OntologyObject {snapshot_id: $snapshot_id,
                        object_type: 'result_binding'})
                      -[:MAPS_TO {snapshot_id: $snapshot_id}]->
                      (metric:OntologyObject {snapshot_id: $snapshot_id})
                    WHERE scene.object_id IN $scene_ids
                      AND coalesce(binding.status, 'confirmed') = 'confirmed'
                      AND (size($scene_snapshot_ids) = 0
                           OR scene_snapshot.scene_snapshot_id IN $scene_snapshot_ids)
                      AND (binding.semantic_key IN $semantic_keys
                           OR binding.field_ref IN $field_refs)
                    RETURN properties(scene) AS scene,
                           properties(metric) AS metric,
                           properties(binding) AS binding
                    """,
                    snapshot_id=snapshot_id,
                    scene_ids=scenes,
                    scene_snapshot_ids=scene_snapshots,
                    semantic_keys=keys,
                    field_refs=refs,
                    )
                    matched_metrics: set[str] = set()
                    for row in binding_records:
                        scene = row["scene"] or {}
                        metric = row["metric"] or {}
                        binding = row["binding"] or {}
                        _put_node(nodes_by_id, scene)
                        _put_node(nodes_by_id, metric)
                        _put_edge(
                            edges_by_id,
                            {
                                "relation_id": str(binding.get("object_id") or ""),
                                "relation_type": "scene_binding",
                                "source_id": str(scene.get("object_id") or ""),
                                "target_id": str(metric.get("object_id") or ""),
                                "name": "场景结果列语义绑定",
                                "semantic_key": binding.get("semantic_key"),
                                "scene_snapshot_id": binding.get("scene_snapshot_id"),
                                "field_ref": binding.get("field_ref"),
                                "data_json": binding.get("data_json"),
                                "provenance_json": binding.get("provenance_json"),
                                "status": binding.get("status"),
                            },
                        )
                        if metric.get("object_id"):
                            matched_metrics.add(str(metric["object_id"]))
                    if matched_metrics:
                        neighbor_records = session.run(
                        """
                        MATCH (matched:OntologyObject {snapshot_id: $snapshot_id})
                        WHERE matched.object_id IN $metric_ids
                        MATCH (matched)-[r]-(neighbor:OntologyObject {snapshot_id: $snapshot_id})
                        WHERE type(r) IN $relationship_types
                          AND r.snapshot_id = $snapshot_id
                        RETURN properties(matched) AS matched,
                               properties(neighbor) AS neighbor,
                               properties(r) AS relation,
                               startNode(r).object_id AS start_id,
                               endNode(r).object_id AS end_id
                        """,
                        snapshot_id=snapshot_id,
                        metric_ids=sorted(matched_metrics),
                        relationship_types=_NEIGHBOR_RELATIONSHIP_TYPES,
                        )
                        for row in neighbor_records:
                            _put_node(nodes_by_id, row["matched"] or {})
                            _put_node(nodes_by_id, row["neighbor"] or {})
                            relation = dict(row["relation"] or {})
                            relation["source_id"] = row["start_id"]
                            relation["target_id"] = row["end_id"]
                            _put_edge(edges_by_id, relation)
                        visible_scene_ids = [
                            object_id
                            for object_id, properties in nodes_by_id.items()
                            if properties.get("object_type") == "scene"
                        ]
                        if visible_scene_ids:
                            bridge_records = session.run(
                                """
                                MATCH (scene:OntologyObject {snapshot_id: $snapshot_id})
                                  -[r:CROSS_SCENE_JOIN {snapshot_id: $snapshot_id}]->
                                  (other:OntologyObject {snapshot_id: $snapshot_id})
                                WHERE scene.object_id IN $scene_ids
                                RETURN properties(scene) AS scene,
                                       properties(other) AS other,
                                       properties(r) AS relation
                                """,
                                snapshot_id=snapshot_id,
                                scene_ids=visible_scene_ids,
                            )
                            for row in bridge_records:
                                _put_node(nodes_by_id, row["scene"] or {})
                                _put_node(nodes_by_id, row["other"] or {})
                                relation = dict(row["relation"] or {})
                                relation["source_id"] = row["scene"].get("object_id")
                                relation["target_id"] = row["other"].get("object_id")
                                _put_edge(edges_by_id, relation)
                            path_records = session.run(
                                """
                                MATCH (path:OntologyObject {snapshot_id: $snapshot_id})
                                WHERE path.object_type = 'analysis_path'
                                  AND any(scene IN $scene_ids
                                          WHERE scene IN path.source_scene_ids)
                                RETURN properties(path) AS path
                                """,
                                snapshot_id=snapshot_id,
                                scene_ids=visible_scene_ids,
                            )
                            for row in path_records:
                                _put_node(nodes_by_id, row["path"] or {})
                    else:
                        scene_records = session.run(
                        """
                        MATCH (scene:OntologyObject {snapshot_id: $snapshot_id})
                        WHERE scene.object_type = 'scene' AND scene.object_id IN $scene_ids
                        RETURN properties(scene) AS scene
                        """,
                        snapshot_id=snapshot_id,
                        scene_ids=scenes,
                        )
                        for row in scene_records:
                            _put_node(nodes_by_id, row["scene"] or {})
            nodes = [_node(item) for item in nodes_by_id.values()]
            edges = [_edge(item) for item in edges_by_id.values()]
            graph = {
                "nodes": [item for item in nodes if item["type"] in _GRAPH_NODE_TYPES],
                "edges": [item for item in edges if item["type"] in _GRAPH_EDGE_TYPES],
            }
            source_refs = _parse_json(record["source_scene_snapshots_json"], [])
            from .markdown import build_prompt_projection
            from .schemas import OntologyGraph

            try:
                prompt_projection = build_prompt_projection(OntologyGraph.model_validate(graph))
            except Exception:
                prompt_projection = {"nodes": graph["nodes"], "edges": graph["edges"]}
            return OntologySnapshot(
                snapshot_id=str(record["snapshot_id"]),
                ontology_id=str(record["ontology_id"]),
                revision=int(record["revision"]),
                status=status,
                content_hash=str(record["content_hash"]),
                graph_json=graph,
                prompt_projection_json=prompt_projection,
                source_scene_snapshots=source_refs if isinstance(source_refs, list) else [],
                compiler_version=str(record["compiler_version"] or "1"),
            )
        except Neo4jOntologyError:
            raise
        except Exception as exc:
            raise Neo4jOntologyError(f"Neo4j 本体快照读取失败：{exc}") from exc


_GRAPH_NODE_TYPES = {
    "entity", "metric", "scene", "rule", "analysis_dimension",
    "analysis_path", "analysis_rule",
}
_GRAPH_EDGE_TYPES = {
    "relation", "belongs_to", "provided_by", "scene_binding",
    "cross_scene_join", "clarification_rule", "metric_relation",
}
_NEIGHBOR_RELATIONSHIP_TYPES = [
    "RELATED_TO",
    "RELATED_TO_METRIC",
    "BELONGS_TO",
    "APPLIES_TO_METRIC",
    "APPLIES_TO_ENTITY",
    "CROSS_SCENE_JOIN",
    "CLARIFICATION_RULE",
]


def _put_node(nodes: dict[str, dict[str, Any]], properties: dict[str, Any]) -> None:
    object_id = str(properties.get("object_id") or "")
    if object_id:
        nodes[object_id] = properties


def _put_edge(edges: dict[str, dict[str, Any]], properties: dict[str, Any]) -> None:
    relation_id = str(properties.get("relation_id") or "")
    if relation_id:
        edges[relation_id] = properties


def _node(item: dict[str, Any]) -> dict[str, Any]:
    data = _parse_json(item.get("data_json"), {})
    if not isinstance(data, dict):
        data = {}
    if item.get("source_scene_ids") and "source_scenes" not in data:
        data["source_scenes"] = item["source_scene_ids"]
    if item.get("status") and "status" not in data:
        data["status"] = item["status"]
    for key in (
        "scene_id",
        "scene_snapshot_id",
        "semantic_key",
        "field_ref",
        "source_field",
        "aggregation",
        "unit",
    ):
        if item.get(key) is not None:
            data.setdefault(key, item[key])
    return {
        "id": str(item.get("object_id") or item.get("id") or ""),
        "type": str(item.get("object_type") or item.get("type") or ""),
        "name": str(item.get("name") or item.get("object_id") or item.get("id") or ""),
        "aliases": list(item.get("aliases") or []),
        "description": str(item.get("description") or ""),
        "data": data,
        "provenance": _parse_json(item.get("provenance_json"), []),
    }


def _edge(item: dict[str, Any]) -> dict[str, Any]:
    data = _parse_json(item.get("data_json"), {})
    if not isinstance(data, dict):
        data = {}
    for key in ("semantic_key", "field_ref", "scene_snapshot_id", "source_field"):
        if item.get(key) is not None:
            data.setdefault(key, item[key])
    return {
        "id": str(item.get("relation_id") or item.get("id") or ""),
        "source": str(item.get("source_id") or item.get("source") or ""),
        "target": str(item.get("target_id") or item.get("target") or ""),
        "type": str(item.get("relation_type") or item.get("type") or ""),
        "name": str(item.get("name") or item.get("relation_type") or item.get("type") or ""),
        "description": str(item.get("description") or ""),
        "data": data,
        "provenance": _parse_json(item.get("provenance_json"), []),
    }


def _parse_json(value: Any, default: Any) -> Any:
    import json

    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


__all__ = ["Neo4jOntologyReader"]
