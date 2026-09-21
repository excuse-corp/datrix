import asyncio
import json
import logging
import os
import time
from functools import cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security.http import HTTPAuthorizationCredentials, HTTPBearer

from dbgpt.component import SystemApp
from dbgpt.model.base import WorkerApplyType
from dbgpt.model.cluster import (
    WorkerApplyRequest,
    WorkerManager,
    WorkerManagerFactory,
    WorkerStartupRequest,
)
from dbgpt.model.cluster.controller.controller import BaseModelController
from dbgpt.model.cluster.storage import ModelStorage
from dbgpt.model.parameter import WorkerType
from dbgpt_serve.core import Result

from ..config import SERVE_SERVICE_COMPONENT_NAME, ServeConfig
from ..service.service import Service
from .schemas import DefaultModelRequest, ModelResponse

logger = logging.getLogger(__name__)
router = APIRouter()

_DEFAULT_MODEL_CONFIG_PATH = Path("pilot/meta_data/default_model.json")
_MODEL_HEALTH_PROBE_CACHE: Dict[str, Tuple[float, bool, Optional[str]]] = {}
_MODEL_HEALTH_PROBE_TTL_SECONDS = float(
    os.getenv("DATAMAN_MODEL_HEALTH_PROBE_TTL_SECONDS", "30")
)
_MODEL_HEALTH_PROBE_TIMEOUT_SECONDS = float(
    os.getenv("DATAMAN_MODEL_HEALTH_PROBE_TIMEOUT_SECONDS", "8")
)
_MODEL_HEALTH_REASON_LIMIT = 240

# Add your API endpoints here

global_system_app: Optional[SystemApp] = None


def get_service() -> Service:
    """Get the service instance"""
    return global_system_app.get_component(SERVE_SERVICE_COMPONENT_NAME, Service)


def get_worker_manager() -> WorkerManager:
    """Get the worker manager instance"""
    return WorkerManagerFactory.get_instance(global_system_app).create()


def get_model_controller() -> BaseModelController:
    """Get the model controller instance"""
    return BaseModelController.get_instance(global_system_app)


def get_model_storage() -> ModelStorage:
    """Get the model storage instance"""
    from ..serve import Serve as ModelServe

    model_serve = ModelServe.get_instance(global_system_app)
    # Persistent model storage
    model_storage = ModelStorage(model_serve.model_storage)
    return model_storage


get_bearer_token = HTTPBearer(auto_error=False)


@cache
def _parse_api_keys(api_keys: str) -> List[str]:
    """Parse the string api keys to a list

    Args:
        api_keys (str): The string api keys

    Returns:
        List[str]: The list of api keys
    """
    if not api_keys:
        return []
    return [key.strip() for key in api_keys.split(",")]


async def check_api_key(
    auth: Optional[HTTPAuthorizationCredentials] = Depends(get_bearer_token),
    service: Service = Depends(get_service),
) -> Optional[str]:
    """Check the api key

    If the api key is not set, allow all.

    Your can pass the token in you request header like this:

    .. code-block:: python

        import requests

        client_api_key = "your_api_key"
        headers = {"Authorization": "Bearer " + client_api_key}
        res = requests.get("http://test/hello", headers=headers)
        assert res.status_code == 200

    """
    if service.config.api_keys:
        api_keys = _parse_api_keys(service.config.api_keys)
        if auth is None or (token := auth.credentials) not in api_keys:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": {
                        "message": "",
                        "type": "invalid_request_error",
                        "param": None,
                        "code": "invalid_api_key",
                    }
                },
            )
        return token
    else:
        # api_keys not set; allow all
        return None


def _read_default_model_name() -> Optional[str]:
    if not _DEFAULT_MODEL_CONFIG_PATH.exists():
        return None
    try:
        value = json.loads(
            _DEFAULT_MODEL_CONFIG_PATH.read_text(encoding="utf-8")
        ).get("model_name")
    except Exception:
        return None
    return value if isinstance(value, str) and value.strip() else None


def _write_default_model_name(model_name: str) -> None:
    _DEFAULT_MODEL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DEFAULT_MODEL_CONFIG_PATH.write_text(
        json.dumps({"model_name": model_name}, ensure_ascii=False),
        encoding="utf-8",
    )


def _clear_model_health_probe_cache() -> None:
    _MODEL_HEALTH_PROBE_CACHE.clear()


def _configured_default_llm() -> Optional[str]:
    try:
        from dbgpt._private.config import Config
        from dbgpt_app.config import ApplicationConfig

        app_config = Config().parse_config(ApplicationConfig, hook_section="hooks")
        configured = app_config.models.default_llm
    except Exception as e:
        logger.debug("Read configured default LLM failed: %s", e)
        return None
    return configured if isinstance(configured, str) and configured.strip() else None


def _sanitize_health_reason(reason: Optional[str]) -> Optional[str]:
    if not reason:
        return None
    normalized = " ".join(str(reason).split())
    if len(normalized) > _MODEL_HEALTH_REASON_LIMIT:
        return normalized[: _MODEL_HEALTH_REASON_LIMIT - 3] + "..."
    return normalized


def _probe_cache_key(model_name: str, host: str, port: int) -> str:
    return f"{model_name}@{host}:{port}"


async def _probe_openai_compatible_llm(
    model_name: str, params: Dict
) -> Tuple[bool, Optional[str]]:
    api_base = params.get("api_base")
    api_key = params.get("api_key")
    if not api_base or not api_key:
        return False, "Missing OpenAI-compatible api_base or api_key"

    try:
        import httpx

        timeout = httpx.Timeout(
            _MODEL_HEALTH_PROBE_TIMEOUT_SECONDS,
            connect=min(2.0, _MODEL_HEALTH_PROBE_TIMEOUT_SECONDS),
        )
        payload = {
            "model": params.get("backend") or model_name,
            "messages": [{"role": "user", "content": "Reply OK"}],
            "temperature": 0,
            "max_tokens": 8,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {api_key}"}
        url = api_base.rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            return False, response.text or f"HTTP {response.status_code}"
        data = response.json() if response.content else {}
        if data.get("error"):
            return False, str(data.get("error"))
        if data.get("choices"):
            return True, None
        return False, "OpenAI-compatible probe returned no choices"
    except Exception as e:
        return False, f"OpenAI-compatible probe failed: {e}"


async def _probe_worker_stream_llm(
    model_name: str, host: str, port: int
) -> Tuple[bool, Optional[str]]:
    payload = {
        "model": model_name,
        "messages": [{"role": "human", "content": "Reply OK"}],
        "temperature": 0,
        "max_new_tokens": 8,
        "echo": False,
    }
    url = f"http://{host}:{port}/api/worker/generate_stream"
    try:
        import httpx

        timeout = httpx.Timeout(
            _MODEL_HEALTH_PROBE_TIMEOUT_SECONDS,
            connect=min(2.0, _MODEL_HEALTH_PROBE_TIMEOUT_SECONDS),
        )
        delimiter = b"\0"
        buffer = b""
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                async for raw_chunk in response.aiter_raw():
                    buffer += raw_chunk
                    while delimiter in buffer:
                        chunk, buffer = buffer.split(delimiter, 1)
                        if not chunk:
                            continue
                        data = json.loads(chunk.decode())
                        if data.get("error_code") == 0:
                            return True, None
                        return False, data.get("text") or data.get("message")
        return False, "LLM probe returned empty stream"
    except Exception as e:
        return False, f"LLM probe failed: {e}"


async def _probe_llm_model_health(
    model_name: str, host: str, port: int, params: Optional[Dict] = None
) -> Tuple[bool, Optional[str]]:
    cache_key = _probe_cache_key(model_name, host, port)
    now = time.monotonic()
    cached = _MODEL_HEALTH_PROBE_CACHE.get(cache_key)
    if cached and cached[0] > now:
        return cached[1], cached[2]

    if params and params.get("provider") == "proxy/openai":
        healthy, reason = await _probe_openai_compatible_llm(model_name, params)
    else:
        healthy, reason = await _probe_worker_stream_llm(model_name, host, port)

    reason = _sanitize_health_reason(reason)
    _MODEL_HEALTH_PROBE_CACHE[cache_key] = (
        now + _MODEL_HEALTH_PROBE_TTL_SECONDS,
        healthy,
        reason,
    )
    return healthy, reason


async def _build_model_responses(
    controller: BaseModelController,
    probe_llm_health: bool = True,
) -> List[ModelResponse]:
    responses = []
    probe_targets = []
    model_storage = None
    stored_model_items = []
    try:
        model_storage = get_model_storage()
        stored_model_items = model_storage.all_model_items(enabled=None)
    except Exception as e:
        logger.debug("Model storage is unavailable when listing models: %s", e)
    managers = await controller.get_all_instances(
        model_name="WorkerManager@service", healthy_only=True
    )
    manager_map = dict(map(lambda manager: (manager.host, manager), managers))
    models = await controller.get_all_instances()
    for model in models:
        worker_name, worker_type = model.model_name.split("@")
        if worker_type not in WorkerType.values():
            continue
        stored_params = None
        provider = None
        stored_enabled = True
        if model_storage:
            try:
                stored_models = model_storage.query_models(
                    worker_name,
                    worker_type,
                    host=model.host,
                    port=model.port,
                    enabled=None,
                )
                if not stored_models:
                    stored_models = model_storage.query_models(worker_name, worker_type)
                if len(stored_models) == 1:
                    stored_params = stored_models[0].params
                    provider = stored_params.get("provider")
                matching_items = [
                    item
                    for item in stored_model_items
                    if item.model == worker_name
                    and item.worker_type == worker_type
                    and item.host == model.host
                    and item.port == model.port
                ]
                if not matching_items:
                    matching_items = [
                        item
                        for item in stored_model_items
                        if item.model == worker_name and item.worker_type == worker_type
                    ]
                if len(matching_items) == 1:
                    stored_enabled = matching_items[0].enabled
            except Exception as e:
                logger.debug(
                    "Fetch stored params for model %s@%s failed: %s",
                    worker_name,
                    worker_type,
                    e,
                )
        manager_host = model.host if manager_map.get(model.host) else ""
        manager_port = manager_map[model.host].port if manager_map.get(model.host) else -1
        health_reason = None if model.healthy else "No recent worker heartbeat"
        response = ModelResponse(
            model_name=worker_name,
            worker_type=worker_type,
            host=model.host,
            port=model.port,
            manager_host=manager_host,
            manager_port=manager_port,
            healthy=bool(model.healthy),
            check_healthy=model.check_healthy,
            last_heartbeat=model.str_last_heartbeat,
            prompt_template=model.prompt_template,
            health_reason=health_reason,
            provider=provider,
            params=stored_params,
            enabled=stored_enabled,
            running=True,
        )
        if probe_llm_health and worker_type == WorkerType.LLM.value and model.healthy:
            probe_targets.append(
                (len(responses), worker_name, model.host, model.port, stored_params)
            )
        responses.append(response)

    if probe_targets:
        probe_results = await asyncio.gather(
            *(
                _probe_llm_model_health(model_name, host, port, stored_params)
                for _, model_name, host, port, stored_params in probe_targets
            ),
            return_exceptions=True,
        )
        for target, probe_result in zip(probe_targets, probe_results):
            index, _, _, _, _ = target
            if isinstance(probe_result, Exception):
                responses[index].healthy = False
                responses[index].health_reason = _sanitize_health_reason(
                    f"LLM probe failed: {probe_result}"
                )
                continue
            healthy, reason = probe_result
            responses[index].healthy = responses[index].healthy and healthy
            responses[index].health_reason = None if healthy else reason or "LLM probe failed"

    active_keys = {(item.model_name, item.worker_type) for item in responses}
    for stored in stored_model_items:
        model_key = (stored.model, stored.worker_type)
        if model_key in active_keys:
            continue
        responses.append(
            ModelResponse(
                model_name=stored.model,
                worker_type=stored.worker_type,
                host=stored.host,
                port=stored.port,
                manager_host="",
                manager_port=-1,
                healthy=False,
                check_healthy=False,
                last_heartbeat=None,
                health_reason=(
                    "Model is stopped"
                    if not stored.enabled
                    else "Model worker is not running"
                ),
                provider=stored.provider,
                params=stored.params,
                enabled=stored.enabled,
                running=False,
            )
        )
    return responses


def _resolve_default_model(responses: List[ModelResponse]) -> dict:
    persisted = _read_default_model_name()
    configured = _configured_default_llm()
    healthy_llms = [
        item.model_name
        for item in responses
        if item.worker_type == WorkerType.LLM.value and item.healthy
    ]
    for candidate, source in ((persisted, "user"), (configured, "config")):
        if candidate and candidate in healthy_llms:
            return {
                "model_name": candidate,
                "source": source,
                "configured_model_name": persisted or configured,
                "available": True,
            }
    return {
        "model_name": healthy_llms[0] if healthy_llms else None,
        "source": "fallback" if healthy_llms else None,
        "configured_model_name": persisted or configured,
        "available": bool(healthy_llms),
    }


@router.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok"}


@router.get("/test_auth", dependencies=[Depends(check_api_key)])
async def test_auth():
    """Test auth endpoint"""
    return {"status": "ok"}


@router.get("/model-types")
async def model_params(worker_manager: WorkerManager = Depends(get_worker_manager)):
    try:
        params = []
        workers = await worker_manager.supported_models()
        for worker in workers:
            for model in worker.models:
                model_dict = model.__dict__
                model_dict["host"] = worker.host
                model_dict["port"] = worker.port
                params.append(model_dict)
        return Result.succ(params)
    except Exception as e:
        return Result.failed(err_code="E000X", msg=f"model stop failed {e}")


@router.get("/models")
async def model_list(
    controller: BaseModelController = Depends(get_model_controller),
):
    try:
        responses = await _build_model_responses(controller)
        return Result.succ(responses)

    except Exception as e:
        return Result.failed(err_code="E000X", msg=f"model list error {e}")


@router.get("/default-model")
async def get_default_model(
    controller: BaseModelController = Depends(get_model_controller),
):
    try:
        responses = await _build_model_responses(controller)
        return Result.succ(_resolve_default_model(responses))
    except Exception as e:
        logger.error("get default model failed %s", e)
        return Result.failed(err_code="E000X", msg=f"get default model failed {e}")


@router.put("/default-model")
async def set_default_model(
    request: DefaultModelRequest,
    controller: BaseModelController = Depends(get_model_controller),
):
    try:
        model_name = request.model_name.strip()
        responses = await _build_model_responses(controller)
        candidates = [
            item
            for item in responses
            if item.worker_type == WorkerType.LLM.value and item.model_name == model_name
        ]
        if not candidates:
            return Result.failed(err_code="E000X", msg="model not found")
        if not any(item.healthy for item in candidates):
            reason = next((item.health_reason for item in candidates if item.health_reason), None)
            message = "model is not available"
            if reason:
                message = f"{message}: {reason}"
            return Result.failed(err_code="E000X", msg=message)
        _write_default_model_name(model_name)
        return Result.succ(
            {
                "model_name": model_name,
                "source": "user",
                "configured_model_name": model_name,
                "available": True,
            }
        )
    except Exception as e:
        logger.error("set default model failed %s", e)
        return Result.failed(err_code="E000X", msg=f"set default model failed {e}")


@router.post("/models/stop")
async def model_stop(
    request: WorkerStartupRequest,
    worker_manager: WorkerManager = Depends(get_worker_manager),
    model_storage: ModelStorage = Depends(get_model_storage),
):
    try:
        request.params = {}
        await worker_manager.model_shutdown(request)
        if not request.delete_after:
            updated = model_storage.set_enabled(
                request.model,
                request.worker_type.value,
                enabled=False,
                sys_code=request.sys_code,
                user_name=request.user_name,
                host=request.host,
                port=request.port,
            )
            if updated == 0:
                logger.warning(
                    "Stopped model %s but no persisted model record matched the worker",
                    request.model,
                )
        _clear_model_health_probe_cache()
        return Result.succ(True)
    except Exception as e:
        return Result.failed(err_code="E000X", msg=f"model stop failed {e}")


@router.post("/models")
async def create_model(
    request: WorkerStartupRequest,
    worker_manager: WorkerManager = Depends(get_worker_manager),
):
    """Create a model.

    Must provide the full information of the model, including the host, port,
    model name, worker type, and params.
    """
    try:
        await worker_manager.model_startup(request)
        _clear_model_health_probe_cache()
        return Result.succ(True)
    except Exception as e:
        logger.error(f"model start failed {e}")
        return Result.failed(err_code="E000X", msg=f"model start failed {e}")


@router.put("/models")
async def update_model(
    request: WorkerStartupRequest,
    worker_manager: WorkerManager = Depends(get_worker_manager),
    model_storage: ModelStorage = Depends(get_model_storage),
):
    """Update an existing model's startup params.

    If the model is running, apply the new params and restart only when the worker
    indicates a restart is required. The persisted startup config is updated in
    both running and stopped cases.
    """
    try:
        worker_type = request.worker_type.value
        stored_models = model_storage.query_models(request.model, worker_type)
        existing_workers = await worker_manager.get_model_instances(
            worker_type, request.model, healthy_only=False
        )
        if not stored_models and not existing_workers:
            return Result.failed(err_code="E000X", msg="model not found")

        if existing_workers:
            apply_req = WorkerApplyRequest(
                model=request.model,
                apply_type=WorkerApplyType.UPDATE_PARAMS,
                worker_type=request.worker_type,
                params=request.params,
            )
            out = await worker_manager.worker_apply(apply_req)
            if not out.success:
                return Result.failed(err_code="E000X", msg=out.message)

        if existing_workers:
            request.host = existing_workers[0].host
            request.port = existing_workers[0].port
        model_storage.save_or_update(request)
        _clear_model_health_probe_cache()
        return Result.succ(True)
    except Exception as e:
        logger.error(f"model update failed {e}")
        return Result.failed(err_code="E000X", msg=f"model update failed {e}")


@router.post("/models/start")
async def start_model(
    request: WorkerStartupRequest,
    worker_manager: WorkerManager = Depends(get_worker_manager),
    model_storage: ModelStorage = Depends(get_model_storage),
):
    """Start an existing model.

    Starting an already running model is a successful no-op. The model page can
    issue this request while its health status is being refreshed.
    """

    try:
        models = model_storage.query_models(
            request.model,
            worker_type=request.worker_type.value,
            user_name=request.user_name,
            sys_code=request.sys_code,
            enabled=None,
            host=request.host,
            port=request.port,
        )
        if not models:
            return Result.failed(err_code="E000X", msg="model not found")
        if len(models) > 1:
            return Result.failed(err_code="E000X", msg="multiple models found")
        worker_type = request.worker_type.value
        existing_workers = await worker_manager.get_model_instances(
            worker_type, request.model, healthy_only=False
        )
        if existing_workers:
            model_storage.set_enabled(
                request.model,
                worker_type,
                enabled=True,
                sys_code=request.sys_code,
                user_name=request.user_name,
                host=models[0].host,
                port=models[0].port,
            )
            _clear_model_health_probe_cache()
            return Result.succ(True)

        try:
            await worker_manager.model_startup(models[0])
        except Exception:
            # Another request may have created the worker after the pre-check.
            existing_workers = await worker_manager.get_model_instances(
                worker_type, request.model, healthy_only=False
            )
            if existing_workers:
                model_storage.set_enabled(
                    request.model,
                    worker_type,
                    enabled=True,
                    sys_code=request.sys_code,
                    user_name=request.user_name,
                    host=models[0].host,
                    port=models[0].port,
                )
                _clear_model_health_probe_cache()
                return Result.succ(True)
            raise
        model_storage.set_enabled(
            request.model,
            worker_type,
            enabled=True,
            sys_code=request.sys_code,
            user_name=request.user_name,
            host=models[0].host,
            port=models[0].port,
        )
        _clear_model_health_probe_cache()
        return Result.succ(True)
    except Exception as e:
        logger.error(f"model start failed {e}")
        return Result.failed(err_code="E000X", msg=f"model start failed {e}")


def init_endpoints(system_app: SystemApp, config: ServeConfig) -> None:
    """Initialize the endpoints"""
    global global_system_app
    system_app.register(Service, config=config)
    global_system_app = system_app
