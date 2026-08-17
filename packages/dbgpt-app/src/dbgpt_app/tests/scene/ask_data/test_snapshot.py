from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dbgpt_app.scene.ask_data.schemas.schema import ViewColumn, ViewSchema
from dbgpt_app.scene.ask_data.schemas.snapshot import SnapshotStatus
from dbgpt_app.scene.ask_data.snapshot import (
    InMemorySnapshotService,
    RegistryLookupError,
    SceneAgentRegistry,
    SnapshotBuilder,
)
from dbgpt_app.scene.ask_data.query.capabilities import query_dimensions, query_metrics

DOCUMENT = """---
schema_version: "1"
scene_id: contracts
name: Contract analysis
description: Contract metrics
keywords: [contracts]
data_source: ecology
view: dbo.vw_contracts
agent:
  capabilities: [query contracts]
  cannot_do: [predict]
time:
  field: signed_date
  required: true
dimensions:
  - key: department
    field: department_id
    filter_operators: [eq]
metrics:
  - key: contract_amount
    field: contract_amount
    aggregation: sum
guidance:
  typical_questions: [monthly contract amount]
---

# Contract analysis
"""

PURE_SEMANTIC_DOCUMENT = """---
schema_version: "1"
scene_id: contracts
name: Contract analysis
description: Contract business semantics
data_source: ecology
view: dbo.vw_contracts
---

<!-- dataman:document=data-dictionary -->
# 视图数据字典

| 字段 | 含义 |
| --- | --- |
| contract_amount | 合同金额 |

<!-- dataman:document=business-semantics -->
# 业务语义说明

- 一行表示一份合同。
- 金额为空时不可按零处理。
"""


def _schema(hash_value: str = "sha256:schema") -> ViewSchema:
    return ViewSchema(
        dialect="mssql",
        data_source="ecology",
        view="dbo.vw_contracts",
        columns=[
            ViewColumn(name="department_id", data_type="int", normalized_type="number"),
            ViewColumn(
                name="contract_amount", data_type="decimal", normalized_type="number"
            ),
            ViewColumn(name="signed_date", data_type="date", normalized_type="date"),
        ],
        schema_hash=hash_value,
        inspected_at=datetime.now(timezone.utc),
    )


def _build(revision_id: str = "rev-1", knowledge_hash: str = "sha256:knowledge"):
    return SnapshotBuilder().build(
        scene_id="contracts",
        revision_id=revision_id,
        markdown=DOCUMENT,
        view_schema=_schema(),
        knowledge_hash=knowledge_hash,
    )


def test_snapshot_build_is_deterministic_and_redacts_physical_fields_from_routing():
    first = _build()
    second = _build()

    assert first.snapshot_id == second.snapshot_id
    assert first.content_hash == second.content_hash
    assert "field" not in str(first.routing_projection)
    assert "metrics" not in first.routing_projection
    assert "dimensions" not in first.routing_projection
    assert first.schema_version == "2"
    assert first.runtime_config["schema_version"] == "2"
    assert first.runtime_config["documents"]["semantic_md"] == DOCUMENT
    assert first.runtime_config["documents"]["semantic_md_hash"] == (
        first.source_hashes.semantic_hash
    )
    assert query_metrics(first)[0]["field"] == "contract_amount"
    for legacy_key in (
        "query_model",
        "dimensions",
        "metrics",
        "time",
        "grain",
        "common_keys",
        "derived_metrics",
    ):
        assert legacy_key not in first.runtime_config


def test_snapshot_keeps_both_full_scene_documents_in_runtime_context():
    document = DOCUMENT.replace(
        "# Contract analysis\n",
        """<!-- dataman:document=data-dictionary -->
# 视图数据字典

| field | meaning |
| --- | --- |
| contract_amount | signed contract amount |

<!-- dataman:document=business-semantics -->
# 业务语义说明

Contract amount excludes tax.
""",
    )

    snapshot = SnapshotBuilder().build(
        scene_id="contracts",
        revision_id="rev-documents",
        markdown=document,
        view_schema=_schema(),
    )

    assert (
        "contract_amount" in snapshot.runtime_config["documents"]["data_dictionary_md"]
    )
    assert snapshot.runtime_config["documents"]["business_semantics_md"] == (
        "Contract amount excludes tax."
    )


def test_snapshot_derives_query_capabilities_from_schema_without_structured_yaml():
    snapshot = SnapshotBuilder().build(
        scene_id="contracts",
        revision_id="rev-pure-semantics",
        markdown=PURE_SEMANTIC_DOCUMENT,
        view_schema=_schema(),
    )

    assert {item["key"] for item in query_dimensions(snapshot)} >= {
        "department_id",
        "contract_amount",
        "signed_date",
    }
    assert {item["key"] for item in query_metrics(snapshot)} >= {
        "count_rows",
        "sum_contract_amount",
        "count_distinct_contract_amount",
    }
    assert snapshot.runtime_config["documents"]["business_semantics_md"].startswith(
        "- 一行表示一份合同。"
    )


def test_legacy_markdown_body_remains_business_semantic_document():
    snapshot = _build()

    assert snapshot.runtime_config["documents"]["data_dictionary_md"] == ""
    assert snapshot.runtime_config["documents"]["business_semantics_md"] == (
        "# Contract analysis"
    )


def test_snapshot_activation_supersedes_previous_version():
    service = InMemorySnapshotService()
    first = service.save_ready(_build("rev-1"))
    active_first = service.activate(first.snapshot_id)
    second = service.save_ready(_build("rev-2"))

    active_second = service.activate(second.snapshot_id)

    assert active_first.status == SnapshotStatus.ACTIVE
    assert active_second.status == SnapshotStatus.ACTIVE
    assert service.get(first.snapshot_id).status == SnapshotStatus.SUPERSEDED
    assert service.active("contracts").snapshot_id == second.snapshot_id


def test_active_snapshot_activation_is_idempotent_and_drift_fails_closed():
    service = InMemorySnapshotService()
    snapshot = service.save_ready(_build())
    active = service.activate(snapshot.snapshot_id)
    registry_version = service.registry_version

    assert service.activate(snapshot.snapshot_id) == active
    assert service.registry_version == registry_version

    assert service.check_schema_drift("contracts", "sha256:changed")
    assert service.get(snapshot.snapshot_id).status == SnapshotStatus.INVALID
    assert service.active("contracts") is None


def test_registry_refreshes_and_rejects_ineligible_scenes():
    service = InMemorySnapshotService()
    snapshot = service.save_ready(_build())
    registry = SceneAgentRegistry(service)

    with pytest.raises(RegistryLookupError):
        registry.get("contracts")

    service.activate(snapshot.snapshot_id)
    assert registry.get("contracts").snapshot_id == snapshot.snapshot_id
    assert registry.version == service.registry_version
