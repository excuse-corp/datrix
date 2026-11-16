"""Neo4j persistence for published three-layer Ontology snapshots."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .graph_model import normalize_ontology_graph
from .neo4j_schema import ensure_ontology_schema

_NODE_LABELS = {
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
_RELATIONSHIP_TYPES = {
    "related_to": "RELATED_TO",
    "belongs_to": "BELONGS_TO",
    "provided_by": "PROVIDED_BY",
    "scene_binding": "SCENE_BINDING",
    "cross_scene_join": "CROSS_SCENE_JOIN",
    "clarification_rule": "CLARIFICATION_RULE",
    "related_to_metric": "RELATED_TO_METRIC",
    "has_snapshot": "HAS_SNAPSHOT",
    "declares_binding": "DECLARES_BINDING",
    "maps_to": "MAPS_TO",
    "supported_by": "SUPPORTED_BY",
    "from_document": "FROM_DOCUMENT",
    "reviews": "REVIEWS",
    "applies_to_metric": "APPLIES_TO_METRIC",
    "applies_to_entity": "APPLIES_TO_ENTITY",
    "from_metric": "FROM_METRIC",
    "to_metric": "TO_METRIC",
    "requires": "REQUIRES",
}


class Neo4jOntologyError(RuntimeError):
    """Raised when the Ontology graph cannot be persisted or verified."""


@dataclass(frozen=True)
class Neo4jOntologyConfig:
    enabled: bool
    uri: str
    username: str
    password: str
    database: str
    max_connection_pool_size: int = 20
    connection_timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "Neo4jOntologyConfig":
        return cls(
            os.getenv("DATAMAN_NEO4J_ENABLED", "0").lower()
            in {"1", "true", "yes", "on"},
            os.getenv("DATAMAN_NEO4J_URI", "bolt://127.0.0.1:7670"),
            os.getenv("DATAMAN_NEO4J_USERNAME", "neo4j"),
            os.getenv("DATAMAN_NEO4J_PASSWORD", ""),
            os.getenv("DATAMAN_NEO4J_DATABASE", "neo4j"),
            int(os.getenv("DATAMAN_NEO4J_MAX_CONNECTION_POOL_SIZE", "20")),
            float(os.getenv("DATAMAN_NEO4J_CONNECT_TIMEOUT_SECONDS", "5")),
        )


class Neo4jOntologyStore:
    """Write immutable, versioned graph snapshots to Neo4j.

    This class deliberately exposes only snapshot synchronization and health
    checks. Runtime retrieval is a separate follow-up feature.
    """

    def __init__(self, config: Neo4jOntologyConfig | None = None):
        self.config = config or Neo4jOntologyConfig.from_env()
        self._driver = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def _get_driver(self):
        if not self.enabled:
            return None
        if self._driver is not None:
            return self._driver
        if not self.config.password:
            raise Neo4jOntologyError("DATAMAN_NEO4J_PASSWORD 未配置。")
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise Neo4jOntologyError(
                "Neo4j Python Driver 未安装，请安装 neo4j。"
            ) from exc
        self._driver = GraphDatabase.driver(
            self.config.uri,
            auth=(self.config.username, self.config.password),
            max_connection_pool_size=self.config.max_connection_pool_size,
            connection_timeout=self.config.connection_timeout_seconds,
        )
        return self._driver

    def _ensure_schema(self, driver) -> None:
        """Create the small, idempotent schema used by the snapshot writer."""
        try:
            ensure_ontology_schema(driver, self.config.database)
        except Exception as exc:
            raise Neo4jOntologyError(f"Neo4j 本体图 schema 初始化失败：{exc}") from exc

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def health(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "status": "disabled"}
        driver = self._get_driver()
        try:
            self._ensure_schema(driver)
            driver.verify_connectivity()
            with driver.session(database=self.config.database) as session:
                record = session.run("RETURN 1 AS ready").single()
            return {
                "enabled": True,
                "status": "ready",
                "uri": self.config.uri,
                "database": self.config.database,
                "ready": bool(record and record["ready"] == 1),
            }
        except Exception as exc:
            raise Neo4jOntologyError(f"Neo4j 健康检查失败：{exc}") from exc

    def snapshot_status(self, snapshot_id: str) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "status": "disabled", "snapshot_id": snapshot_id}
        driver = self._get_driver()
        try:
            with driver.session(database=self.config.database) as session:
                record = session.run(
                    """
                    MATCH (s:OntologySnapshot {snapshot_id: $snapshot_id})
                    OPTIONAL MATCH (n:OntologyObject {snapshot_id: $snapshot_id})
                    WITH s, count(n) AS nodes
                    OPTIONAL MATCH ()-[r {snapshot_id: $snapshot_id}]->()
                    WHERE type(r) <> 'CONTAINS'
                    RETURN s.status AS status, s.content_hash AS content_hash,
                           nodes, count(r) AS relationships,
                           s.revision AS revision, s.ontology_id AS ontology_id,
                           s.sync_error AS sync_error,
                           s.layer_counts_json AS layer_counts_json
                    """,
                    snapshot_id=snapshot_id,
                ).single()
            if record is None:
                return {
                    "enabled": True,
                    "status": "missing",
                    "snapshot_id": snapshot_id,
                }
            return {
                "enabled": True,
                "status": record["status"],
                "snapshot_id": snapshot_id,
                "content_hash": record["content_hash"],
                "revision": record["revision"],
                "ontology_id": record["ontology_id"],
                "node_count": record["nodes"],
                "relationship_count": record["relationships"],
                "sync_error": record["sync_error"] or None,
                "layer_counts": _parse_json(record["layer_counts_json"], {}),
            }
        except Exception as exc:
            raise Neo4jOntologyError(f"Neo4j 快照状态查询失败：{exc}") from exc

    def active_snapshot_id(self) -> str | None:
        if not self.enabled:
            return None
        driver = self._get_driver()
        try:
            with driver.session(database=self.config.database) as session:
                record = session.run(
                    "MATCH (s:OntologySnapshot {status: 'active'}) "
                    "RETURN s.snapshot_id AS snapshot_id LIMIT 1"
                ).single()
            return str(record["snapshot_id"]) if record else None
        except Exception as exc:
            raise Neo4jOntologyError(f"Neo4j 活动快照查询失败：{exc}") from exc

    def activate_snapshot(self, snapshot_id: str) -> None:
        if not self.enabled:
            return
        driver = self._get_driver()
        try:
            with driver.session(database=self.config.database) as session:
                session.execute_write(self._activate_snapshot, snapshot_id)
        except Exception as exc:
            raise Neo4jOntologyError(f"Neo4j 快照激活失败：{exc}") from exc

    def deactivate_snapshot(self, snapshot_id: str) -> None:
        """Return a newly activated snapshot to ready during rollback."""
        if not self.enabled:
            return
        driver = self._get_driver()
        try:
            with driver.session(database=self.config.database) as session:
                session.execute_write(self._deactivate_snapshot, snapshot_id)
        except Exception as exc:
            raise Neo4jOntologyError(f"Neo4j 快照激活回滚失败：{exc}") from exc

    @staticmethod
    def _deactivate_snapshot(tx, snapshot_id: str) -> None:
        tx.run(
            "MATCH (s:OntologySnapshot {snapshot_id: $snapshot_id, status: 'active'}) "
            "SET s.status = 'ready' REMOVE s.activated_at",
            snapshot_id=snapshot_id,
        ).consume()

    @staticmethod
    def _activate_snapshot(tx, snapshot_id: str) -> None:
        record = tx.run(
            "MATCH (s:OntologySnapshot {snapshot_id: $snapshot_id}) "
            "RETURN s.status AS status",
            snapshot_id=snapshot_id,
        ).single()
        if record is None or record["status"] not in {"ready", "active"}:
            raise Neo4jOntologyError("Neo4j 快照不存在或尚未 ready，不能激活。")
        tx.run(
            "MATCH (s:OntologySnapshot {status: 'active'}) "
            "WHERE s.snapshot_id <> $snapshot_id SET s.status = 'superseded'",
            snapshot_id=snapshot_id,
        ).consume()
        tx.run(
            "MATCH (s:OntologySnapshot {snapshot_id: $snapshot_id}) "
            "SET s.status = 'active', s.activated_at = datetime()",
            snapshot_id=snapshot_id,
        ).consume()

    def sync_snapshot(self, snapshot: Any) -> dict[str, Any]:
        """Synchronize one already-compiled OntologySnapshot idempotently."""
        if not self.enabled:
            return {
                "enabled": False,
                "status": "skipped",
                "snapshot_id": snapshot.snapshot_id,
            }
        try:
            manifest = normalize_ontology_graph(
                snapshot.graph_json,
                snapshot_id=snapshot.snapshot_id,
                ontology_id=snapshot.ontology_id,
                revision=snapshot.revision,
                source_scene_snapshots=list(snapshot.source_scene_snapshots),
            )
        except Exception as exc:
            raise Neo4jOntologyError(f"三层本体规范化失败：{exc}") from exc
        driver = self._get_driver()
        try:
            self._ensure_schema(driver)
            with driver.session(database=self.config.database) as session:
                result = session.execute_write(self._write_manifest, manifest, snapshot)
            return {"enabled": True, **result}
        except Exception as exc:
            try:
                self._mark_sync_failed(driver, manifest, snapshot, exc)
            except Exception:
                pass
            if isinstance(exc, Neo4jOntologyError):
                raise
            raise Neo4jOntologyError(f"Neo4j 本体快照同步失败：{exc}") from exc

    def _mark_sync_failed(
        self, driver, manifest: dict[str, Any], snapshot: Any, exc: Exception
    ) -> None:
        message = f"{type(exc).__name__}: {exc}"[:1000]
        with driver.session(database=self.config.database) as session:
            session.run(
                """
                MERGE (s:OntologySnapshot {snapshot_id: $snapshot_id})
                ON CREATE SET s.ontology_id = $ontology_id,
                              s.revision = $revision,
                              s.content_hash = $content_hash,
                              s.compiler_version = $compiler_version,
                              s.node_count = $node_count,
                              s.relationship_count = $relationship_count,
                              s.layer_counts_json = $layer_counts_json,
                              s.source_scene_count = $source_scene_count,
                              s.source_scene_snapshots_json = $source_scene_snapshots_json
                SET s.sync_error = $sync_error,
                    s.sync_failed_at = datetime(),
                    s.status = CASE WHEN s.status = 'active' THEN 'active' ELSE 'failed' END
                """,
                snapshot_id=manifest["snapshot_id"],
                ontology_id=manifest["ontology_id"],
                revision=manifest["revision"],
                content_hash=snapshot.content_hash,
                compiler_version=snapshot.compiler_version,
                node_count=manifest["node_count"],
                relationship_count=manifest["relationship_count"],
                layer_counts_json=_json(manifest["layer_counts"]),
                source_scene_count=len(snapshot.source_scene_snapshots),
                source_scene_snapshots_json=_json(snapshot.source_scene_snapshots),
                sync_error=message,
            ).consume()

    @staticmethod
    def _write_manifest(tx, manifest: dict[str, Any], snapshot: Any) -> dict[str, Any]:
        snapshot_id = manifest["snapshot_id"]
        existing = tx.run(
            "MATCH (s:OntologySnapshot {snapshot_id: $snapshot_id}) "
            "RETURN s.status AS status",
            snapshot_id=snapshot_id,
        ).single()
        final_status = (
            "active" if existing and existing["status"] == "active" else "ready"
        )
        tx.run(
            "MATCH (n:OntologyObject {snapshot_id: $snapshot_id}) DETACH DELETE n",
            snapshot_id=snapshot_id,
        ).consume()
        tx.run(
            """
            MERGE (s:OntologySnapshot {snapshot_id: $snapshot_id})
            SET s.ontology_id = $ontology_id,
                s.revision = $revision,
                s.status = 'building',
                s.content_hash = $content_hash,
                s.compiler_version = $compiler_version,
                s.node_count = $node_count,
                s.relationship_count = $relationship_count,
                s.layer_counts_json = $layer_counts_json,
                s.source_scene_count = $source_scene_count,
                s.source_scene_snapshots_json = $source_scene_snapshots_json,
                s.sync_error = '',
                s.sync_failed_at = null
            """,
            snapshot_id=snapshot_id,
            ontology_id=manifest["ontology_id"],
            revision=manifest["revision"],
            content_hash=snapshot.content_hash,
            compiler_version=snapshot.compiler_version,
            node_count=manifest["node_count"],
            relationship_count=manifest["relationship_count"],
            layer_counts_json=_json(manifest["layer_counts"]),
            source_scene_count=len(snapshot.source_scene_snapshots),
            source_scene_snapshots_json=_json(snapshot.source_scene_snapshots),
        ).consume()
        nodes_by_type: dict[str, list[dict[str, Any]]] = {}
        for item in manifest["nodes"]:
            nodes_by_type.setdefault(str(item.get("object_type") or ""), []).append(
                item
            )
        for object_type, items in nodes_by_type.items():
            label = _NODE_LABELS.get(object_type)
            label_clause = f":{label}" if label else ""
            tx.run(
                f"""
                UNWIND $nodes AS item
                CREATE (n:OntologyObject{label_clause})
                SET n = item.properties
                """,
                nodes=items,
            ).consume()
        tx.run(
            """
            MATCH (s:OntologySnapshot {snapshot_id: $snapshot_id})
            MATCH (n:OntologyObject {snapshot_id: $snapshot_id})
            MERGE (s)-[:CONTAINS {snapshot_id: $snapshot_id}]->(n)
            """,
            snapshot_id=snapshot_id,
        ).consume()
        relationships_by_type: dict[str, list[dict[str, Any]]] = {}
        for item in manifest["relationships"]:
            relationship_type = str(item.get("neo4j_type") or "related_to")
            relationships_by_type.setdefault(relationship_type, []).append(item)
        for relationship_type, items in relationships_by_type.items():
            relationship_label = _RELATIONSHIP_TYPES.get(relationship_type)
            if relationship_label is None:
                raise Neo4jOntologyError(f"不支持的本体关系类型：{relationship_type}。")
            tx.run(
                f"""
                UNWIND $relationships AS item
                MATCH (a:OntologyObject {{snapshot_id: $snapshot_id, object_id: item.source_id}})
                MATCH (b:OntologyObject {{snapshot_id: $snapshot_id, object_id: item.target_id}})
                MERGE (a)-[r:{relationship_label} {{snapshot_id: $snapshot_id, relation_id: item.relation_id}}]->(b)
                SET r = item.properties,
                    r.source_id = item.source_id,
                    r.target_id = item.target_id
                """,
                snapshot_id=snapshot_id,
                relationships=items,
            ).consume()
        tx.run(
            """
            MATCH (s:OntologySnapshot {snapshot_id: $snapshot_id})
            SET s.status = $status
            """,
            snapshot_id=snapshot_id,
            status=final_status,
        ).consume()
        node_count = tx.run(
            """
            MATCH (s:OntologySnapshot {snapshot_id: $snapshot_id})
            OPTIONAL MATCH (n:OntologyObject {snapshot_id: $snapshot_id})
            RETURN count(n) AS nodes
            """,
            snapshot_id=snapshot_id,
        ).single()
        relationship_count = tx.run(
            """
            MATCH (s:OntologySnapshot {snapshot_id: $snapshot_id})
            OPTIONAL MATCH ()-[r {snapshot_id: $snapshot_id}]->()
            WHERE type(r) <> 'CONTAINS'
            RETURN count(r) AS relationships
            """,
            snapshot_id=snapshot_id,
        ).single()
        if not node_count or node_count["nodes"] != manifest["node_count"]:
            raise Neo4jOntologyError("Neo4j 本体节点数量校验失败。")
        if (
            not relationship_count
            or relationship_count["relationships"] != manifest["relationship_count"]
        ):
            raise Neo4jOntologyError("Neo4j 本体关系数量校验失败。")
        return {
            "snapshot_id": snapshot_id,
            "status": final_status,
            "node_count": node_count["nodes"],
            "relationship_count": relationship_count["relationships"],
            "layer_counts": manifest["layer_counts"],
        }


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_json(value: str | None, default: Any) -> Any:
    import json

    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "Neo4jOntologyConfig",
    "Neo4jOntologyError",
    "Neo4jOntologyStore",
]
