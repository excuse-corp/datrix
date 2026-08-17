from __future__ import annotations

from types import SimpleNamespace

import pytest

from dbgpt_app.scene.ask_data.rag import (
    KnowledgeManagerError,
    KnowledgeServiceBackend,
    SceneKnowledgeManager,
)


class FakeKnowledgeService:
    def __init__(self):
        self.spaces: set[str] = set()
        self.documents: dict[str, dict[str, str]] = {}

    def get_knowledge_space(self, request):
        return (
            [SimpleNamespace(name=request.name)] if request.name in self.spaces else []
        )

    def create_knowledge_space(self, request):
        self.spaces.add(request.name)
        self.documents.setdefault(request.name, {})
        return 1

    def get_knowledge_documents(self, space, request):
        data = [
            {"doc_name": name, "content": content}
            for name, content in self.documents.get(space, {}).items()
        ]
        return SimpleNamespace(data=data)

    def create_knowledge_document(self, space, request):
        self.documents.setdefault(space, {})[request.doc_name] = request.content
        return len(self.documents[space])


def test_knowledge_service_backend_publishes_immutable_documents():
    service = FakeKnowledgeService()
    backend = KnowledgeServiceBackend(service, quality_gate=lambda *_: True)
    manager = SceneKnowledgeManager(backend)

    result = manager.build(
        "contracts",
        3,
        (("semantic.md", "# Contracts"), ("schema.json", "{}")),
    )

    assert result.space_name == "askdata_contracts_r3"
    assert set(service.documents[result.space_name]) == {"semantic.md", "schema.json"}


def test_knowledge_service_backend_requires_quality_callback():
    backend = KnowledgeServiceBackend(FakeKnowledgeService())
    manager = SceneKnowledgeManager(backend)

    with pytest.raises(KnowledgeManagerError) as error:
        manager.build("contracts", 3, (("semantic.md", "# Contracts"),))

    assert error.value.code == "RAG_QUALITY_GATE_FAILED"


def test_knowledge_service_backend_rejects_unexpected_documents():
    service = FakeKnowledgeService()
    backend = KnowledgeServiceBackend(service, quality_gate=lambda *_: True)
    manager = SceneKnowledgeManager(backend)
    manager.build("contracts", 3, (("semantic.md", "# Contracts"),))
    service.documents["askdata_contracts_r3"]["unexpected.md"] = "bad"

    with pytest.raises(KnowledgeManagerError) as error:
        manager.build("contracts", 3, (("semantic.md", "# Contracts"),))

    assert error.value.code == "KNOWLEDGE_SPACE_IMMUTABLE"


def test_in_memory_knowledge_retrieval_returns_opaque_references():
    manager = SceneKnowledgeManager()
    result = manager.build(
        "contracts",
        1,
        (("semantic.md", "合同金额按签约状态统计"),),
    )
    assert manager.retrieve(result.space_name, "合同金额") == [
        f"{result.space_name}:semantic.md"
    ]
