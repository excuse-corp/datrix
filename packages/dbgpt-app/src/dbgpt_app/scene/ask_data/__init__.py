"""Controlled ask-data building blocks.

The package intentionally contains protocol and validation primitives first.  HTTP
registration and orchestration are layered on top of these modules in later phases.
"""

from .query_service import SceneQueryService
from .scene.markdown import SemanticMarkdownParser
from .schemas.semantic import SemanticMarkdownError

__all__ = ["SceneQueryService", "SemanticMarkdownError", "SemanticMarkdownParser"]
