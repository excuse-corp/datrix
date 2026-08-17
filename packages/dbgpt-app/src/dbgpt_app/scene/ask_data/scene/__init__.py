"""Scene configuration services."""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "SceneConfigValidator": ".validator",
    "SceneSchemaInspector": ".schema_inspector",
    "SceneLifecycleService": ".service",
    "ScenePublishError": ".publish_service",
    "ScenePublishService": ".publish_service",
    "SemanticDraftBuilder": ".draft_builder",
    "SemanticMarkdownParser": ".markdown",
    "ValidationResult": ".validator",
}

__all__ = [
    "SceneConfigValidator",
    "SceneSchemaInspector",
    "SceneLifecycleService",
    "ScenePublishError",
    "ScenePublishService",
    "SemanticDraftBuilder",
    "SemanticMarkdownParser",
    "ValidationResult",
]


def __getattr__(name: str):
    try:
        module_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
