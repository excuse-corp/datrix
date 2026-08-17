"""Versioned knowledge-space publishing boundary for AskData Scenes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, Sequence


class KnowledgeManagerError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class KnowledgeBackend(Protocol):
    def publish(self, space_name: str, documents: Sequence[tuple[str, str]]) -> None:
        """Publish immutable documents into a versioned space."""

    def quality_gate(
        self, space_name: str, documents: Sequence[tuple[str, str]]
    ) -> bool:
        """Return whether fixed retrieval checks pass."""

    def retrieve(self, space_name: str, question: str, limit: int = 5) -> list[str]:
        """Return opaque reference IDs, never document contents."""


@dataclass(frozen=True)
class KnowledgeBuildResult:
    space_name: str
    knowledge_hash: str
    quality_gate_passed: bool
    document_names: tuple[str, ...]


class InMemoryKnowledgeBackend:
    """Deterministic backend used by tests and local MVP execution."""

    def __init__(self, *, quality_gate_passed: bool = True):
        self.quality_gate_passed = quality_gate_passed
        self.spaces: dict[str, tuple[tuple[str, str], ...]] = {}
        self.quality_results: dict[str, bool] = {}

    def publish(self, space_name: str, documents: Sequence[tuple[str, str]]) -> None:
        self.spaces[space_name] = tuple(documents)

    def quality_gate(
        self, space_name: str, documents: Sequence[tuple[str, str]]
    ) -> bool:
        self.quality_results[space_name] = self.quality_gate_passed
        return self.quality_gate_passed

    def retrieve(self, space_name: str, question: str, limit: int = 5) -> list[str]:
        documents = self.spaces.get(space_name, ())
        tokens = {token.lower() for token in question.split() if token}
        scored = []
        for name, content in documents:
            score = sum(token in content.lower() for token in tokens)
            if score:
                scored.append((score, name))
        return [
            f"{space_name}:{name}" for _, name in sorted(scored, reverse=True)[:limit]
        ]


class KnowledgeServiceBackend:
    """Adapter for DB-GPT's KnowledgeService document APIs.

    The adapter deliberately does not bypass the KnowledgeService lifecycle.
    A separate quality callback is required before a Snapshot can be built.
    """

    def __init__(
        self,
        service: object,
        *,
        quality_gate: Callable[[str, Sequence[tuple[str, str]]], bool] | None = None,
    ):
        self.service = service
        self.quality_gate_callback = quality_gate
        self._hashes: dict[str, str] = {}
        self._document_names: dict[str, tuple[str, ...]] = {}
        self._quality_results: dict[str, bool] = {}

    def publish(self, space_name: str, documents: Sequence[tuple[str, str]]) -> None:
        from dbgpt_app.knowledge.request.request import (
            KnowledgeDocumentRequest,
            KnowledgeSpaceRequest,
        )

        spaces = self.service.get_knowledge_space(
            KnowledgeSpaceRequest(name=space_name)
        )
        if not spaces:
            self.service.create_knowledge_space(
                KnowledgeSpaceRequest(name=space_name, desc="AskData immutable Scene")
            )
            spaces = self.service.get_knowledge_space(
                KnowledgeSpaceRequest(name=space_name)
            )
        if len(spaces) != 1:
            raise KnowledgeManagerError(
                "KNOWLEDGE_SPACE_CONFLICT",
                f"Knowledge space is not unique: {space_name}",
            )
        existing = self.service.get_knowledge_documents(
            space_name,
            self._document_query_request(),
        )
        existing_names = {
            self._document_name(item)
            for item in getattr(existing, "data", [])
            if self._document_name(item) is not None
        }
        missing = [
            (name, content) for name, content in documents if name not in existing_names
        ]
        unexpected = existing_names - {name for name, _ in documents}
        if unexpected:
            raise KnowledgeManagerError(
                "KNOWLEDGE_SPACE_IMMUTABLE",
                f"Knowledge space already contains unrelated documents: {space_name}",
            )
        for name, content in missing:
            self.service.create_knowledge_document(
                space_name,
                KnowledgeDocumentRequest(
                    doc_name=name,
                    doc_type="text",
                    content=content,
                    source=f"ask-data:{space_name}",
                ),
            )
        self._hashes[space_name] = SceneKnowledgeManager._hash(documents)
        self._document_names[space_name] = tuple(name for name, _ in documents)

    def verify(self, space_name: str, expected_hash: str) -> bool:
        return self._hashes.get(space_name) == expected_hash

    @staticmethod
    def _document_name(item: object) -> str | None:
        if isinstance(item, dict):
            value = item.get("doc_name") or item.get("name")
        else:
            value = getattr(item, "doc_name", None) or getattr(item, "name", None)
        return str(value) if value else None

    def quality_gate(
        self, space_name: str, documents: Sequence[tuple[str, str]]
    ) -> bool:
        if self.quality_gate_callback is None:
            return False
        passed = bool(self.quality_gate_callback(space_name, documents))
        self._quality_results[space_name] = passed
        return passed

    def retrieve(self, space_name: str, question: str, limit: int = 5) -> list[str]:
        query = getattr(self.service, "similarity_query", None) or getattr(
            self.service, "similarity_search", None
        )
        if query is None:
            from dbgpt_app.knowledge.api import similarity_query
            from dbgpt_app.knowledge.request.request import KnowledgeQueryRequest

            response = similarity_query(
                space_name,
                KnowledgeQueryRequest(query=question, space=space_name, top_k=limit),
            )
        else:
            response = query(space_name, question, top_k=limit)
        items = getattr(response, "data", response) or []
        if isinstance(items, dict):
            items = items.get("response", items.get("data", [])) or []
        references = []
        for item in items[:limit]:
            if isinstance(item, dict):
                reference = item.get("chunk_id") or item.get("doc_id") or item.get("id")
                reference = reference or item.get("source")
            else:
                reference = (
                    getattr(item, "chunk_id", None)
                    or getattr(item, "doc_id", None)
                    or getattr(item, "id", None)
                    or getattr(item, "source", None)
                )
            if reference:
                references.append(str(reference))
        return references

    @staticmethod
    def _document_query_request():
        from dbgpt_app.knowledge.request.request import DocumentQueryRequest

        return DocumentQueryRequest(page=1, page_size=100)


class SceneKnowledgeManager:
    def __init__(self, backend: KnowledgeBackend | None = None):
        self.backend = backend or InMemoryKnowledgeBackend()

    def build(
        self,
        scene_id: str,
        revision: int,
        documents: Sequence[tuple[str, str]],
        *,
        run_quality_gate: bool = True,
        force_rebuild: bool = False,
    ) -> KnowledgeBuildResult:
        if not documents:
            raise KnowledgeManagerError(
                "KNOWLEDGE_DOCUMENTS_EMPTY",
                "At least one knowledge document is required",
            )
        normalized = tuple(sorted((name, content) for name, content in documents))
        knowledge_hash = self._hash(normalized)
        space_name = f"askdata_{scene_id}_r{revision}"
        if force_rebuild or space_name not in getattr(self.backend, "spaces", {}):
            self.backend.publish(space_name, normalized)
        quality_gate_passed = self.backend.quality_gate(space_name, normalized)
        if run_quality_gate and not quality_gate_passed:
            raise KnowledgeManagerError(
                "RAG_QUALITY_GATE_FAILED",
                f"Knowledge quality gate failed for {space_name}",
            )
        return KnowledgeBuildResult(
            space_name=space_name,
            knowledge_hash=knowledge_hash,
            quality_gate_passed=quality_gate_passed,
            document_names=tuple(name for name, _ in normalized),
        )

    def verify(self, space_name: str, expected_hash: str) -> bool:
        verifier = getattr(self.backend, "verify", None)
        if verifier is not None:
            return bool(verifier(space_name, expected_hash))
        documents = getattr(self.backend, "spaces", {}).get(space_name)
        if documents is None:
            return False
        return self._hash(documents) == expected_hash

    def retrieve(self, space_name: str, question: str, limit: int = 5) -> list[str]:
        return self.backend.retrieve(space_name, question, limit)

    def status(self, space_name: str | None) -> dict[str, object]:
        if not space_name:
            return {
                "document_count": 0,
                "chunk_count": 0,
                "quality_status": "pending",
                "pass_rate": None,
            }
        documents = getattr(self.backend, "spaces", {}).get(space_name)
        names = getattr(self.backend, "_document_names", {}).get(space_name)
        if documents is not None:
            document_count = len(documents)
        elif names is not None:
            document_count = len(names)
        else:
            document_count = 0
        quality_results = getattr(self.backend, "quality_results", {})
        quality_results = quality_results or getattr(
            self.backend, "_quality_results", {}
        )
        quality = quality_results.get(space_name)
        return {
            "document_count": document_count,
            "chunk_count": None,
            "quality_status": (
                "passed"
                if quality is True
                else "failed"
                if quality is False
                else "pending"
            ),
            "pass_rate": 1.0 if quality is True else 0.0 if quality is False else None,
        }

    @staticmethod
    def _hash(documents: Sequence[tuple[str, str]]) -> str:
        payload = json.dumps(
            [{"name": name, "content": content} for name, content in documents],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


__all__ = [
    "InMemoryKnowledgeBackend",
    "KnowledgeServiceBackend",
    "KnowledgeBuildResult",
    "KnowledgeManagerError",
    "SceneKnowledgeManager",
]
