"""Agent Snapshot construction and lifecycle services."""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "InMemorySnapshotService": ".service",
    "SqlSnapshotService": ".service",
    "RegistryLookupError": ".registry",
    "SceneAgentRegistry": ".registry",
    "SnapshotBuildError": ".builder",
    "SnapshotBuilder": ".builder",
    "SnapshotStateError": ".service",
}

__all__ = [
    "InMemorySnapshotService",
    "SqlSnapshotService",
    "RegistryLookupError",
    "SceneAgentRegistry",
    "SnapshotBuildError",
    "SnapshotBuilder",
    "SnapshotStateError",
]


def __getattr__(name: str):
    try:
        module_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
