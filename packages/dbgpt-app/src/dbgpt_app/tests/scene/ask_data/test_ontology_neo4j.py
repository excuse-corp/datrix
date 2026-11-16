from types import SimpleNamespace

from dbgpt_app.scene.ask_data.ontology.graph_model import normalize_ontology_graph
from dbgpt_app.scene.ask_data.ontology.neo4j_store import (
    Neo4jOntologyConfig,
    Neo4jOntologyStore,
)


def test_normalize_ontology_graph_materializes_three_layers_and_bindings():
    manifest = normalize_ontology_graph(
        {
            "nodes": [
                {"id": "project", "type": "entity", "name": "项目"},
                {
                    "id": "paid_amount",
                    "type": "metric",
                    "name": "已支付金额",
                    "data": {
                        "source_metric_refs": [
                            {
                                "scene_id": "scene_project",
                                "metric_id": "paid_amount_local",
                                "semantic_key": "paid_amount",
                                "section": "指标定义",
                                "evidence": "语义文档中明确声明已支付金额。",
                            }
                        ]
                    },
                },
                {
                    "id": "rule_1",
                    "type": "analysis_rule",
                    "name": "金额解释规则",
                },
            ],
            "edges": [
                {
                    "id": "binding_1",
                    "source": "scene_project",
                    "target": "paid_amount",
                    "type": "scene_binding",
                    "name": "场景绑定",
                    "data": {"semantic_key": "paid_amount"},
                },
                {
                    "id": "rule_metric",
                    "source": "rule_1",
                    "target": "paid_amount",
                    "type": "applies_to_metric",
                    "name": "适用指标",
                },
            ],
        },
        snapshot_id="onto_snapshot",
        ontology_id="global_business",
        revision=3,
        source_scene_snapshots=[
            {
                "scene_id": "scene_project",
                "snapshot_id": "scene_snapshot_1",
                "revision_id": "2",
                "semantic_md_hash": "sha256:semantic",
            }
        ],
    )

    # The source snapshot makes the missing scene explicit, then adds a
    # ResultBinding object without manufacturing a new metric.
    object_types = {item["properties"]["object_type"] for item in manifest["nodes"]}
    assert object_types >= {
        "entity",
        "metric",
        "analysis_rule",
        "scene",
        "scene_snapshot",
        "result_binding",
        "evidence",
        "source_document",
    }
    layers = {
        item["properties"]["object_type"]: item["layer"] for item in manifest["nodes"]
    }
    assert layers["metric"] == "business_semantics"
    assert layers["analysis_rule"] == "analysis_explanations"
    assert layers["result_binding"] == "mapping_governance"
    assert layers["evidence"] == "mapping_governance"
    assert layers["source_document"] == "mapping_governance"
    assert (
        sum(item["properties"]["object_type"] == "metric" for item in manifest["nodes"])
        == 1
    )
    assert (
        next(
            item
            for item in manifest["nodes"]
            if item["properties"]["object_type"] == "result_binding"
        )["properties"]["semantic_key"]
        == "paid_amount"
    )
    relation_types = {
        item["properties"]["relation_type"] for item in manifest["relationships"]
    }
    assert {
        "scene_binding",
        "has_snapshot",
        "declares_binding",
        "maps_to",
        "supported_by",
        "from_document",
    } <= relation_types
    assert manifest["layer_counts"]["mapping_governance"]["nodes"] >= 3


def test_normalize_ontology_graph_rejects_dangling_relation():
    try:
        normalize_ontology_graph(
            {
                "nodes": [{"id": "metric", "type": "metric", "name": "指标"}],
                "edges": [
                    {
                        "id": "bad",
                        "source": "missing",
                        "target": "metric",
                        "type": "relation",
                    }
                ],
            },
            snapshot_id="snapshot",
            ontology_id="global",
            revision=1,
        )
    except ValueError as exc:
        assert "不存在" in str(exc)
    else:
        raise AssertionError("dangling relation should be rejected")


def test_neo4j_store_is_disabled_by_default_configuration():
    config = Neo4jOntologyConfig(
        enabled=False,
        uri="bolt://127.0.0.1:7670",
        username="neo4j",
        password="",
        database="neo4j",
    )
    store = Neo4jOntologyStore(config)
    assert store.health() == {"enabled": False, "status": "disabled"}
    result = store.sync_snapshot(
        SimpleNamespace(
            snapshot_id="snapshot", graph_json={}, ontology_id="global", revision=1
        )
    )
    assert result["status"] == "skipped"
