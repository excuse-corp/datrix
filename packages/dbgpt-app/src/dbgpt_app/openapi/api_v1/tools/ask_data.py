"""AskData tools exposed to the generic ReAct chat agent."""

# ruff: noqa: E501, I001

from __future__ import annotations

import json
import uuid
from asyncio import Semaphore, gather
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from dbgpt.agent.resource.tool.base import tool

from dbgpt_app.scene.ask_data.security import AskDataPrincipal

if TYPE_CHECKING:
    from dbgpt_app.scene.ask_data.agents import SceneQueryAgent
    from dbgpt_app.scene.ask_data.schemas.plan import MainAgentPlan


def _capability_items(principal: AskDataPrincipal) -> list[dict[str, Any]]:
    from dbgpt_app.scene.ask_data.api import scenes

    items: list[dict[str, Any]] = []
    authorizer = getattr(scenes, "_authorizer", None)
    for snapshot in scenes._snapshot_service.active_snapshots():
        if authorizer is not None and not principal.can_access_scene(snapshot.scene_id):
            continue
        projection = snapshot.routing_projection
        items.append(
            {
                "scene_id": snapshot.scene_id,
                "name": projection.get("name"),
                "description": projection.get("description"),
                "keywords": projection.get("keywords", []),
            }
        )
    return items


def ask_data_capability_summary(
    principal: AskDataPrincipal, question: str | None = None
) -> str:
    items = _capability_items(principal)
    if not items:
        return "当前用户没有可用的已发布业务问数场景。"
    lines = ["可用业务问数场景介绍（只能通过 ask_data_query 查询）："]
    for item in items:
        introduction = str(item.get("description") or "").strip()
        lines.append(
            f"- {item['name'] or item['scene_id']}（{item['scene_id']}）："
            f"{introduction or '未填写场景介绍'}"
        )
    try:
        from dbgpt_app.scene.ask_data.api.ontology import get_ontology_service
        from dbgpt_app.scene.ask_data.ontology.context import (
            render_main_agent_ontology_context,
        )

        ontology_context = render_main_agent_ontology_context(
            get_ontology_service().active_snapshot(), question
        )
        if ontology_context:
            lines.extend(["", ontology_context])
    except Exception:
        # Ontology is additive context. A temporary metadata issue must never
        # hide already-published Scene capabilities.
        pass
    return "\n".join(lines)


def _parse_ask_data_query_args(input_str: str | None):
    """Normalize legacy AskData Action Input while keeping one canonical schema.

    The generic ReAct agent is text-protocol based.  If the model emits
    ``{"query": "..."}``, JSON parsing succeeds, so the normal single-argument
    fallback is never used; ToolPack then drops ``query`` because it is not in the
    registered schema and the call fails with a missing ``question`` argument.

    Keep ``question`` as the only public parameter, but accept the historical
    ``query`` key at execution time so one bad model token does not abort the
    business-data workflow.
    """
    if not input_str:
        return None
    raw = str(input_str).strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        # Preserve existing single-parameter fallback behavior for plain text.
        return (), {"question": raw}
    if isinstance(payload, dict):
        question = payload.get("question")
        if (question is None or not str(question).strip()) and "query" in payload:
            question = payload.get("query")
        if question is not None:
            return (), {"question": str(question)}
    if isinstance(payload, str):
        return (), {"question": payload}
    return None


def make_ask_data_tools(
    react_state: dict[str, Any],
    stream_callback: Callable[[str, dict[str, Any]], Awaitable[None]],
    principal: AskDataPrincipal,
    scene_query_agent: "SceneQueryAgent | None" = None,
    max_parallel_scene_agents: int = 5,
    max_result_tokens: int | None = None,
):
    """Build constrained AskData tools for a single generic-chat request."""

    @tool(
        description=(
            "查询已授权的业务数据场景。仅用于业务指标、统计、合同、项目、"
            "部门和时间维度等问题。参数只允许自然语言问题，禁止传入 SQL、"
            "数据源、表名、字段名、视图名或 Snapshot。"
        ),
        args={
            "question": {
                "type": "string",
                "description": (
                    "自然语言业务问题。必须使用 question 作为参数名；禁止传入 "
                    "SQL、表名、字段名、数据源名或 Snapshot ID。"
                ),
                "required": True,
            }
        },
    )
    async def ask_data_query(question: str) -> str:
        from dbgpt_app.scene.ask_data.api import scenes

        normalized_question = question.strip()
        if not normalized_question:
            return json.dumps(
                {
                    "chunks": [
                        {"output_type": "text", "content": "请提供需要查询的业务问题。"}
                    ]
                },
                ensure_ascii=False,
            )
        await stream_callback(
            "ask_data.stage",
            {
                "stage": "routing",
                "title": "正在匹配业务场景",
                "detail": "验证可用场景和权限",
            },
        )
        try:
            plan = await _route_ask_data_plan(
                normalized_question,
                principal=principal,
                scene_query_agent=scene_query_agent,
                max_scenes=10,
            )
            scenes._require_plan_scene_access(principal, plan)
            if plan.action == "execute":
                await stream_callback(
                    "ask_data.stage",
                    {
                        "stage": "planning",
                        "title": "已选中业务场景",
                        "detail": "、".join(task.scene_id for task in plan.tasks),
                        "status": "done",
                    },
                )
            sql_by_task = await _build_scene_sqls(
                plan,
                scene_query_agent,
                stream_callback=stream_callback,
                max_parallel_scene_agents=_scene_agent_parallelism(
                    scenes, max_parallel_scene_agents
                ),
            )
            await stream_callback(
                "ask_data.stage",
                {
                    "stage": "querying",
                    "title": "正在执行场景 SQL",
                    "detail": f"并发执行 {len(sql_by_task)} 个场景",
                },
            )
            query, result = await scenes.get_run_service().execute(
                question=normalized_question,
                plan=plan,
                user_id=principal.user_id,
                request_id=f"react_{uuid.uuid4().hex}",
                conversation_id=react_state.get("conv_id"),
                idempotency_key=f"react-{react_state.get('conv_id', 'anonymous')}-{uuid.uuid4().hex}",
                engine_resolver=scenes._default_engine_resolver,
                max_agents=10,
                entry_type="main_agent",
                sql_by_task=sql_by_task,
            )
            payload = scenes._query_response(query, result, plan)
        except Exception as exc:
            payload = {
                "status": "failed",
                "errors": [{"code": "ASK_DATA_EXECUTION_FAILED", "message": str(exc)}],
            }

        await _emit_query_outcome_stages(payload, stream_callback)
        await stream_callback("ask_data.result", payload)
        clarification = payload.get("clarification")
        if payload.get("status") == "clarification_required" and clarification:
            react_state["ask_data_pending_query_id"] = payload.get("query_id")
            await stream_callback(
                "ask_data.clarification",
                {"query_id": payload.get("query_id"), "clarification": clarification},
            )
            content = clarification.get("question") or "需要补充业务查询条件。"
        elif payload.get("status") in {"succeeded", "partial_succeeded"}:
            content = _main_agent_result_content(payload, max_result_tokens)
        else:
            error = (payload.get("errors") or [{}])[0]
            content = error.get("message") or "业务查询未能完成。"
        return json.dumps(
            {"chunks": [{"output_type": "text", "content": content}]},
            ensure_ascii=False,
        )

    # Do not expose `query` in the public tool schema.  This parser is an
    # execution-time compatibility shim for older prompts/model outputs only.
    getattr(ask_data_query, "_tool")._parse_execute_args_func = (
        _parse_ask_data_query_args
    )

    @tool(
        description=(
            "列出当前用户可访问的业务问数场景。只返回场景能力摘要，"
            "不返回数据库、SQL、表、字段或连接信息。"
        )
    )
    def ask_data_capabilities() -> str:
        return json.dumps(
            {
                "chunks": [
                    {
                        "output_type": "text",
                        "content": ask_data_capability_summary(principal),
                    }
                ]
            },
            ensure_ascii=False,
        )

    return ask_data_query, ask_data_capabilities


async def _route_ask_data_plan(
    question: str,
    *,
    principal: AskDataPrincipal,
    scene_query_agent: "SceneQueryAgent | None",
    max_scenes: int,
) -> "MainAgentPlan":
    from dbgpt_app.scene.ask_data.api import scenes
    from dbgpt_app.scene.ask_data.schemas.plan import CombinePlan, MainAgentPlan, PlanTask

    if scene_query_agent is None:
        return scenes.get_router_agent().route(question)

    snapshots = _visible_active_snapshots(principal)
    if not snapshots:
        return MainAgentPlan(
            action="reject",
            reason_code="NO_MATCHING_SCENE",
            message="No active Scene matches the question",
        )
    try:
        scene_ids = await scene_query_agent.select_scenes(
            snapshots, question, max_scenes=max_scenes
        )
    except Exception as exc:
        return MainAgentPlan(
            action="reject",
            reason_code="SCENE_ROUTER_UNAVAILABLE",
            message=f"Scene router failed: {exc}",
        )
    if not scene_ids:
        return MainAgentPlan(
            action="reject",
            reason_code="NO_MATCHING_SCENE",
            message="No active Scene matches the question",
        )
    snapshots_by_id = {snapshot.scene_id: snapshot for snapshot in snapshots}
    tasks = []
    for scene_id in scene_ids[:max_scenes]:
        snapshot = snapshots_by_id[scene_id]
        metrics = snapshot.runtime_config.get("metrics", [])
        metric_key = (
            metrics[0].get("key")
            if metrics and isinstance(metrics[0], dict) and metrics[0].get("key")
            else "count_rows"
        )
        tasks.append(
            PlanTask(
                task_id=f"route_{scene_id}",
                scene_id=scene_id,
                question=question,
                metrics=[metric_key],
            )
        )
    return MainAgentPlan(
        action="execute",
        combine=CombinePlan(mode="separate"),
        tasks=tasks,
    )


def _visible_active_snapshots(principal: AskDataPrincipal):
    from dbgpt_app.scene.ask_data.api import scenes

    authorizer = getattr(scenes, "_authorizer", None)
    snapshots = []
    for snapshot in scenes._snapshot_service.active_snapshots():
        if authorizer is not None and not principal.can_access_scene(snapshot.scene_id):
            continue
        snapshots.append(snapshot)
    return snapshots


async def _build_scene_sqls(
    plan: "MainAgentPlan",
    scene_query_agent: "SceneQueryAgent | None",
    *,
    stream_callback: Callable[[str, dict[str, Any]], Awaitable[None]],
    max_parallel_scene_agents: int = 5,
) -> dict[str, str]:
    if scene_query_agent is None or plan.action != "execute":
        return {}
    if len(plan.tasks) > 10:
        raise ValueError("TOO_MANY_TASKS")

    from dbgpt_app.scene.ask_data.api import scenes

    semaphore = Semaphore(max(1, min(max_parallel_scene_agents, 10)))

    async def build_task(task):
        snapshot = scenes._snapshot_service.active(task.scene_id)
        if snapshot is None:
            raise ValueError("SCENE_NOT_ACTIVE")
        await stream_callback(
            "ask_data.stage",
            {
                "stage": f"subagent-{task.scene_id}",
                "title": f"场景子 Agent：{task.scene_id}",
                "detail": "读取数据字典和业务语义文档",
            },
        )
        try:
            async with semaphore:
                schema = snapshot.runtime_config.get("schema", {})
                dialect = schema.get("dialect") if isinstance(schema, dict) else None
                sql = await scene_query_agent.build_sql(
                    snapshot, task.question, dialect=dialect
                )
            await stream_callback(
                "ask_data.stage",
                {
                    "stage": f"sql-gen-{task.scene_id}",
                    "title": f"SQL 生成成功：{task.scene_id}",
                    "detail": "已通过绑定表/视图校验",
                    "status": "done",
                },
            )
            return task.task_id, sql
        except Exception as exc:
            await stream_callback(
                "ask_data.stage",
                {
                    "stage": f"sql-gen-{task.scene_id}",
                    "title": f"SQL 生成失败：{task.scene_id}",
                    "detail": str(exc),
                    "status": "failed",
                },
            )
            raise

    pairs = await gather(*(build_task(task) for task in plan.tasks))
    return dict(pairs)


async def _emit_query_outcome_stages(
    payload: dict[str, Any],
    stream_callback: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> None:
    for result in payload.get("results") or []:
        scene_id = result.get("scene_id") or result.get("task_id") or "unknown"
        await stream_callback(
            "ask_data.stage",
            {
                "stage": f"sql-exec-{scene_id}",
                "title": f"SQL 执行成功：{scene_id}",
                "detail": f"{result.get('row_count', 0)} 行",
                "status": "done",
            },
        )
    for error in payload.get("errors") or []:
        task_id = error.get("task_id") or "unknown"
        await stream_callback(
            "ask_data.stage",
            {
                "stage": f"sql-exec-{task_id}",
                "title": f"SQL 执行失败：{task_id}",
                "detail": error.get("message") or error.get("code") or "查询失败",
                "status": "failed",
            },
        )


def _main_agent_result_content(
    payload: dict[str, Any], max_result_tokens: int | None
) -> str:
    compact = {
        "status": payload.get("status"),
        "query_id": payload.get("query_id"),
        "answer": payload.get("answer"),
        "scenes": [
            {
                "scene_id": item.get("scene_id"),
                "status": item.get("status"),
                "row_count": item.get("row_count"),
                "truncated": item.get("truncated"),
                "columns": [
                    column.get("key")
                    for column in item.get("columns", [])
                    if isinstance(column, dict)
                ],
                "rows": item.get("rows", []),
            }
            for item in payload.get("results", [])
            if isinstance(item, dict)
        ],
        "errors": payload.get("errors", []),
    }
    max_chars = max(1024, int(max_result_tokens or 0) * 4)
    if not max_result_tokens:
        return json.dumps(compact, ensure_ascii=False, default=str)
    encoded = json.dumps(compact, ensure_ascii=False, default=str)
    if len(encoded) <= max_chars:
        return encoded
    compact["truncated_for_main_agent"] = True
    compact["truncation_message"] = (
        "AskData result exceeded 50% of the configured context window; rows were truncated."
    )
    for scene in compact["scenes"]:
        rows = scene.get("rows") or []
        if isinstance(rows, list):
            scene["rows"] = rows[: max(1, min(len(rows), 20))]
    encoded = json.dumps(compact, ensure_ascii=False, default=str)
    while len(encoded) > max_chars and compact["scenes"]:
        largest = max(
            compact["scenes"],
            key=lambda item: len(json.dumps(item.get("rows", []), ensure_ascii=False, default=str)),
        )
        rows = largest.get("rows") or []
        if not isinstance(rows, list) or not rows:
            largest["columns"] = []
        else:
            largest["rows"] = rows[: max(0, len(rows) // 2)]
        encoded = json.dumps(compact, ensure_ascii=False, default=str)
        if all(not scene.get("rows") for scene in compact["scenes"]):
            break
    if len(encoded) <= max_chars:
        return encoded
    for scene in compact["scenes"]:
        scene["rows"] = []
        scene["columns"] = []
    encoded = json.dumps(compact, ensure_ascii=False, default=str)
    if len(encoded) <= max_chars:
        return encoded
    minimal = {
        "status": compact.get("status"),
        "query_id": compact.get("query_id"),
        "truncated_for_main_agent": True,
        "truncation_message": compact["truncation_message"],
        "scene_summaries": [
            {
                "scene_id": scene.get("scene_id"),
                "status": scene.get("status"),
                "row_count": scene.get("row_count"),
                "truncated": True,
            }
            for scene in compact.get("scenes", [])
        ],
        "errors": compact.get("errors", []),
    }
    encoded = json.dumps(minimal, ensure_ascii=False, default=str)
    if len(encoded) <= max_chars:
        return encoded
    minimal["scene_summaries"] = []
    return json.dumps(minimal, ensure_ascii=False, default=str)


async def reply_to_ask_data_query(
    *,
    query_id: str,
    answer: str,
    principal: AskDataPrincipal,
    conversation_id: str,
    scene_query_agent: "SceneQueryAgent | None" = None,
    max_parallel_scene_agents: int = 5,
) -> dict[str, Any]:
    """Continue an AskData clarification without re-entering generic ReAct."""
    from dbgpt_app.scene.ask_data.api import scenes

    existing = scenes.get_run_service().repository.get_query(query_id)
    if existing is None or existing.user_id != principal.user_id:
        return {
            "status": "failed",
            "errors": [{"code": "QUERY_NOT_FOUND", "message": "业务查询澄清已失效。"}],
        }
    if existing.status != "clarification_required" or existing.clarification_expired:
        return {
            "status": "failed",
            "errors": [
                {
                    "code": "QUERY_NOT_ACTIONABLE",
                    "message": "该业务查询当前不能继续澄清。",
                }
            ],
        }
    question = f"{existing.question}\n补充信息：{answer.strip()}"
    try:
        plan = await _route_ask_data_plan(
            question,
            principal=principal,
            scene_query_agent=scene_query_agent,
            max_scenes=10,
        )
        scenes._require_plan_scene_access(principal, plan)
        async def noop_stream(_event_type: str, _payload: dict[str, Any]) -> None:
            return None

        sql_by_task = await _build_scene_sqls(
            plan,
            scene_query_agent,
            stream_callback=noop_stream,
            max_parallel_scene_agents=_scene_agent_parallelism(
                scenes, max_parallel_scene_agents
            ),
        )
        query, result = await scenes.get_run_service().execute(
            question=question,
            plan=plan,
            user_id=principal.user_id,
            request_id=f"react_{uuid.uuid4().hex}",
            conversation_id=conversation_id,
            query_id=query_id,
            engine_resolver=scenes._default_engine_resolver,
            max_agents=10,
            entry_type="main_agent",
            sql_by_task=sql_by_task,
        )
        return scenes._query_response(query, result, plan)
    except Exception as exc:
        return {
            "status": "failed",
            "errors": [{"code": "ASK_DATA_REPLY_FAILED", "message": str(exc)}],
        }


async def _apply_scene_query_agent(
    plan: "MainAgentPlan",
    scene_query_agent: "SceneQueryAgent | None",
    *,
    max_parallel_scene_agents: int = 5,
) -> "MainAgentPlan":
    """Legacy QuerySpec path retained for explicit-plan compatibility tests."""
    if scene_query_agent is None or plan.action != "execute":
        return plan
    if len(plan.tasks) > 10:
        raise ValueError("TOO_MANY_TASKS")

    from dbgpt_app.scene.ask_data.api import scenes

    semaphore = Semaphore(max(1, min(max_parallel_scene_agents, 10)))

    async def build_task(task):
        snapshot = scenes._snapshot_service.active(task.scene_id)
        if snapshot is None:
            raise ValueError("SCENE_NOT_ACTIVE")
        async with semaphore:
            spec = await scene_query_agent.build_query_spec(snapshot, task.question)
        return task.model_copy(
            update={
                "metrics": spec.metrics,
                "dimensions": spec.dimensions,
                "filters": spec.filters,
                "time_range": spec.time_range,
                "time_grain": spec.time_grain,
                "named_filters": spec.named_filters,
                "order_by": [item.model_dump(mode="json") for item in spec.order_by],
                "limit": spec.limit,
            }
        )

    tasks = await gather(*(build_task(task) for task in plan.tasks))
    return plan.model_copy(update={"tasks": tasks})


def _scene_agent_parallelism(scenes: Any, fallback: int) -> int:
    """Keep LLM planning concurrency aligned with the query-task limit."""
    orchestrator = getattr(scenes.get_run_service(), "orchestrator", None)
    configured = getattr(orchestrator, "max_parallel_tasks", fallback)
    return max(1, min(int(configured), 10))


__all__ = [
    "ask_data_capability_summary",
    "make_ask_data_tools",
    "reply_to_ask_data_query",
]
