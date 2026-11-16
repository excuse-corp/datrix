"""Idempotent Neo4j schema for AskData Ontology snapshots."""

from __future__ import annotations

from typing import Any

_SCHEMA_STATEMENTS = (
    """
    CREATE CONSTRAINT ontology_snapshot_id IF NOT EXISTS
    FOR (n:OntologySnapshot) REQUIRE n.snapshot_id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT ontology_object_identity IF NOT EXISTS
    FOR (n:OntologyObject) REQUIRE (n.snapshot_id, n.object_id) IS UNIQUE
    """,
    """
    CREATE INDEX ontology_object_lookup IF NOT EXISTS
    FOR (n:OntologyObject) ON (n.snapshot_id, n.object_type, n.name)
    """,
    """
    CREATE INDEX ontology_object_layer IF NOT EXISTS
    FOR (n:OntologyObject) ON (n.snapshot_id, n.layer)
    """,
    """
    CREATE INDEX ontology_binding_key IF NOT EXISTS
    FOR (n:OntologyObject)
    ON (n.snapshot_id, n.object_type, n.scene_id, n.semantic_key)
    """,
    """
    CREATE INDEX ontology_scene_snapshot IF NOT EXISTS
    FOR (n:OntologyObject)
    ON (n.snapshot_id, n.object_type, n.scene_id, n.scene_snapshot_id)
    """,
)


def ensure_ontology_schema(driver: Any, database: str) -> None:
    """Apply all schema statements once per driver/session.

    Neo4j's ``IF NOT EXISTS`` makes this safe for service restarts and for
    concurrent application instances.  Schema failures are intentionally
    propagated to the caller because writing an unindexed graph can produce
    an unsafe partial deployment.
    """
    with driver.session(database=database) as session:
        for statement in _SCHEMA_STATEMENTS:
            session.run(statement).consume()


__all__ = ["ensure_ontology_schema"]
