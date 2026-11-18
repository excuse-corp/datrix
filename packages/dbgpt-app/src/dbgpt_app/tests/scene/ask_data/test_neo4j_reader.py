from __future__ import annotations

import logging

from dbgpt_app.scene.ask_data.ontology.neo4j_reader import Neo4jOntologyReader
from dbgpt_app.scene.ask_data.ontology.neo4j_store import (
    Neo4jOntologyConfig,
    Neo4jOntologyStore,
)
from dbgpt_app.scene.ask_data.ontology.repository import InMemoryOntologyRepository
from dbgpt_app.scene.ask_data.ontology.service import OntologyLifecycleService
from dbgpt_app.scene.ask_data.ontology.schemas import OntologySnapshot


class _FakeSession:
    def __init__(self):
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self, query, **parameters):
        self.queries.append((query, parameters))
        if "RETURN s.snapshot_id AS snapshot_id" in query:
            return _FakeResult(
                [
                    {
                        "snapshot_id": "onto_active",
                        "ontology_id": "global_business",
                        "revision": 4,
                        "status": "active",
                        "content_hash": "sha256:graph",
                        "compiler_version": "2",
                        "source_scene_snapshots_json": "[]",
                    }
                ]
            )
        if "properties(binding) AS binding" in query:
            return _FakeResult(
                [
                    {
                        "scene": {
                            "object_id": "finance",
                            "object_type": "scene",
                            "name": "财务",
                            "data_json": "{}",
                            "provenance_json": "[]",
                        },
                        "metric": {
                            "object_id": "finance.payment_amount",
                            "object_type": "metric",
                            "name": "支付金额",
                            "data_json": '{"semantic_key":"finance.payment_amount"}',
                            "provenance_json": "[]",
                        },
                        "binding": {
                            "object_id": "finance__result_binding_payment_amount",
                            "object_type": "result_binding",
                            "semantic_key": "finance.payment_amount",
                            "scene_snapshot_id": "snap_finance",
                            "field_ref": "finance@snap_finance:paid_amount",
                            "data_json": '{"semantic_key":"finance.payment_amount"}',
                            "provenance_json": "[]",
                        },
                    }
                ]
            )
        return _FakeResult([])


class _FakeResult(list):
    def single(self):
        return self[0] if self else None


class _FakeDriver:
    def __init__(self):
        self.session_instance = _FakeSession()

    def session(self, **_kwargs):
        return self.session_instance


def test_neo4j_reader_returns_result_scoped_snapshot_subgraph():
    store = Neo4jOntologyStore(
        Neo4jOntologyConfig(
            enabled=True,
            uri="bolt://unused",
            username="neo4j",
            password="test",
            database="neo4j",
        )
    )
    driver = _FakeDriver()
    store._get_driver = lambda: driver  # type: ignore[method-assign]

    snapshot = Neo4jOntologyReader(store).read_snapshot(
        "onto_active",
        scene_ids=["finance"],
        scene_snapshot_ids=["snap_finance"],
        semantic_keys=["finance.payment_amount"],
        field_refs=["finance@snap_finance:paid_amount"],
    )

    assert snapshot.snapshot_id == "onto_active"
    assert [item["id"] for item in snapshot.graph_json["nodes"]] == [
        "finance",
        "finance.payment_amount",
    ]
    assert snapshot.graph_json["edges"][0]["data"]["semantic_key"] == (
        "finance.payment_amount"
    )
    match_query = next(
        (query, params)
        for query, params in driver.session_instance.queries
        if "properties(binding) AS binding" in query
    )
    assert match_query[1]["snapshot_id"] == "onto_active"
    assert match_query[1]["scene_snapshot_ids"] == ["snap_finance"]
    assert "coalesce(binding.status, 'confirmed') = 'confirmed'" in match_query[0]
    assert all("MATCH (n:OntologyObject {snapshot_id: $snapshot_id})" not in query for query, _ in driver.session_instance.queries)


def test_non_admin_without_authorized_result_scene_skips_neo4j_read():
    store = Neo4jOntologyStore(
        Neo4jOntologyConfig(
            enabled=True,
            uri="bolt://unused",
            username="neo4j",
            password="test",
            database="neo4j",
        )
    )
    service = OntologyLifecycleService(InMemoryOntologyRepository(), neo4j_store=store)
    service.neo4j_reader.active_snapshot_id = lambda: "onto_active"  # type: ignore[method-assign]

    def fail_if_read(*_args, **_kwargs):
        raise AssertionError("unauthorized scope must not read the graph")

    service.neo4j_reader.read_snapshot = fail_if_read  # type: ignore[method-assign]
    snapshot, source = service.analysis_snapshot_with_source(
        scene_ids=["finance"],
        result_columns=[{"key": "payment_amount", "type": "metric"}],
        authorized_scene_ids=["projects"],
        is_platform_admin=False,
    )

    assert snapshot is None
    assert source == "no_authorized_scene"


def test_dual_read_shadow_comparison_logs_counts_without_business_keys(caplog):
    snapshot = OntologySnapshot(
        snapshot_id="onto_shadow",
        ontology_id="global_business",
        revision=4,
        status="active",
        content_hash="sha256:shadow",
        graph_json={
            "nodes": [
                {"id": "finance", "type": "scene", "name": "财务"},
            ],
            "edges": [],
        },
        prompt_projection_json={"nodes": [], "edges": [], "metrics": []},
    )
    store = Neo4jOntologyStore(
        Neo4jOntologyConfig(
            enabled=True,
            uri="bolt://unused",
            username="neo4j",
            password="test",
            database="neo4j",
        )
    )
    repository = InMemoryOntologyRepository()
    repository.save_snapshot(snapshot)
    service = OntologyLifecycleService(repository, neo4j_store=store)
    neo4j_snapshot = snapshot.model_copy(
        update={
            "graph_json": {
                "nodes": [
                    *snapshot.graph_json["nodes"],
                    {
                        "id": "finance.sensitive_metric",
                        "type": "metric",
                        "name": "敏感指标名称",
                            "source_scenes": ["finance"],
                    },
                ],
                "edges": [],
            }
        }
    )

    with caplog.at_level(logging.INFO):
        service._log_analysis_snapshot_comparison(
            neo4j_snapshot,
            snapshot_id="onto_shadow",
            scene_ids=["finance"],
            result_columns=[
                {
                    "key": "finance.sensitive_metric",
                    "semantic_key": "finance.sensitive_metric",
                    "type": "metric",
                    "scene_id": "finance",
                }
            ],
            result_lineage=[],
            result_profiles=[],
            authorized_scene_ids=["finance"],
            is_platform_admin=False,
        )

    assert '"event": "ontology_dual_read_comparison"' in caplog.text
    assert '"equivalent": false' in caplog.text
    assert '"neo4j_only": 1' in caplog.text
    assert "finance.sensitive_metric" not in caplog.text
    assert "敏感指标名称" not in caplog.text
