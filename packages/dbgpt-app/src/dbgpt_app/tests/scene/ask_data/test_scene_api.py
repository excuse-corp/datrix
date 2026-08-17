from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dbgpt_app.scene.ask_data.agents.orchestrator import QueryRunResult
from dbgpt_app.scene.ask_data.api import scenes as ask_data_api
from dbgpt_app.scene.ask_data.api.scenes import router
from dbgpt_app.scene.ask_data.models import InMemorySceneRepository, QueryRun, RunStatus
from dbgpt_app.scene.ask_data.scene import SceneLifecycleService, ScenePublishService
from dbgpt_app.scene.ask_data.schemas.schema import ViewColumn, ViewSchema
from dbgpt_app.scene.ask_data.security import AskDataAuthorizer, AskDataPrincipal
from dbgpt_app.scene.ask_data.snapshot import InMemorySnapshotService


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _schema() -> ViewSchema:
    return ViewSchema(
        dialect="mssql",
        data_source="ecology",
        view="dbo.vw_contracts",
        columns=[
            ViewColumn(name="department_id", data_type="int", normalized_type="number"),
            ViewColumn(name="amount", data_type="decimal", normalized_type="number"),
        ],
        schema_hash="sha256:api-schema",
        inspected_at=datetime.now(timezone.utc),
    )


def _document(scene_id: str, *, name: str = "Contracts") -> str:
    return f"""---
schema_version: "1"
scene_id: {scene_id}
name: {name}
description: Contract analysis
data_source: ecology
view: dbo.vw_contracts
agent:
  capabilities: [query contracts]
  cannot_do: [predict]
dimensions:
  - key: department
    field: department_id
    filter_operators: [eq]
metrics:
  - key: amount
    field: amount
    aggregation: sum
---
<!-- dataman:document=data-dictionary -->
# 数据字典

| 字段 | 含义 |
| --- | --- |
| department_id | 部门 |
| amount | 合同金额 |

<!-- dataman:document=business-semantics -->
# 业务语义说明

一行表示一份合同。
"""


def _client_with_scene_services(schema: ViewSchema | None = None) -> TestClient:
    repository = InMemorySceneRepository()
    snapshot_service = InMemorySnapshotService()
    lifecycle = SceneLifecycleService(repository)
    publish = ScenePublishService(
        repository,
        snapshot_service,
        lambda *_: schema or _schema(),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[ask_data_api.get_scene_service] = lambda: lifecycle
    app.dependency_overrides[ask_data_api.get_publish_service] = lambda: publish
    return TestClient(app)


def test_scene_management_api_lifecycle():
    client = _client_with_scene_services()
    payload = {
        "scene_id": "api_contracts",
        "name": "Contracts",
        "description": "Contract analysis",
        "data_source_name": "ecology",
        "view_name": "dbo.vw_contracts",
        "semantic_md": _document("api_contracts"),
    }

    created = client.post("/api/v1/ask-data/scenes", json=payload)
    assert created.status_code == 200
    assert created.json()["data"]["latest_revision"] == 1
    assert created.json()["data"]["status"] == "active"

    listed = client.get("/api/v1/ask-data/scenes")
    assert listed.status_code == 200
    assert any(
        item["scene_id"] == "api_contracts" for item in listed.json()["data"]["items"]
    )

    active_update = client.put(
        "/api/v1/ask-data/scenes/api_contracts",
        json={key: value for key, value in payload.items() if key != "scene_id"}
        | {"name": "Contracts v2"},
    )
    assert active_update.status_code == 409
    assert active_update.json()["detail"]["code"] == "SCENE_MUST_BE_INACTIVE_TO_EDIT"

    disabled = client.post("/api/v1/ask-data/scenes/api_contracts/disable")
    assert disabled.status_code == 200
    assert disabled.json()["data"]["status"] == "inactive"

    updated = client.put(
        "/api/v1/ask-data/scenes/api_contracts",
        json={key: value for key, value in payload.items() if key != "scene_id"}
        | {"name": "Contracts v2", "semantic_md": _document("api_contracts", name="Contracts v2")},
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2

    deleted = client.delete("/api/v1/ask-data/scenes/api_contracts")
    assert deleted.status_code == 200
    assert client.get("/api/v1/ask-data/scenes/api_contracts").status_code == 404


def test_scene_api_returns_conflict_for_duplicate_scene_id():
    client = _client_with_scene_services()
    payload = {
        "scene_id": "api_duplicate",
        "name": "Contracts",
        "description": "Contract analysis",
        "data_source_name": "ecology",
        "view_name": "dbo.vw_contracts",
        "semantic_md": _document("api_duplicate"),
    }
    assert client.post("/api/v1/ask-data/scenes", json=payload).status_code == 200
    duplicate = client.post("/api/v1/ask-data/scenes", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "SCENE_ID_CONFLICT"


def test_scene_publish_api_validates_enables_and_disables_system_snapshot():
    client = _client_with_scene_services()
    created = client.post(
        "/api/v1/ask-data/scenes",
        json={
            "scene_id": "api_publish",
            "name": "Contracts",
            "description": "Contract analysis",
            "data_source_name": "ecology",
            "view_name": "dbo.vw_contracts",
            "semantic_md": _document("api_publish"),
        },
    )
    assert created.status_code == 200
    assert created.json()["data"]["status"] == "active"
    snapshot_id = created.json()["snapshot"]["snapshot_id"]

    active_snapshot = client.get("/api/v1/ask-data/scenes/api_publish/snapshot")
    assert active_snapshot.status_code == 200
    assert active_snapshot.json()["data"]["snapshot_id"] == snapshot_id

    validated = client.post("/api/v1/ask-data/scenes/api_publish/revisions/1/validate")
    assert validated.status_code == 200
    assert validated.json()["data"]["valid"] is True
    assert validated.json()["data"]["scene_status"] == "active"

    disabled = client.post("/api/v1/ask-data/scenes/api_publish/disable")
    assert disabled.status_code == 200
    assert disabled.json()["data"]["status"] == "inactive"
    assert client.get("/api/v1/ask-data/scenes/api_publish/snapshot").status_code == 404

    enabled = client.post("/api/v1/ask-data/scenes/api_publish/enable")
    assert enabled.status_code == 200
    assert enabled.json()["data"]["status"] == "active"


def test_query_api_persists_contract_and_returns_structured_result():
    class FakeRunService:
        async def execute(self, **kwargs):
            return (
                QueryRun(
                    query_id="qry_api",
                    request_id="req_api",
                    user_id=kwargs["user_id"],
                    question=kwargs["question"],
                    status=RunStatus.REJECTED,
                    errors=[{"code": "OUT_OF_SCOPE", "message": "No access"}],
                ),
                QueryRunResult(
                    status="rejected",
                    errors=[{"code": "OUT_OF_SCOPE", "message": "No access"}],
                ),
            )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[ask_data_api.get_run_service] = lambda: FakeRunService()
    client = TestClient(app)

    response = client.post(
        "/api/v1/ask-data/query",
        headers={"Idempotency-Key": "api-query-1"},
        json={
            "question": "show contracts",
            "user_id": "user-api",
            "plan": {"action": "reject", "reason_code": "OUT_OF_SCOPE"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["data"]["query_id"] == "qry_api"


def test_query_api_rejects_invalid_plan_shape():
    client = _client()
    response = client.post(
        "/api/v1/ask-data/query",
        json={"question": "show contracts", "plan": {"action": "clarify"}},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_PLAN"


def test_config_and_scene_routes_enforce_injected_authorizer(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setattr(ask_data_api, "_authorizer", AskDataAuthorizer())
    client = TestClient(app)

    ordinary = client.get(
        "/api/v1/ask-data/config",
        headers={"X-User-Id": "ordinary"},
    )
    assert ordinary.status_code == 403

    admin = client.get(
        "/api/v1/ask-data/config",
        headers={"X-User-Id": "admin", "X-AskData-Role": "admin"},
    )
    assert admin.status_code == 200
    assert admin.json()["data"]["runtime_secrets_exposed"] is False


def test_main_query_rejects_unauthorized_scene_in_explicit_plan(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setattr(ask_data_api, "_authorizer", AskDataAuthorizer())

    response = TestClient(app).post(
        "/api/v1/ask-data/query",
        headers={"X-User-Id": "ordinary", "X-AskData-Scenes": "allowed"},
        json={
            "question": "show contracts",
            "plan": {
                "action": "execute",
                "combine": {"mode": "separate"},
                "tasks": [
                    {
                        "task_id": "task-1",
                        "scene_id": "forbidden",
                        "question": "show contracts",
                        "metrics": ["amount"],
                    }
                ],
            },
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "SCENE_NOT_FOUND"


def test_ordinary_user_can_read_own_run_but_not_another_users_run(monkeypatch):
    repository = InMemorySceneRepository()

    class FakeRunService:
        def __init__(self):
            self.repository = repository

    monkeypatch.setattr(ask_data_api, "_authorizer", AskDataAuthorizer())
    own = QueryRun(
        query_id="own-query",
        request_id="request-own",
        user_id="ordinary",
        question="own question",
        status=RunStatus.SUCCEEDED,
    )
    other = own.model_copy(
        update={"query_id": "other-query", "user_id": "other"}
    )

    class QueryRepository:
        def get_query(self, query_id):
            return {"own-query": own, "other-query": other}.get(query_id)

        def agents_for_query(self, query_id):
            return []

    service = FakeRunService()
    service.repository = QueryRepository()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[ask_data_api.get_run_service] = lambda: service
    client = TestClient(app)
    headers = {"X-User-Id": "ordinary", "X-AskData-Scenes": "contracts"}

    assert (
        client.get("/api/v1/ask-data/runs/own-query", headers=headers).status_code
        == 200
    )
    hidden = client.get("/api/v1/ask-data/runs/other-query", headers=headers)
    assert hidden.status_code == 404


def test_principal_resolver_takes_precedence_over_integration_headers(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.state.ask_data_principal_resolver = lambda request: AskDataPrincipal(
        user_id="resolved-admin", roles=frozenset({"admin"})
    )
    monkeypatch.setattr(ask_data_api, "_authorizer", AskDataAuthorizer())

    response = TestClient(app).get(
        "/api/v1/ask-data/config",
        headers={"X-User-Id": "ordinary"},
    )

    assert response.status_code == 200


def test_db_gpt_user_request_role_is_adapted(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.state.user = None
    monkeypatch.setattr(ask_data_api, "_authorizer", AskDataAuthorizer())

    class DbGptUser:
        user_id = "db-gpt-admin"
        role = "admin"

    async def middleware(request, call_next):
        request.state.user = DbGptUser()
        return await call_next(request)

    app.middleware("http")(middleware)
    response = TestClient(app).get("/api/v1/ask-data/config")

    assert response.status_code == 200


def test_principal_resolver_enables_default_policy_when_authorizer_omitted(
    monkeypatch,
):
    previous = {
        name: getattr(ask_data_api, name)
        for name in ("_repository", "_service", "_snapshot_service", "_run_service")
    }
    app = FastAPI()
    app.include_router(router)
    app.state.ask_data_principal_resolver = lambda request: AskDataPrincipal(
        user_id="ordinary"
    )
    monkeypatch.setattr(ask_data_api, "_authorizer", None)
    monkeypatch.setattr(ask_data_api, "_principal_resolver", None)
    ask_data_api.configure_ask_data_services(
        repository=InMemorySceneRepository(),
        snapshot_service=InMemorySnapshotService(),
        run_service=object(),
        principal_resolver=app.state.ask_data_principal_resolver,
    )

    try:
        response = TestClient(app).get("/api/v1/ask-data/config")
        assert response.status_code == 403
    finally:
        for name, value in previous.items():
            setattr(ask_data_api, name, value)


def test_query_run_entry_type_matches_api_surface():
    calls = []

    class FakeRunService:
        class Repository:
            def get_query(self, query_id):
                return None

        repository = Repository()

        async def execute(self, **kwargs):
            calls.append(kwargs)
            query = QueryRun(
                query_id=f"q-{len(calls)}",
                request_id="request",
                user_id=kwargs["user_id"],
                question=kwargs["question"],
                status=RunStatus.REJECTED,
            )
            return query, QueryRunResult(status="rejected")

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[ask_data_api.get_run_service] = FakeRunService
    client = TestClient(app)

    assert client.post(
        "/api/v1/ask-data/query",
        json={
            "question": "not supported",
            "plan": {"action": "reject", "reason_code": "NO_SCENE"},
        },
    ).status_code == 200
    assert client.post(
        "/api/v1/ask-data/scenes/contracts/query",
        json={
            "question": "not supported",
            "plan": {"action": "reject", "reason_code": "NO_SCENE"},
        },
    ).status_code == 200

    assert [call["entry_type"] for call in calls] == ["main_agent", "scene_api"]
