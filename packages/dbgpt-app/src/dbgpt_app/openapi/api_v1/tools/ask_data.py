"""AskData tools exposed to the generic ReAct chat agent."""

# ruff: noqa: E501, I001

from __future__ import annotations

import json
import uuid
from asyncio import Semaphore, gather
from time import monotonic
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from dbgpt.agent.resource.tool.base import tool

from dbgpt_app.scene.ask_data.security import AskDataPrincipal

if TYPE_CHECKING:
    from dbgpt_app.scene.ask_data.agents import SceneQueryAgent
    from dbgpt_app.scene.ask_data.schemas.plan import MainAgentPlan


_ASK_DATA_RESULT_ANALYSIS_GUIDANCE = [
    "先直接回答用户问题，再解释关键数字的业务含义。",
    "结合 ontology_context_for_analysis 中的实体、指标含义、指标关系和分析规则分析结果。",
    "只分析用户明确询问的范围；不要为了补充未要求的字段再次查询。",
    "存在空值、零值、极端值或查询警告时，提示可能的数据质量、口径或业务风险。",
    "只基于查询结果给结论；未命中的场景只能作为后续分析方向，不要当作事实。",
]


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
    del question
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
    return "\n".join(lines)


def _duration_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))


def _scene_display_name(scene_id: str, snapshot: Any | None = None) -> str:
    """Return the user-facing scene name for progress events."""
    if snapshot is not None:
        projection = getattr(snapshot, "routing_projection", {}) or {}
        if isinstance(projection, dict):
            for key in ("name", "scene_name", "display_name"):
                value = str(projection.get(key) or "").strip()
                if value:
                    return value
    try:
        from dbgpt_app.scene.ask_data.api import scenes

        scene = scenes._repository.get_scene(scene_id, include_deleted=True)
        value = str(getattr(scene, "name", "") or "").strip()
        if value:
            return value
    except Exception:
        pass
    return scene_id


def _scene_id_from_task_id(task_id: str) -> str:
    if task_id.startswith("route_scene_"):
        return task_id.removeprefix("route_")
    if task_id.startswith("route_"):
        return task_id.removeprefix("route_")
    return task_id


def _normalize_ask_data_question(question: str) -> str:
    return " ".join(question.split())


def _ask_data_idempotency_key(
    react_state: dict[str, Any], user_id: str, normalized_question: str
) -> str:
    conversation_id = str(react_state.get("conv_id") or "anonymous")
    turn_id = str(react_state.get("turn_id") or "unknown-turn")
    fingerprint = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{user_id}\n{conversation_id}\n{turn_id}\n{normalized_question}",
    ).hex
    return f"react-{fingerprint}"


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

    successful_results: dict[str, dict[str, Any]] = {}

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

        normalized_question = _normalize_ask_data_question(question)
        if not normalized_question:
            return json.dumps(
                {
                    "chunks": [
                        {"output_type": "text", "content": "请提供需要查询的业务问题。"}
                    ]
                },
                ensure_ascii=False,
            )

        cached = successful_results.get(normalized_question)
        if cached is not None:
            reused_content = dict(cached["content"])
            reused_content.update(
                {
                    "reused": True,
                    "query_complete": True,
                    "next_action": (
                        "相同问题已在本轮成功查询。直接使用当前结果回答用户，"
                        "不要再次调用 ask_data_query。"
                    ),
                }
            )
            await stream_callback(
                "ask_data.reused",
                {
                    "call_id": cached["call_id"],
                    "query_id": cached.get("query_id"),
                    "question": normalized_question,
                    "status": "reused",
                    "title": "复用本轮业务查询结果",
                    "detail": "相同问题已成功查询，不再生成或执行 SQL",
                },
            )
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": json.dumps(
                                reused_content, ensure_ascii=False, default=str
                            ),
                        }
                    ]
                },
                ensure_ascii=False,
            )

        call_id = f"call_{uuid.uuid4().hex}"

        async def call_stream_callback(
            event_type: str, payload: dict[str, Any]
        ) -> None:
            await stream_callback(event_type, {"call_id": call_id, **payload})

        await call_stream_callback(
            "ask_data.started",
            {
                "question": normalized_question,
                "status": "running",
                "title": "业务问数",
                "detail": normalized_question,
            },
        )
        routing_started = monotonic()
        await call_stream_callback(
            "ask_data.stage",
            {
                "stage": "routing",
                "title": "正在语义选择业务场景",
                "detail": "基于用户问题和场景 Snapshot 进行判断",
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
            routing_duration_ms = _duration_ms(routing_started)
            await call_stream_callback(
                "ask_data.stage",
                {
                    "stage": "routing",
                    "title": "业务场景语义选择完成",
                    "detail": "基于用户问题和场景 Snapshot 完成判断",
                    "status": "done",
                    "duration_ms": routing_duration_ms,
                },
            )
            if plan.action == "execute":
                planning_started = monotonic()
                planning_detail = "、".join(
                    _scene_display_name(task.scene_id) for task in plan.tasks
                )
                await call_stream_callback(
                    "ask_data.stage",
                    {
                        "stage": "planning",
                        "title": "已选中业务场景",
                        "detail": planning_detail,
                        "status": "done",
                        "duration_ms": _duration_ms(planning_started),
                    },
                )
            sql_by_task = await _build_scene_sqls(
                plan,
                scene_query_agent,
                stream_callback=call_stream_callback,
                max_parallel_scene_agents=_scene_agent_parallelism(
                    scenes, max_parallel_scene_agents
                ),
            )
            await call_stream_callback(
                "ask_data.stage",
                {
                    "stage": "querying",
                    "title": "正在执行场景 SQL",
                    "detail": f"并发执行 {len(sql_by_task)} 个场景",
                },
            )
            query_started = monotonic()
            query, result = await scenes.get_run_service().execute(
                question=normalized_question,
                plan=plan,
                user_id=principal.user_id,
                request_id=f"react_{uuid.uuid4().hex}",
                conversation_id=react_state.get("conv_id"),
                idempotency_key=_ask_data_idempotency_key(
                    react_state, principal.user_id, normalized_question
                ),
                engine_resolver=scenes._default_engine_resolver,
                max_agents=10,
                entry_type="main_agent",
                sql_by_task=sql_by_task,
            )
            payload = scenes._query_response(query, result, plan)
            payload["querying_duration_ms"] = _duration_ms(query_started)
        except Exception as exc:
            payload = {
                "status": "failed",
                "errors": [{"code": "ASK_DATA_EXECUTION_FAILED", "message": str(exc)}],
            }

        payload["call_id"] = call_id
        await _emit_query_outcome_stages(payload, call_stream_callback)
        await call_stream_callback("ask_data.result", payload)
        clarification = payload.get("clarification")
        if payload.get("status") == "clarification_required" and clarification:
            react_state["ask_data_pending_query_id"] = payload.get("query_id")
            await call_stream_callback(
                "ask_data.clarification",
                {"query_id": payload.get("query_id"), "clarification": clarification},
            )
            content = clarification.get("question") or "需要补充业务查询条件。"
        elif payload.get("status") in {"succeeded", "partial_succeeded"}:
            content = _main_agent_result_content(
                payload, max_result_tokens, normalized_question
            )
        else:
            error = (payload.get("errors") or [{}])[0]
            content = error.get("message") or "业务查询未能完成。"

        if payload.get("status") == "succeeded":
            try:
                cached_content = json.loads(content)
            except Exception:
                cached_content = None
            if isinstance(cached_content, dict):
                successful_results[normalized_question] = {
                    "call_id": call_id,
                    "query_id": payload.get("query_id"),
                    "content": cached_content,
                }
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
    from dbgpt_app.scene.ask_data.schemas.plan import CombinePlan, MainAgentPlan, PlanTask
    from dbgpt_app.scene.ask_data.snapshot.runtime import query_metrics

    normalized_question = question.strip()
    if not normalized_question:
        return MainAgentPlan(
            action="reject",
            reason_code="EMPTY_QUESTION",
            message="Question is empty",
        )
    if scene_query_agent is None:
        return MainAgentPlan(
            action="reject",
            reason_code="SCENE_ROUTER_UNAVAILABLE",
            message="场景路由 Agent 暂时不可用。",
        )

    snapshots = _visible_active_snapshots(principal)
    if not snapshots:
        return MainAgentPlan(
            action="reject",
            reason_code="NO_MATCHING_SCENE",
            message="No active Scene matches the question",
        )
    try:
        scene_ids = await scene_query_agent.select_scenes(
            snapshots, normalized_question, max_scenes=max_scenes
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
        metrics = query_metrics(snapshot)
        metric_key = (
            metrics[0].get("key")
            if metrics and isinstance(metrics[0], dict) and metrics[0].get("key")
            else "count_rows"
        )
        tasks.append(
            PlanTask(
                task_id=f"route_{scene_id}",
                scene_id=scene_id,
                question=normalized_question,
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
        task_started = monotonic()
        snapshot = scenes._snapshot_service.active(task.scene_id)
        if snapshot is None:
            raise ValueError("SCENE_NOT_ACTIVE")
        scene_name = _scene_display_name(task.scene_id, snapshot)
        await stream_callback(
            "ask_data.stage",
            {
                "stage": f"subagent-{task.scene_id}",
                "title": f"场景子 Agent：{scene_name}",
                "detail": "读取数据字典和业务语义文档",
                "scene_id": task.scene_id,
                "scene_name": scene_name,
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
                    "title": f"SQL 生成成功：{scene_name}",
                    "detail": "已通过绑定表/视图校验",
                    "status": "done",
                    "duration_ms": _duration_ms(task_started),
                    "scene_id": task.scene_id,
                    "scene_name": scene_name,
                },
            )
            return task.task_id, sql
        except Exception as exc:
            await stream_callback(
                "ask_data.stage",
                {
                    "stage": f"sql-gen-{task.scene_id}",
                    "title": f"SQL 生成失败：{scene_name}",
                    "detail": str(exc),
                    "status": "failed",
                    "duration_ms": _duration_ms(task_started),
                    "scene_id": task.scene_id,
                    "scene_name": scene_name,
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
        scene_name = _scene_display_name(str(scene_id))
        await stream_callback(
            "ask_data.stage",
            {
                "stage": f"sql-exec-{scene_id}",
                "title": f"SQL 执行成功：{scene_name}",
                "detail": f"{result.get('row_count', 0)} 行",
                "status": "done",
                "duration_ms": result.get("duration_ms"),
                "scene_id": scene_id,
                "scene_name": scene_name,
            },
        )
    for error in payload.get("errors") or []:
        task_id = error.get("task_id") or "unknown"
        scene_id = error.get("scene_id") or _scene_id_from_task_id(str(task_id))
        scene_name = _scene_display_name(str(scene_id))
        await stream_callback(
            "ask_data.stage",
            {
                "stage": f"sql-exec-{task_id}",
                "title": f"SQL 执行失败：{scene_name}",
                "detail": error.get("message") or error.get("code") or "查询失败",
                "status": "failed",
                "duration_ms": error.get("duration_ms")
                or payload.get("querying_duration_ms"),
                "scene_id": scene_id,
                "scene_name": scene_name,
            },
        )


def _main_agent_result_content(
    payload: dict[str, Any], max_result_tokens: int | None, question: str | None = None
) -> str:
    scene_items = [item for item in payload.get("results", []) if isinstance(item, dict)]
    scene_ids = [
        str(item.get("scene_id"))
        for item in scene_items
        if item.get("scene_id")
    ]
    result_columns = [
        column
        for item in scene_items
        for column in item.get("columns", [])
        if isinstance(column, dict)
    ]
    compact = {
        "status": payload.get("status"),
        "query_id": payload.get("query_id"),
        "call_id": payload.get("call_id"),
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
            for item in scene_items
        ],
        "errors": payload.get("errors", []),
    }
    if payload.get("status") == "succeeded":
        compact["query_complete"] = True
        compact["next_action"] = (
            "本次业务查询已成功完成。直接基于这些结果回答用户；"
            "不要用相同问题再次调用 ask_data_query。"
        )
    ontology_context = _active_ontology_analysis_context(
        question,
        scene_ids=scene_ids,
        result_columns=result_columns,
    )
    if ontology_context:
        compact["ontology_context_for_analysis"] = ontology_context
        compact["analysis_guidance_for_final_answer"] = (
            _ASK_DATA_RESULT_ANALYSIS_GUIDANCE
        )
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
        "call_id": compact.get("call_id"),
        "query_complete": compact.get("query_complete"),
        "next_action": compact.get("next_action"),
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
    if ontology_context:
        minimal["ontology_context_for_analysis"] = ontology_context
        minimal["analysis_guidance_for_final_answer"] = (
            _ASK_DATA_RESULT_ANALYSIS_GUIDANCE
        )
    encoded = json.dumps(minimal, ensure_ascii=False, default=str)
    if len(encoded) <= max_chars:
        return encoded
    minimal["scene_summaries"] = []
    return json.dumps(minimal, ensure_ascii=False, default=str)


def _active_ontology_analysis_context(
    question: str | None,
    *,
    scene_ids: list[str] | None = None,
    result_columns: list[dict[str, Any]] | None = None,
) -> str:
    try:
        from dbgpt_app.scene.ask_data.api.ontology import get_ontology_service
        from dbgpt_app.scene.ask_data.ontology.context import (
            render_main_agent_ontology_context,
        )

        return render_main_agent_ontology_context(
            get_ontology_service().active_snapshot(),
            question,
            scene_ids=scene_ids,
            result_columns=result_columns,
        )
    except Exception:
        return ""


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
