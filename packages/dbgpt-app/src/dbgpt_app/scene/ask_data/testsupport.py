"""Small protocol fixtures shared by AskData tests."""

from __future__ import annotations

from datetime import datetime, timezone

from .schemas.snapshot import Snapshot, SnapshotSourceHashes, SnapshotStatus


def build_test_snapshot(scene_id: str, revision_id: str) -> Snapshot:
    return Snapshot(
        snapshot_id=f"snap_{scene_id}_{revision_id}",
        scene_id=scene_id,
        revision_id=revision_id,
        status=SnapshotStatus.READY,
        content_hash="sha256:content",
        source_hashes=SnapshotSourceHashes(
            semantic_hash="sha256:semantic",
            schema_hash="sha256:schema",
            knowledge_hash="sha256:knowledge",
        ),
        routing_projection={"scene_id": scene_id},
        runtime_config={
            "data_source": "ecology",
            "view": "dbo.vw_contracts",
            "query_model": {
                "dimensions": [],
                "metrics": [
                    {"key": "contract_amount", "aggregation": "sum", "field": "amount"}
                ],
                "time": {"required": False, "granularities": []},
            },
            "named_filters": [],
            "query_limits": {"max_rows": 2},
        },
        created_at=datetime.now(timezone.utc),
    )
