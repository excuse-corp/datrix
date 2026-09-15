import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock

from dbgpt.component import SystemApp
from dbgpt.model.base import ModelInstance
from dbgpt.model.cluster import WorkerStartupRequest
from dbgpt.model.parameter import WorkerType
from dbgpt.storage.metadata import db
from dbgpt_serve.core import BaseServeConfig
from dbgpt_serve.core.tests.conftest import (  # noqa: F401
    asystem_app,
    client,
    config,
    system_app,
)

from ..api.endpoints import init_endpoints, model_list, router, set_default_model
from ..api.endpoints import start_model
from ..api.schemas import DefaultModelRequest, ModelResponse
from ..config import SERVE_CONFIG_KEY_PREFIX


@pytest.fixture(autouse=True)
def setup_and_teardown():
    db.init_db("sqlite:///:memory:")
    db.create_all()

    yield


def client_init_caller(app: FastAPI, system_app: SystemApp, config: BaseServeConfig):
    app.include_router(router)
    init_endpoints(system_app, config)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client, asystem_app, has_auth",
    [
        (
            {
                "app_caller": client_init_caller,
                "client_api_key": "test_token1",
            },
            {
                "app_config": {
                    f"{SERVE_CONFIG_KEY_PREFIX}api_keys": "test_token1,test_token2"
                }
            },
            True,
        ),
        (
            {
                "app_caller": client_init_caller,
                "client_api_key": "error_token",
            },
            {
                "app_config": {
                    f"{SERVE_CONFIG_KEY_PREFIX}api_keys": "test_token1,test_token2"
                }
            },
            False,
        ),
    ],
    indirect=["client", "asystem_app"],
)
async def test_api_health(client: AsyncClient, asystem_app, has_auth: bool):
    response = await client.get("/test_auth")
    if has_auth:
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    else:
        assert response.status_code == 401
        assert response.json() == {
            "detail": {
                "error": {
                    "message": "",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_api_key",
                }
            }
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client", [{"app_caller": client_init_caller}], indirect=["client"]
)
async def test_api_health(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client", [{"app_caller": client_init_caller}], indirect=["client"]
)
async def test_api_create(client: AsyncClient):
    # TODO: add your test case
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client", [{"app_caller": client_init_caller}], indirect=["client"]
)
async def test_api_update(client: AsyncClient):
    # TODO: implement your test case
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client", [{"app_caller": client_init_caller}], indirect=["client"]
)
async def test_api_query(client: AsyncClient):
    # TODO: implement your test case
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client", [{"app_caller": client_init_caller}], indirect=["client"]
)
async def test_api_query_by_page(client: AsyncClient):
    # TODO: implement your test case
    pass


def _model_request() -> WorkerStartupRequest:
    return WorkerStartupRequest(
        host="127.0.0.1",
        port=8001,
        model="test-model",
        worker_type=WorkerType.LLM,
        params={},
    )


@pytest.mark.asyncio
async def test_start_model_is_idempotent_for_running_worker():
    request = _model_request()
    worker_manager = MagicMock()
    worker_manager.get_model_instances = AsyncMock(return_value=[MagicMock()])
    worker_manager.model_startup = AsyncMock()
    model_storage = MagicMock()
    model_storage.query_models.return_value = [request]

    response = await start_model(request, worker_manager, model_storage)

    assert response.success is True
    worker_manager.model_startup.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_model_handles_concurrent_start():
    request = _model_request()
    worker_manager = MagicMock()
    worker_manager.get_model_instances = AsyncMock(
        side_effect=[[], [MagicMock()]]
    )
    worker_manager.model_startup = AsyncMock(
        side_effect=Exception("worker instances is exist")
    )
    model_storage = MagicMock()
    model_storage.query_models.return_value = [request]

    response = await start_model(request, worker_manager, model_storage)

    assert response.success is True
    worker_manager.model_startup.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_model_list_uses_generation_probe_for_llm_health(monkeypatch):
    controller = MagicMock()
    controller.get_all_instances = AsyncMock(
        side_effect=[
            [
                ModelInstance(
                    model_name="WorkerManager@service",
                    host="127.0.0.1",
                    port=7771,
                    healthy=True,
                )
            ],
            [
                ModelInstance(
                    model_name="bad-model@llm",
                    host="127.0.0.1",
                    port=7771,
                    healthy=True,
                )
            ],
        ]
    )

    async def failed_probe(model_name, host, port, params=None):
        return False, "401 Unauthorized"

    monkeypatch.setattr(
        "dbgpt_serve.model.api.endpoints._probe_llm_model_health", failed_probe
    )

    response = await model_list(controller)

    assert response.success is True
    assert response.data[0].model_name == "bad-model"
    assert response.data[0].healthy is False
    assert response.data[0].health_reason == "401 Unauthorized"


def test_resolve_default_model_falls_back_to_healthy_llm(monkeypatch, tmp_path):
    from ..api import endpoints

    monkeypatch.chdir(tmp_path)
    (tmp_path / "pilot" / "meta_data").mkdir(parents=True)
    (tmp_path / "pilot" / "meta_data" / "default_model.json").write_text(
        '{"model_name": "bad-model"}', encoding="utf-8"
    )
    monkeypatch.setattr(endpoints, "_configured_default_llm", lambda: None)

    default_model = endpoints._resolve_default_model(
        [
            ModelResponse(
                model_name="bad-model",
                worker_type="llm",
                host="127.0.0.1",
                port=7771,
                manager_host="127.0.0.1",
                manager_port=7771,
                healthy=False,
                check_healthy=True,
            ),
            ModelResponse(
                model_name="good-model",
                worker_type="llm",
                host="127.0.0.1",
                port=7771,
                manager_host="127.0.0.1",
                manager_port=7771,
                healthy=True,
                check_healthy=True,
            ),
        ]
    )

    assert default_model["model_name"] == "good-model"
    assert default_model["source"] == "fallback"
    assert default_model["configured_model_name"] == "bad-model"


@pytest.mark.asyncio
async def test_set_default_model_rejects_unhealthy_llm(monkeypatch, tmp_path):
    controller = MagicMock()
    controller.get_all_instances = AsyncMock(
        side_effect=[
            [
                ModelInstance(
                    model_name="WorkerManager@service",
                    host="127.0.0.1",
                    port=7771,
                    healthy=True,
                )
            ],
            [
                ModelInstance(
                    model_name="bad-model@llm",
                    host="127.0.0.1",
                    port=7771,
                    healthy=True,
                )
            ],
        ]
    )

    async def failed_probe(model_name, host, port, params=None):
        return False, "401 Unauthorized"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "dbgpt_serve.model.api.endpoints._probe_llm_model_health", failed_probe
    )

    response = await set_default_model(
        DefaultModelRequest(model_name="bad-model"), controller
    )

    assert response.success is False
    assert "model is not available" in response.err_msg
    assert not (tmp_path / "pilot" / "meta_data" / "default_model.json").exists()


# Add more test cases according to your own logic
