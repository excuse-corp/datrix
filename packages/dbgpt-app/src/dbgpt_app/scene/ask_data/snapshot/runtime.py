"""Compatibility wrappers for the Scene Snapshot runtime contract.

New code should import from ``ask_data.query.capabilities`` directly.  This
module remains only so older call sites and external imports continue to work
during the Snapshot V2 migration.
"""

from __future__ import annotations

from ..query.capabilities import (
    metric_definition,
    metric_keys,
    named_filters,
    query_dimensions,
    query_limits,
    query_metrics,
    query_model,
    query_time,
    runtime_dict,
    schema_config,
    semantic_keys,
)

__all__ = [
    "metric_definition",
    "metric_keys",
    "named_filters",
    "query_dimensions",
    "query_limits",
    "query_metrics",
    "query_model",
    "query_time",
    "runtime_dict",
    "schema_config",
    "semantic_keys",
]
