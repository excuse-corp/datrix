"""AskData Scene management HTTP endpoints."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import RLock
from typing import Any, Callable, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models.audit import AuditEvent, InMemoryAuditRepository
from ..models.entities import Scene, SceneStatus
from ..models.idempotency import IdempotencyRecord
from ..models.repositories import InMemorySceneRepository, SceneRepositoryError
from ..scene.publish_service import ScenePublishError, ScenePublishService
from ..scene.service import SceneLifecycleService
from ..schemas.schema import ViewSchema
from ..security import (
    AskDataAuthorizationError,
    AskDataAuthorizer,
    AskDataPrincipal,
)
from ..snapshot.service import InMemorySnapshotService

router = APIRouter(prefix="/api/v1/ask-data", tags=["AskData"])
_repository = InMemorySceneRepository()
_service = SceneLifecycleService(_repository)
_snapshot_service = InMemorySnapshotService()


def _missing_schema(scene_id: str, revision: int) -> ViewSchema:
    raise ScenePublishError(
        "SCHEMA_SOURCE_UNAVAILABLE",
        "No configured Schema resolver is available for this Scene",
    )


_publish_service = ScenePublishService(_repository, _snapshot_service, _missing_schema)
_run_service: Any = None
_router_agent: Any = None
_audit_repository = InMemoryAuditRepository()
_authorizer: AskDataAuthorizer | None = None
_principal_resolver: Callable[[Request], AskDataPrincipal] | None = None
_idempotency_repository: Any = None
_idempotency_cache: dict[
    tuple[str, str], tuple[str, dict[str, Any], datetime]
] = {}
_idempotency_lock = RLock()


def _idempotent_replay(
    *,
    endpoint: str,
    principal: AskDataPrincipal,
    idempotency_key: str | None,
    payload: Any,
) -> dict[str, Any] | None:
    if not idempotency_key:
        return None
    fingerprint = sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    cache_key = (f"{principal.user_id}:{endpoint}", idempotency_key)
    with _idempotency_lock:
        cached = _idempotency_cache.get(cache_key)
        if cached and cached[2] <= datetime.now(timezone.utc):
            _idempotency_cache.pop(cache_key, None)
            cached = None
        if cached is None and _idempotency_repository is not None:
            record = _idempotency_repository.get(
                f"{principal.user_id}:{endpoint}", idempotency_key
            )
            if record is not None:
                cached = (record.fingerprint, record.response, record.expires_at)
                _idempotency_cache[cache_key] = cached
        if cached is None:
            return None
        if cached[0] != fingerprint:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IDEMPOTENCY_KEY_CONFLICT",
                    "message": (
                        "Idempotency-Key was already used with a different request"
                    ),
                },
            )
        return deepcopy(cached[1])


def _remember_idempotent(
    *,
    endpoint: str,
    principal: AskDataPrincipal,
    idempotency_key: str | None,
    payload: Any,
    response: dict[str, Any],
) -> None:
    if not idempotency_key:
        return
    fingerprint = sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    cache_key = (f"{principal.user_id}:{endpoint}", idempotency_key)
    with _idempotency_lock:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        copied = deepcopy(response)
        _idempotency_cache[cache_key] = (fingerprint, copied, expires_at)
        if _idempotency_repository is not None:
            _idempotency_repository.save(
                IdempotencyRecord(
                    scope=f"{principal.user_id}:{endpoint}",
                    key=idempotency_key,
                    fingerprint=fingerprint,
                    response=copied,
                    expires_at=expires_at,
                )
            )


def configure_ask_data_services(
    *,
    repository,
    snapshot_service,
    run_service,
    publish_service=None,
    audit_repository=None,
    authorizer: AskDataAuthorizer | None = None,
    idempotency_repository=None,
    principal_resolver: Callable[[Request], AskDataPrincipal] | None = None,
):
    """Inject production repositories without changing the public API paths."""
    global _repository, _service, _snapshot_service, _run_service
    global _publish_service, _router_agent, _audit_repository, _authorizer
    global _idempotency_repository, _principal_resolver
    _repository = repository
    _service = SceneLifecycleService(repository)
    _snapshot_service = snapshot_service
    _run_service = run_service
    _publish_service = publish_service or ScenePublishService(
        repository, snapshot_service, _missing_schema
    )
    _router_agent = None
    if audit_repository is not None:
        _audit_repository = audit_repository
    _authorizer = authorizer or (
        AskDataAuthorizer() if principal_resolver is not None else None
    )
    _idempotency_repository = idempotency_repository
    _principal_resolver = principal_resolver


def configure_sql_ask_data_services(
    db_manager,
    *,
    schema_resolver=None,
    knowledge_manager=None,
    authorizer: AskDataAuthorizer | None = None,
    idempotency_repository=None,
    principal_resolver: Callable[[Request], AskDataPrincipal] | None = None,
    max_parallel_agents: int = 5,
    max_parallel_per_datasource: int = 3,
    max_concurrent_queries: int = 10,
    query_queue_timeout_seconds: float = 30,
    agent_timeout_seconds: float = 30,
    query_total_timeout_seconds: float = 120,
):
    from ..service_factory import create_sql_ask_data_services

    bundle = create_sql_ask_data_services(
        db_manager,
        schema_resolver or _missing_schema,
        knowledge_manager=knowledge_manager,
        max_parallel_agents=max_parallel_agents,
        max_parallel_per_datasource=max_parallel_per_datasource,
        max_concurrent_queries=max_concurrent_queries,
        query_queue_timeout_seconds=query_queue_timeout_seconds,
        agent_timeout_seconds=agent_timeout_seconds,
        query_total_timeout_seconds=query_total_timeout_seconds,
    )
    configure_ask_data_services(
        repository=bundle.scene_repository,
        snapshot_service=bundle.snapshot_service,
        run_service=bundle.run_service,
        publish_service=bundle.publish_service,
        audit_repository=bundle.audit_repository,
        authorizer=authorizer,
        idempotency_repository=idempotency_repository or bundle.idempotency_repository,
        principal_resolver=principal_resolver,
    )
    # Keep the global Ontology in the same metadata database as Scene and run
    # records.  This also makes the RouterAgent created below use its active
    # immutable Snapshot rather than the process-local fallback.
    from .ontology import configure_ontology_service
    from ..ontology.repository import SqlOntologyRepository
    from ..ontology.service import OntologyLifecycleService

    configure_ontology_service(OntologyLifecycleService(SqlOntologyRepository(db_manager)))
    return bundle


class SceneCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    data_source_name: str = Field(min_length=1)
    view_name: str = Field(min_length=1)
    semantic_md: str = Field(min_length=1)


class SceneUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    data_source_name: str = Field(min_length=1)
    view_name: str = Field(min_length=1)
    semantic_md: str = Field(min_length=1)


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    plan: dict[str, Any] | None = None
    user_id: str = Field(default="api-user", min_length=1)
    conversation_id: str | None = None
    timezone: str | None = None
    response_mode: Literal["full", "data_only", "answer_only"] = "full"
    include_chart: bool = True
    max_agents: int = Field(default=10, ge=1, le=10)


class QueryReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str | None = Field(default=None, min_length=1)
    question: str | None = Field(default=None, min_length=1)
    plan: dict[str, Any] | None = None
    user_id: str = Field(default="api-user", min_length=1)

    @model_validator(mode="after")
    def require_answer(self) -> "QueryReplyRequest":
        if not self.answer and not self.question:
            raise ValueError("answer is required")
        return self


class SemanticMarkdownRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_md: str = Field(min_length=1)


def get_run_service():
    global _run_service
    if _run_service is None:
        from ..agents.orchestrator import AskDataOrchestrator
        from ..planning.validator import PlanValidator
        from ..query_service import SceneQueryService
        from ..run_service import RunExecutionService
        from ..snapshot.registry import SceneAgentRegistry

        registry = SceneAgentRegistry(_snapshot_service)
        orchestrator = AskDataOrchestrator(
            PlanValidator(registry),
            SceneQueryService(),
        )
        _run_service = RunExecutionService(orchestrator)
    return _run_service


def get_router_agent():
    global _router_agent
    if _router_agent is None:
        from ..agents.router import RouterAgent
        from .ontology import get_ontology_service
        from ..snapshot.registry import SceneAgentRegistry

        _router_agent = RouterAgent(
            SceneAgentRegistry(_snapshot_service),
            ontology_snapshot_provider=get_ontology_service().active_snapshot,
        )
    return _router_agent


class SceneSummary(BaseModel):
    scene_id: str
    name: str
    description: str
    status: SceneStatus
    data_source_name: str
    view_name: str
    latest_revision: int
    active_revision: int | None
    current_snapshot_id: str | None
    query_api: str
    created_at: str
    updated_at: str


def get_scene_service() -> SceneLifecycleService:
    return _service


def get_publish_service() -> ScenePublishService:
    return _publish_service


def _default_engine_resolver(data_source: str):
    errors: list[str] = []
    try:
        from dbgpt._private.config import Config

        CFG = Config()
        connector = CFG.local_db_manager.get_connector(data_source)
        for attr in ("_engine", "engine"):
            engine = getattr(connector, attr, None)
            if engine is not None:
                return engine
        errors.append(f"connector {type(connector).__name__} exposes no SQLAlchemy engine")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    suffix = f": {'; '.join(errors)}" if errors else ""
    raise RuntimeError(
        f"ENGINE_UNAVAILABLE: no query engine is configured for {data_source}{suffix}"
    )


def _default_scene_query_agent(model_name: str | None = None):
    """Build the default LLM-backed SceneQueryAgent for HTTP AskData APIs."""
    from dbgpt._private.config import Config
    from dbgpt.component import ComponentType
    from dbgpt.core import ModelMessage, ModelMessageRoleType, ModelRequest
    from dbgpt.model.cluster import WorkerManagerFactory
    from dbgpt.model.cluster.client import DefaultLLMClient

    from ..agents import SceneQueryAgent

    cfg = Config()
    llm_client = DefaultLLMClient(
        cfg.SYSTEM_APP.get_component(
            ComponentType.WORKER_MANAGER_FACTORY, WorkerManagerFactory
        ).create(),
        auto_convert_message=True,
    )

    async def generate(prompt: str) -> str:
        models = await llm_client.models()
        selected_model = model_name or (models[0].model if models else None)
        if not selected_model:
            raise RuntimeError("No models available for SceneQueryAgent")
        request = ModelRequest.build_request(
            selected_model,
            messages=[
                ModelMessage(
                    role=ModelMessageRoleType.HUMAN,
                    content=prompt,
                )
            ],
            temperature=0,
        )
        response = await llm_client.generate(request)
        if not response.success or not response.has_text:
            raise RuntimeError("SceneQueryAgent model generation failed")
        return response.text

    return SceneQueryAgent(generate)


async def _build_sql_agent_plan(
    *,
    question: str,
    principal: AskDataPrincipal,
    max_agents: int,
    scene_id: str | None = None,
) -> tuple[Any, dict[str, str]]:
    """Route to scenes and generate one scoped SQL statement per selected scene."""
    from dbgpt_app.openapi.api_v1.tools.ask_data import (
        _build_scene_sqls,
        _route_ask_data_plan,
    )

    from ..schemas.plan import CombinePlan, MainAgentPlan, PlanTask

    scene_query_agent = _default_scene_query_agent()
    if scene_id:
        plan = MainAgentPlan(
            action="execute",
            combine=CombinePlan(mode="separate"),
            tasks=[
                PlanTask(
                    task_id=f"scene_{scene_id}",
                    scene_id=scene_id,
                    question=question,
                    metrics=["__sql__"],
                )
            ],
        )
    else:
        plan = await _route_ask_data_plan(
            question,
            principal=principal,
            scene_query_agent=scene_query_agent,
            max_scenes=max_agents,
        )
    _require_plan_scene_access(principal, plan)

    async def noop_stream(_event_type: str, _payload: dict[str, Any]) -> None:
        return None

    sql_by_task = await _build_scene_sqls(
        plan,
        scene_query_agent,
        stream_callback=noop_stream,
        max_parallel_scene_agents=max_agents,
    )
    return plan, sql_by_task


def _request_id() -> str:
    return f"req_{uuid4().hex}"


def _plan_payload(plan: Any) -> dict[str, Any]:
    return {
        "task_count": len(getattr(plan, "tasks", ()) or ()),
        "combine_mode": (
            getattr(getattr(plan, "combine", None), "mode", None)
            or "separate"
        ),
        "scenes": [
            task.scene_id
            for task in (getattr(plan, "tasks", ()) or ())
            if getattr(task, "scene_id", None)
        ],
    }


def _apply_request_timezone(plan: Any, timezone: str | None) -> Any:
    if not timezone or not getattr(plan, "tasks", None):
        return plan
    tasks = []
    for task in plan.tasks:
        if task.time_range is None:
            tasks.append(task)
            continue
        tasks.append(
            task.model_copy(
                update={
                    "time_range": task.time_range.model_copy(
                        update={"timezone": timezone}
                    )
                }
            )
        )
    return plan.model_copy(update={"tasks": tasks})


def _query_response(
    query: Any,
    result: Any,
    plan: Any,
    *,
    response_mode: str = "full",
    include_chart: bool = True,
) -> dict[str, Any]:
    bundle = result.bundle or {}
    results = (
        []
        if response_mode == "answer_only"
        else [item.model_dump(mode="json") for item in result.results]
    )
    warnings = query.warnings
    answer = bundle.get("answer") if response_mode != "data_only" else None
    combined = bundle.get("combined") if response_mode != "answer_only" else None
    charts = (
        bundle.get("charts", [])
        if include_chart and response_mode != "data_only"
        else []
    )
    visible_bundle = dict(bundle)
    if response_mode == "answer_only":
        visible_bundle.pop("results", None)
        visible_bundle.pop("combined", None)
        visible_bundle.pop("charts", None)
    elif response_mode == "data_only":
        visible_bundle.pop("answer", None)
    if not include_chart:
        visible_bundle.pop("charts", None)
    return {
        "request_id": query.request_id,
        "status": query.status.value,
        "query_id": query.query_id,
        "conversation_id": query.conversation_id,
        "answer": answer,
        "clarification": result.clarification,
        "plan": _plan_payload(plan),
        "results": results,
        "combined": combined,
        "charts": charts,
        "warnings": warnings,
        "errors": result.errors,
        "data": {
            "query_id": query.query_id,
            "status": query.status.value,
            "results": results,
            "errors": result.errors,
            "warnings": warnings,
            "snapshot_ids": query.snapshot_ids,
            "combine": result.combine,
            "bundle": visible_bundle or None,
            "answer": answer,
            "conversation_id": query.conversation_id,
            "clarification": result.clarification,
            "duration_ms": query.duration_ms or 0,
        },
    }


def get_principal(request: Request) -> AskDataPrincipal:
    """Resolve the principal supplied by DB-GPT auth middleware.

    Header parsing is intentionally limited to integration environments. A
    configured DB-GPT middleware principal on ``request.state.user`` wins.
    """
    resolver = getattr(request.app.state, "ask_data_principal_resolver", None)
    resolver = resolver or _principal_resolver
    if resolver is not None:
        principal = resolver(request)
        if not isinstance(principal, AskDataPrincipal):
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "INVALID_PRINCIPAL",
                    "message": "Authentication resolver returned an invalid principal",
                },
            )
        return principal
    user = getattr(request.state, "user", None)
    if user is not None:
        if isinstance(user, dict):
            user_id = str(user.get("user_id") or user.get("username") or "anonymous")
            roles = user.get("roles") or user.get("role", ())
            scene_ids = user.get("scene_ids", ())
        else:
            user_id = str(
                getattr(user, "user_id", None)
                or getattr(user, "username", None)
                or "anonymous"
            )
            roles = getattr(user, "roles", None) or getattr(user, "role", ())
            scene_ids = getattr(user, "scene_ids", ())
        if isinstance(roles, str):
            roles = (roles,)
        return AskDataPrincipal(
            user_id=user_id,
            roles=frozenset(str(item) for item in roles),
            scene_ids=frozenset(str(item) for item in scene_ids),
        )
    roles = frozenset(
        item.strip()
        for item in request.headers.get("X-AskData-Role", "").split(",")
        if item.strip()
    )
    scene_ids = frozenset(
        item.strip()
        for item in request.headers.get("X-AskData-Scenes", "").split(",")
        if item.strip()
    )
    return AskDataPrincipal(
        user_id=request.headers.get("X-User-Id", "anonymous"),
        roles=roles,
        scene_ids=scene_ids,
    )


def _require_admin(principal: AskDataPrincipal) -> None:
    if _authorizer is None:
        return
    try:
        _authorizer.require_admin(principal)
    except AskDataAuthorizationError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": exc.code, "message": "Administrator permission required"},
        ) from exc


def _require_scene_access(principal: AskDataPrincipal, scene_id: str) -> None:
    if _authorizer is None:
        return
    try:
        _authorizer.require_scene_access(principal, scene_id)
    except AskDataAuthorizationError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "SCENE_NOT_FOUND", "message": "Scene not found"},
        ) from exc


def _require_plan_scene_access(principal: AskDataPrincipal, plan: Any) -> None:
    if getattr(plan, "action", None) != "execute":
        return
    for task in getattr(plan, "tasks", ()):
        _require_scene_access(principal, task.scene_id)


def _require_query_visibility(principal: AskDataPrincipal, query: Any) -> None:
    if _authorizer is not None and not principal.is_admin:
        if getattr(query, "user_id", None) != principal.user_id:
            raise HTTPException(
                status_code=404,
                detail={"code": "QUERY_NOT_FOUND", "message": "Query run not found"},
            )


def _query_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "QUERY_NOT_FOUND", "message": "Query run not found"},
    )


def _execution_http_error(exc: ValueError) -> HTTPException:
    code = str(exc)
    return HTTPException(
        status_code=409,
        detail={"code": code, "message": code},
    )


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _audit(
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    request_id: str,
    outcome: str,
    error_code: str | None = None,
    user_id: str = "api-user",
    details: dict[str, Any] | None = None,
) -> None:
    _audit_repository.append(
        AuditEvent(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=user_id,
            request_id=request_id,
            outcome=outcome,
            error_code=error_code,
            details=details or {},
        )
    )


def _summary(scene: Scene, service: SceneLifecycleService) -> SceneSummary:
    revision = service.repository.latest_revision(scene.scene_id)
    return SceneSummary(
        scene_id=scene.scene_id,
        name=scene.name,
        description=scene.description,
        status=scene.status,
        data_source_name=revision.data_source_name,
        view_name=revision.view_name,
        latest_revision=scene.latest_revision,
        active_revision=scene.active_revision,
        current_snapshot_id=scene.current_snapshot_id,
        query_api=f"/api/v1/ask-data/scenes/{scene.scene_id}/query",
        created_at=scene.created_at.isoformat(),
        updated_at=scene.updated_at.isoformat(),
    )


def _error(exc: SceneRepositoryError) -> HTTPException:
    status = {
        "SCENE_ID_CONFLICT": 409,
        "SCENE_NOT_FOUND": 404,
        "SCENE_NOT_ACTIVE": 409,
        "SCENE_HAS_NO_ACTIVE_SNAPSHOT": 409,
        "SCENE_MUST_BE_INACTIVE_TO_EDIT": 409,
    }.get(str(exc), 400)
    return HTTPException(
        status_code=status, detail={"code": str(exc), "message": str(exc)}
    )


@router.post("/scenes")
def create_scene(
    request: SceneCreateRequest,
    service: SceneLifecycleService = Depends(get_scene_service),
    publish_service: ScenePublishService = Depends(get_publish_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    request_id = _request_id()
    try:
        scene, revision = service.create(
            **request.model_dump(),
            created_by=principal.user_id,
        )
    except SceneRepositoryError as exc:
        _audit(
            action="scene.create",
            resource_type="scene",
            resource_id=request.scene_id,
            request_id=request_id,
            outcome="failed",
            error_code=str(exc),
            user_id=principal.user_id,
        )
        raise _error(exc) from exc
    try:
        validation, snapshot, registry_version = publish_service.publish(
            scene.scene_id, revision.revision
        )
        scene = service.repository.get_scene(scene.scene_id)
    except ScenePublishError as exc:
        try:
            service.delete(scene.scene_id)
        except Exception:
            pass
        _audit(
            action="scene.create",
            resource_type="scene",
            resource_id=request.scene_id,
            request_id=request_id,
            outcome="failed",
            error_code=exc.code,
            user_id=principal.user_id,
            details={"message": exc.message},
        )
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "message": exc.message}
        ) from exc
    _audit(
        action="scene.create",
        resource_type="scene",
        resource_id=scene.scene_id,
        request_id=request_id,
        outcome="succeeded",
        user_id=principal.user_id,
    )
    return {
        "request_id": request_id,
        "status": "succeeded",
        "data": _summary(scene, service),
        "revision": revision.revision,
        "validation": {
            "valid": validation.valid,
            "errors": [item.model_dump(mode="json") for item in validation.errors],
            "warnings": [item.model_dump(mode="json") for item in validation.warnings],
        },
        "snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "registry_version": f"reg_{registry_version}",
        },
    }


@router.get("/capabilities")
def list_capabilities(principal: AskDataPrincipal = Depends(get_principal)):
    items = []
    for snapshot in _snapshot_service.active_snapshots():
        if _authorizer is not None and not principal.can_access_scene(
            snapshot.scene_id
        ):
            continue
        projection = snapshot.routing_projection
        items.append(
            {
                "scene_id": snapshot.scene_id,
                "name": projection.get("name"),
                "description": projection.get("description"),
                "keywords": projection.get("keywords", []),
                "query_api": projection.get("query_api"),
            }
        )
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": {"items": items},
    }


@router.post("/query")
async def execute_query(
    request: QueryRequest,
    request_id_header: str | None = Header(default=None, alias="X-Request-Id"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    service=Depends(get_run_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    from ..schemas.plan import MainAgentPlan

    sql_by_task = None
    try:
        if request.plan is not None:
            plan = MainAgentPlan.model_validate(request.plan)
        else:
            plan, sql_by_task = await _build_sql_agent_plan(
                question=request.question,
                principal=principal,
                max_agents=request.max_agents,
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_PLAN", "message": str(exc)},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCENE_SQL_AGENT_FAILED",
                "message": str(exc),
            },
        ) from exc
    plan = _apply_request_timezone(plan, request.timezone)
    _require_plan_scene_access(principal, plan)
    request_id = request_id_header or _request_id()
    try:
        query, result = await service.execute(
            question=request.question,
            plan=plan,
            user_id=principal.user_id,
            request_id=request_id,
            conversation_id=request.conversation_id,
            idempotency_key=idempotency_key,
            engine_resolver=_default_engine_resolver,
            max_agents=request.max_agents,
            entry_type="main_agent",
            sql_by_task=sql_by_task,
        )
    except ValueError as exc:
        raise _execution_http_error(exc) from exc
    _audit(
        action="query.execute",
        resource_type="query",
        resource_id=query.query_id,
        request_id=request_id,
        outcome=query.status.value,
        error_code=result.errors[0].get("code") if result.errors else None,
        user_id=principal.user_id,
    )
    return _query_response(
        query,
        result,
        plan,
        response_mode=request.response_mode,
        include_chart=request.include_chart,
    )


@router.post("/scenes/{scene_id}/query")
async def execute_scene_query(
    scene_id: str,
    request: QueryRequest,
    request_id_header: str | None = Header(default=None, alias="X-Request-Id"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    service=Depends(get_run_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_scene_access(principal, scene_id)
    from ..schemas.plan import MainAgentPlan

    sql_by_task = None
    try:
        if request.plan is not None:
            plan = MainAgentPlan.model_validate(request.plan)
        else:
            plan, sql_by_task = await _build_sql_agent_plan(
                question=request.question,
                principal=principal,
                max_agents=1,
                scene_id=scene_id,
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_PLAN", "message": str(exc)},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCENE_SQL_AGENT_FAILED",
                "message": str(exc),
            },
        ) from exc
    plan = _apply_request_timezone(plan, request.timezone)
    if plan.action == "execute" and any(
        task.scene_id != scene_id for task in plan.tasks
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SCENE_SCOPE_MISMATCH",
                "message": "Plan contains a task outside the requested Scene",
            },
        )
    try:
        query, result = await service.execute(
            question=request.question,
            plan=plan,
            user_id=principal.user_id,
            request_id=request_id_header or _request_id(),
            conversation_id=request.conversation_id,
            idempotency_key=idempotency_key,
            engine_resolver=_default_engine_resolver,
            max_agents=request.max_agents,
            entry_type="scene_api",
            sql_by_task=sql_by_task,
        )
    except ValueError as exc:
        raise _execution_http_error(exc) from exc
    return _query_response(
        query,
        result,
        plan,
        response_mode=request.response_mode,
        include_chart=request.include_chart,
    )


@router.get("/runs/{query_id}")
def get_query_run(
    query_id: str,
    service=Depends(get_run_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    try:
        query = service.repository.get_query(query_id)
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "QUERY_NOT_FOUND", "message": "Query run not found"},
        ) from exc
    if query is None:
        raise _query_not_found()
    _require_query_visibility(principal, query)
    agent_runs = service.repository.agents_for_query(query_id)
    if _authorizer is not None and not principal.is_admin:
        for agent in agent_runs:
            _require_scene_access(principal, agent.scene_id)
    return {
        "request_id": query.request_id,
        "status": "succeeded",
        "data": query.model_dump(mode="json"),
        "agent_runs": [
            item.model_dump(mode="json")
            for item in agent_runs
        ],
    }


@router.get("/runs/{query_id}/agents")
def list_agent_runs(
    query_id: str,
    service=Depends(get_run_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    try:
        query = service.repository.get_query(query_id)
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "QUERY_NOT_FOUND", "message": "Query run not found"},
        ) from exc
    if query is None:
        raise _query_not_found()
    _require_query_visibility(principal, query)
    agent_runs = service.repository.agents_for_query(query_id)
    if _authorizer is not None and not principal.is_admin:
        for agent in agent_runs:
            _require_scene_access(principal, agent.scene_id)
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": {
            "items": [
                item.model_dump(mode="json")
                for item in agent_runs
            ]
        },
    }


@router.get("/agent-runs/{agent_run_id}")
def get_agent_run(
    agent_run_id: str,
    include_sql: bool = False,
    include_rag: bool = False,
    service=Depends(get_run_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    try:
        agent = service.repository.get_agent(agent_run_id)
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "AGENT_RUN_NOT_FOUND", "message": "Agent run not found"},
        ) from exc
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "AGENT_RUN_NOT_FOUND", "message": "Agent run not found"},
        )
    query = service.repository.get_query(agent.query_id)
    if query is None:
        raise _query_not_found()
    _require_query_visibility(principal, query)
    if _authorizer is not None and not principal.is_admin:
        _require_scene_access(principal, agent.scene_id)
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": agent.model_dump(mode="json"),
    }


@router.get("/runs")
def list_query_runs(
    entry_type: Literal["main_agent", "scene_api"] | None = None,
    scene_id: str | None = None,
    status: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service=Depends(get_run_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    items = service.repository.list_queries()
    if _authorizer is not None and not principal.is_admin:
        items = [item for item in items if item.user_id == principal.user_id]
    if entry_type:
        items = [item for item in items if item.entry_type == entry_type]
    if start_time:
        start = _utc_datetime(start_time)
        items = [item for item in items if _utc_datetime(item.created_at) >= start]
    if end_time:
        end = _utc_datetime(end_time)
        items = [item for item in items if _utc_datetime(item.created_at) <= end]
    if status:
        items = [item for item in items if item.status.value == status]
    if scene_id:
        items = [
            item
            for item in items
            if any(
                agent.scene_id == scene_id
                for agent in service.repository.agents_for_query(item.query_id)
            )
        ]
    total = len(items)
    start = (page - 1) * page_size
    items = items[start : start + page_size]
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": {
            "items": [
                {
                    "query_id": item.query_id,
                    "entry_type": item.entry_type,
                    "question": item.question,
                    "status": item.status.value,
                    "snapshot_ids": item.snapshot_ids,
                    "created_at": item.created_at.isoformat(),
                    "duration_ms": item.duration_ms,
                }
                for item in items
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
        },
    }


@router.post("/queries/{query_id}/reply")
async def reply_query(
    query_id: str,
    request: QueryReplyRequest,
    request_id_header: str | None = Header(default=None, alias="X-Request-Id"),
    service=Depends(get_run_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    try:
        existing = service.repository.get_query(query_id)
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "QUERY_NOT_FOUND", "message": "Query run not found"},
        ) from exc
    if existing is None:
        raise _query_not_found()
    if existing.status != "clarification_required":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "QUERY_NOT_CLARIFICATION_REQUIRED",
                "message": "Query is not awaiting clarification",
            },
        )
    if existing.user_id != principal.user_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "QUERY_NOT_FOUND", "message": "Query run not found"},
        )
    if existing.clarification_expired:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "QUERY_CLARIFICATION_EXPIRED",
                "message": "Clarification window has expired",
            },
        )
    if existing.clarification_round >= 2:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "QUERY_NOT_ACTIONABLE",
                "message": "Clarification limit has been reached",
            },
        )
    from ..schemas.plan import MainAgentPlan

    try:
        if request.plan is not None:
            plan = MainAgentPlan.model_validate(request.plan)
        else:
            plan = get_router_agent().route(
                f"{existing.question}\n补充信息：{request.answer or request.question}"
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_PLAN", "message": str(exc)},
        ) from exc
    try:
        query, result = await service.execute(
            question=(
                f"{existing.question}\n补充信息："
                f"{request.answer or request.question}"
            ),
            plan=plan,
            user_id=principal.user_id,
            request_id=request_id_header or _request_id(),
            engine_resolver=_default_engine_resolver,
            query_id=query_id,
        )
    except ValueError as exc:
        raise _execution_http_error(exc) from exc
    return _query_response(query, result, plan)


@router.post("/scenes/{scene_id}/queries/{query_id}/reply")
async def reply_scene_query(
    scene_id: str,
    query_id: str,
    request: QueryReplyRequest,
    request_id_header: str | None = Header(default=None, alias="X-Request-Id"),
    service=Depends(get_run_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_scene_access(principal, scene_id)
    try:
        agents = service.repository.agents_for_query(query_id)
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "QUERY_NOT_FOUND", "message": "Query run not found"},
        ) from exc
    if not any(agent.scene_id == scene_id for agent in agents):
        raise HTTPException(
            status_code=404,
            detail={"code": "QUERY_NOT_FOUND", "message": "Query run not found"},
        )
    return await reply_query(
        query_id=query_id,
        request=request,
        request_id_header=request_id_header,
        service=service,
        principal=principal,
    )


@router.post("/conversations/{conversation_id}/reset")
def reset_conversation(
    conversation_id: str,
    service=Depends(get_run_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    user_id = (
        principal.user_id
        if _authorizer is not None and not principal.is_admin
        else None
    )
    service.reset_conversation(conversation_id, user_id=user_id)
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": True,
    }


@router.get("/scenes")
def list_scenes(
    status: Literal["draft", "active", "inactive", "invalid"] | None = None,
    keyword: str | None = None,
    data_source_name: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: SceneLifecycleService = Depends(get_scene_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    scenes = service.repository.list_scenes()
    if _authorizer is not None and not principal.is_admin:
        scenes = [
            scene for scene in scenes if principal.can_access_scene(scene.scene_id)
        ]
    if status:
        scenes = [scene for scene in scenes if scene.status.value == status]
    if keyword:
        scenes = [
            scene
            for scene in scenes
            if keyword.lower() in f"{scene.name} {scene.description}".lower()
        ]
    if data_source_name:
        scenes = [
            scene
            for scene in scenes
            if service.repository.latest_revision(scene.scene_id).data_source_name
            == data_source_name
        ]
    total = len(scenes)
    start = (page - 1) * page_size
    items = [_summary(scene, service) for scene in scenes[start : start + page_size]]
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": {"items": items, "page": page, "page_size": page_size, "total": total},
    }


@router.get("/scenes/{scene_id}")
def get_scene(
    scene_id: str,
    service: SceneLifecycleService = Depends(get_scene_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_scene_access(principal, scene_id)
    try:
        scene = service.repository.get_scene(scene_id)
        revision = service.repository.latest_revision(scene_id)
    except SceneRepositoryError as exc:
        raise _error(exc) from exc
    data = _summary(scene, service).model_dump(mode="json")
    if revision.parsed_config_json:
        config = revision.parsed_config_json
        data["metrics"] = config.get("metrics", [])
        data["dimensions"] = config.get("dimensions", [])
        data["limits"] = config.get("query_limits", {})
    return {"request_id": _request_id(), "status": "succeeded", "data": data}


@router.get("/scenes/{scene_id}/api-spec")
def get_scene_api_spec(
    scene_id: str,
    service: SceneLifecycleService = Depends(get_scene_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_scene_access(principal, scene_id)
    try:
        scene = service.repository.get_scene(scene_id)
        revision = service.repository.latest_revision(scene_id)
    except SceneRepositoryError as exc:
        raise _error(exc) from exc
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": {
            "scene_id": scene.scene_id,
            "query_api": f"/api/v1/ask-data/scenes/{scene_id}/query",
            "capabilities": revision.parsed_config_json.get("agent", {})
            if revision.parsed_config_json
            else {},
            "limits": revision.parsed_config_json.get("query_limits", {})
            if revision.parsed_config_json
            else {},
        },
    }


@router.get("/config")
def get_ask_data_config(
    service=Depends(get_run_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    orchestrator = getattr(service, "orchestrator", None)
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": {
            "max_parallel_tasks": getattr(orchestrator, "max_parallel_tasks", 5),
            "max_parallel_per_datasource": getattr(
                orchestrator, "max_parallel_per_datasource", 3
            ),
            "max_concurrent_queries": getattr(
                orchestrator, "max_concurrent_queries", 10
            ),
            "query_queue_timeout_seconds": getattr(
                orchestrator, "query_queue_timeout_seconds", 30
            ),
            "task_timeout_seconds": getattr(
                orchestrator, "task_timeout_seconds", 30
            ),
            "total_timeout_seconds": getattr(
                orchestrator, "total_timeout_seconds", 120
            ),
            "runtime_secrets_exposed": False,
        },
    }


@router.post("/scenes/{scene_id}/semantic-md")
def save_semantic_md(
    scene_id: str,
    request: SemanticMarkdownRequest,
    service: SceneLifecycleService = Depends(get_scene_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        scene_model = service.repository.get_scene(scene_id)
        if scene_model.status != SceneStatus.INACTIVE:
            raise SceneRepositoryError("SCENE_MUST_BE_INACTIVE_TO_EDIT")
        scene, revision = service.update_draft(
            scene_id,
            name=scene_model.name,
            description=scene_model.description,
            data_source_name=service.repository.latest_revision(
                scene_id
            ).data_source_name,
            view_name=service.repository.latest_revision(scene_id).view_name,
            semantic_md=request.semantic_md,
            updated_by=principal.user_id,
        )
    except SceneRepositoryError as exc:
        raise _error(exc) from exc
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": _summary(scene, service),
        "revision": revision.revision,
    }


@router.get("/scenes/{scene_id}/semantic-md")
def get_semantic_md(
    scene_id: str,
    revision: int | None = None,
    service: SceneLifecycleService = Depends(get_scene_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_scene_access(principal, scene_id)
    try:
        revision = service.repository.get_revision(
            scene_id,
            revision
            or service.repository.get_scene(scene_id).latest_revision,
        )
    except SceneRepositoryError as exc:
        raise _error(exc) from exc
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": {
            "scene_id": scene_id,
            "revision": revision.revision,
            "semantic_md": revision.semantic_md,
            "semantic_hash": revision.semantic_hash,
        },
    }


def _snapshot_payload(
    snapshot: Any,
    *,
    include_runtime: bool = False,
    principal: AskDataPrincipal | None = None,
) -> dict[str, Any]:
    payload = {
        "snapshot_id": snapshot.snapshot_id,
        "scene_id": snapshot.scene_id,
        "scene_revision": int(snapshot.revision_id),
        "snapshot_status": snapshot.status.value,
        "content_hash": snapshot.content_hash,
        "source_hashes": snapshot.source_hashes.model_dump(mode="json"),
        "routing_projection": snapshot.routing_projection,
        "generated_at": snapshot.created_at.isoformat(),
    }
    if include_runtime:
        if principal is None or _authorizer is None:
            raise HTTPException(
                status_code=403,
                detail={"code": "ADMIN_REQUIRED", "message": "Runtime access denied"},
            )
        _require_admin(principal)
        payload["runtime_config"] = _authorizer.redact_runtime(
            principal, snapshot.scene_id, snapshot.runtime_config
        )
    return payload


@router.get("/scenes/{scene_id}/snapshot")
def get_active_snapshot(
    scene_id: str,
    service: ScenePublishService = Depends(get_publish_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_scene_access(principal, scene_id)
    snapshot = service.snapshot_service.active(scene_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SNAPSHOT_NOT_FOUND", "message": "Snapshot not found"},
        )
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": _snapshot_payload(snapshot),
    }


@router.get("/scenes/{scene_id}/snapshots/{snapshot_id}")
def get_snapshot(
    scene_id: str,
    snapshot_id: str,
    include_runtime: bool = False,
    service: ScenePublishService = Depends(get_publish_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_scene_access(principal, scene_id)
    try:
        snapshot = service.snapshot_service.get(snapshot_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "SNAPSHOT_NOT_FOUND", "message": "Snapshot not found"},
        ) from exc
    if snapshot.scene_id != scene_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "SNAPSHOT_NOT_FOUND", "message": "Snapshot not found"},
        )
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": _snapshot_payload(
            snapshot, include_runtime=include_runtime, principal=principal
        ),
    }


@router.get("/snapshots")
def list_snapshots(
    scene_id: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: ScenePublishService = Depends(get_publish_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    snapshots = service.snapshot_service.list_snapshots()
    if scene_id:
        snapshots = [item for item in snapshots if item.scene_id == scene_id]
    if status:
        snapshots = [item for item in snapshots if item.status.value == status]
    total = len(snapshots)
    start = (page - 1) * page_size
    snapshots = snapshots[start : start + page_size]
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": {
            "items": [
                {
                    "snapshot_id": item.snapshot_id,
                    "scene_id": item.scene_id,
                    "revision": int(item.revision_id),
                    "status": item.status.value,
                    "content_hash": item.content_hash,
                    "source_hashes": item.source_hashes.model_dump(mode="json"),
                }
                for item in snapshots
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
        },
    }


@router.get("/snapshots/routing-projections")
def list_routing_projections(
    service: ScenePublishService = Depends(get_publish_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    projections = service.snapshot_service.active_snapshots()
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": {
            "registry_version": f"reg_{service.snapshot_service.registry_version}",
            "max_agents_per_query": 10,
            "items": [item.routing_projection for item in projections],
        },
    }


@router.get("/snapshots/{snapshot_id}")
def get_snapshot_by_id(
    snapshot_id: str,
    include_runtime: bool = False,
    service: ScenePublishService = Depends(get_publish_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    try:
        snapshot = service.snapshot_service.get(snapshot_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "SNAPSHOT_NOT_FOUND", "message": "Snapshot not found"},
        ) from exc
    _require_scene_access(principal, snapshot.scene_id)
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": _snapshot_payload(
            snapshot, include_runtime=include_runtime, principal=principal
        ),
    }


@router.put("/scenes/{scene_id}")
def update_scene(
    scene_id: str,
    request: SceneUpdateRequest,
    service: SceneLifecycleService = Depends(get_scene_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        scene_model = service.repository.get_scene(scene_id)
        if scene_model.status != SceneStatus.INACTIVE:
            raise SceneRepositoryError("SCENE_MUST_BE_INACTIVE_TO_EDIT")
        scene, revision = service.update_draft(
            scene_id,
            **request.model_dump(),
            updated_by=principal.user_id,
        )
    except SceneRepositoryError as exc:
        raise _error(exc) from exc
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": _summary(scene, service),
        "revision": revision.revision,
    }


@router.post("/scenes/{scene_id}/revisions/{revision}/validate")
def validate_revision(
    scene_id: str,
    revision: int,
    service: ScenePublishService = Depends(get_publish_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        result, snapshot, registry_version = service.publish(scene_id, revision)
    except ScenePublishError as exc:
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "message": exc.message}
        ) from exc
    scene = service.repository.get_scene(scene_id)
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": {
            "scene_id": scene_id,
            "revision": revision,
            "valid": result.valid,
            "errors": [item.model_dump(mode="json") for item in result.errors],
            "warnings": [item.model_dump(mode="json") for item in result.warnings],
            "scene_status": scene.status.value,
            "snapshot_id": snapshot.snapshot_id,
            "registry_version": f"reg_{registry_version}",
        },
    }


@router.post("/scenes/{scene_id}/disable")
def disable_scene(
    scene_id: str,
    lifecycle: SceneLifecycleService = Depends(get_scene_service),
    service: ScenePublishService = Depends(get_publish_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        scene, registry_version = service.disable(scene_id)
    except ScenePublishError as exc:
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "message": exc.message}
        ) from exc
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": _summary(scene, lifecycle),
        "registry_version": f"reg_{registry_version}",
    }


@router.post("/scenes/{scene_id}/enable")
def enable_scene(
    scene_id: str,
    lifecycle: SceneLifecycleService = Depends(get_scene_service),
    service: ScenePublishService = Depends(get_publish_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        scene, snapshot, registry_version = service.enable(scene_id)
    except ScenePublishError as exc:
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "message": exc.message}
        ) from exc
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": _summary(scene, lifecycle),
        "snapshot": {"snapshot_id": snapshot.snapshot_id},
        "registry_version": f"reg_{registry_version}",
    }


@router.delete("/scenes/{scene_id}")
def delete_scene(
    scene_id: str,
    service: SceneLifecycleService = Depends(get_scene_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        scene = service.delete(scene_id)
    except SceneRepositoryError as exc:
        raise _error(exc) from exc
    return {
        "request_id": _request_id(),
        "status": "succeeded",
        "data": _summary(scene, service),
    }


__all__ = [
    "configure_sql_ask_data_services",
    "get_principal",
    "get_publish_service",
    "get_scene_service",
    "router",
]
