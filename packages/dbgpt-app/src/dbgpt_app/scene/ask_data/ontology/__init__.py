"""Global business Ontology for main-agent planning and analysis."""

from .markdown import (
    DEFAULT_ONTOLOGY_MARKDOWN,
    LEGACY_DEFAULT_ONTOLOGY_MARKDOWN,
    OntologyMarkdownError,
    OntologyMarkdownParser,
    is_legacy_default_ontology,
)
from .service import (
    OntologyConflictError,
    OntologyLifecycleService,
    OntologyServiceError,
)

__all__ = [
    "DEFAULT_ONTOLOGY_MARKDOWN",
    "LEGACY_DEFAULT_ONTOLOGY_MARKDOWN",
    "OntologyConflictError",
    "OntologyLifecycleService",
    "OntologyMarkdownError",
    "OntologyMarkdownParser",
    "is_legacy_default_ontology",
    "OntologyServiceError",
]
