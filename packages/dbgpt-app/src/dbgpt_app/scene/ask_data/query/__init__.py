"""Safe semantic query pipeline."""

from .ast_validator import QueryAstValidator, QuerySafetyError
from .compiler import CompiledQuery, QuerySpecCompiler
from .executor import SafeSceneQueryExecutor
from .query_spec import (
    FieldLineage,
    OrderBy,
    QueryFilter,
    QuerySpec,
    SceneResult,
    TimeRange,
    build_field_ref,
)
from .repair import QuerySpecRepair, QuerySpecRepairError
from .validator import QuerySpecIssue, QuerySpecValidationError, QuerySpecValidator

__all__ = [
    "CompiledQuery",
    "FieldLineage",
    "OrderBy",
    "QueryAstValidator",
    "QueryFilter",
    "QuerySafetyError",
    "QuerySpec",
    "QuerySpecCompiler",
    "QuerySpecIssue",
    "QuerySpecValidationError",
    "QuerySpecValidator",
    "SafeSceneQueryExecutor",
    "SceneResult",
    "TimeRange",
    "build_field_ref",
    "QuerySpecRepair",
    "QuerySpecRepairError",
]
