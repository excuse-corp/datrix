from fastapi import FastAPI
from fastapi.testclient import TestClient

from dbgpt_app.scene.ask_data.api import ontology as ontology_api
from dbgpt_app.scene.ask_data.ontology.repository import InMemoryOntologyRepository
from dbgpt_app.scene.ask_data.ontology.service import OntologyLifecycleService
from dbgpt_app.scene.ask_data.security import AskDataPrincipal


def test_ontology_document_preview_and_publish_api():
    app = FastAPI()
    app.include_router(ontology_api.router)
    service = OntologyLifecycleService(InMemoryOntologyRepository())
    previous_service = ontology_api.get_ontology_service()
    ontology_api.configure_ontology_service(service)
    app.dependency_overrides[ontology_api.get_principal] = lambda: AskDataPrincipal(
        user_id="admin", roles=frozenset({"admin"})
    )
    try:
        client = TestClient(app)
        draft = client.get("/api/v1/ask-data/ontology/draft")
        assert draft.status_code == 200
        revision = draft.json()["data"]["revision"]
        markdown = draft.json()["data"]["markdown"]

        preview = client.post(
            "/api/v1/ask-data/ontology/compile-preview",
            json={"markdown": markdown},
        )
        assert preview.status_code == 200
        assert preview.json()["data"]["valid"]

        saved = client.put(
            "/api/v1/ask-data/ontology/draft",
            json={"markdown": markdown, "expected_revision": revision},
        )
        assert saved.status_code == 200

        built = client.post(
            f"/api/v1/ask-data/ontology/revisions/{revision}/build-snapshot"
        )
        assert built.status_code == 200
        snapshot_id = built.json()["data"]["snapshot_id"]

        activated = client.post(
            f"/api/v1/ask-data/ontology/snapshots/{snapshot_id}/activate"
        )
        assert activated.status_code == 200
        assert activated.json()["data"]["status"] == "active"
    finally:
        ontology_api.configure_ontology_service(previous_service)
