"""AskData tools exposed to the generic ReAct chat agent."""

# ruff: noqa: E501, I001

from __future__ import annotations

import json
import logging
import uuid
from asyncio import CancelledError, Semaphore, gather
from datetime import datetime, timezone
from time import monotonic
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from dbgpt.agent.resource.tool.base import tool

from dbgpt_app.scene.ask_data.security import AskDataPrincipal

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from dbgpt_app.scene.ask_data.agents import SceneQueryAgent
    from dbgpt_app.scene.ask_data.schemas.plan import MainAgentPlan


_ASK_DATA_RESULT_ANALYSIS_GUIDANCE = [
    "先直接回答用户问题，再解释关键数字的业务含义。",
    "在查数结果后必须给出“数据口径”说明，使用普通中文说明数据来自哪里、每一行代表什么、数字怎么算、统计范围和需要注意的业务规则。",
    "数据口径说明只能依据 sql_explanation；缺失的内容明确说未识别，不要根据常识臆测。",
    "面向不懂数据库的用户表达，不要直接复述 SQL、表名、字段名、聚合、粒度等数据库术语；优先使用 sql_explanation 中已经翻译好的中文说明。",
    "结合 ontology_context_for_analysis 中的实体、指标含义、指标关系和分析规则分析结果。",
    "跨场景桥接只定义业务映射和分析条件，不代表本次查询已满足条件；必须先核对桥接两侧本次结果的时间依据、时间范围和统计粒度。条件未满足或无法确认时，不做直接比较或比值推断。",
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stage_node_id(call_id: str, stage: str, task_id: str | None = None) -> str:
    parts = ["ask-data", call_id, stage]
    if task_id:
        parts.append(task_id)
    return "-".join(
        "".join(char if char.isalnum() or char in "-_" else "_" for char in part)
        for part in parts
    )


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


def _task_id(task: Any) -> str:
    return str(getattr(task, "task_id", None) or getattr(task, "scene_id", "unknown"))


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

    Accept the historical ``query`` key and preserve the optional reply flag.
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
            args = {"question": str(question)}
            if payload.get("reply_after_query") is True:
                args["reply_after_query"] = True
            return (), args
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
            "部门和时间维度等问题。查询条件只接受自然语言，禁止传入 SQL、"
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
            },
            "reply_after_query": {
                "type": "boolean",
                "description": "本次查询覆盖用户完整问题，查完即可回复时设为 true。",
                "required": False,
            },
        },
    )
    async def ask_data_query(question: str, reply_after_query: bool = False) -> str:
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
            if reply_after_query is True:
                react_state["ask_data_reply_call_id"] = cached["call_id"]
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

        query_started = monotonic()
        query_started_at = _utc_now()

        async def emit_stage(
            stage: str,
            *,
            status: str,
            title: str,
            detail: str = "",
            started_at: str,
            started_monotonic: float,
            scope: str = "query",
            task_id: str | None = None,
            scene_id: str | None = None,
            scene_name: str | None = None,
            result: dict[str, Any] | None = None,
            error: dict[str, Any] | None = None,
            duration_ms: int | None = None,
        ) -> None:
            terminal = status != "running"
            event = {
                "schema_version": 2,
                "node_id": _stage_node_id(call_id, stage, task_id),
                "parent_node_id": _stage_node_id(call_id, "query")
                if stage != "query"
                else None,
                "stage": stage,
                "scope": scope,
                "task_id": task_id,
                "scene_id": scene_id,
                "scene_name": scene_name,
                "status": status,
                "title": title,
                "detail": detail,
                "started_at": started_at,
                "ended_at": _utc_now() if terminal else None,
                "duration_ms": (
                    duration_ms
                    if duration_ms is not None
                    else (_duration_ms(started_monotonic) if terminal else None)
                ),
                "result": result,
                "error": error,
            }
            await call_stream_callback("ask_data.stage", event)

        await call_stream_callback(
            "ask_data.started",
            {
                "question": normalized_question,
                "status": "running",
                "title": "业务问数",
                "detail": normalized_question,
            },
        )
        await emit_stage(
            "query",
            status="running",
            title="业务问数",
            detail=normalized_question,
            started_at=query_started_at,
            started_monotonic=query_started,
        )
        routing_started = monotonic()
        routing_started_at = _utc_now()
        await emit_stage(
            "routing",
            status="running",
            title="正在语义选择业务场景",
            detail="基于用户问题和场景 Snapshot 进行判断",
            started_at=routing_started_at,
            started_monotonic=routing_started,
        )
        plan = None
        routing_terminal = False
        formatting_started: float | None = None
        formatting_started_at: str | None = None
        try:
            plan = await _route_ask_data_plan(
                normalized_question,
                principal=principal,
                scene_query_agent=scene_query_agent,
                max_scenes=10,
            )
            scenes._require_plan_scene_access(principal, plan)
            routing_rejected = plan.action == "reject"
            selected_scene_names = list(
                dict.fromkeys(
                    _scene_display_name(str(task.scene_id)) for task in plan.tasks
                )
            )
            routing_detail = (
                f"已选择场景：{'、'.join(selected_scene_names)}"
                if selected_scene_names
                else "基于用户问题和场景 Snapshot 完成判断"
            )
            await emit_stage(
                "routing",
                status="failed" if routing_rejected else "done",
                title=(
                    "业务场景语义选择失败"
                    if routing_rejected
                    else "业务场景语义选择完成"
                ),
                detail=(
                    plan.message or "场景路由未能继续"
                    if routing_rejected
                    else routing_detail
                ),
                started_at=routing_started_at,
                started_monotonic=routing_started,
                result=(
                    None
                    if routing_rejected
                    else {"action": plan.action, "task_count": len(plan.tasks)}
                ),
                error=(
                    {
                        "code": plan.reason_code or "SCENE_ROUTING_REJECTED",
                        "message": plan.message or "场景路由未能继续",
                    }
                    if routing_rejected
                    else None
                ),
            )
            routing_terminal = True
            sql_by_task = await _build_scene_sqls(
                plan,
                scene_query_agent,
                stream_callback=call_stream_callback,
                max_parallel_scene_agents=_scene_agent_parallelism(
                    scenes, max_parallel_scene_agents
                ),
                call_id=call_id,
            )

            async def on_sql_task_event(event: dict[str, Any]) -> None:
                scene_id = str(event.get("scene_id") or "unknown")
                scene_name = _scene_display_name(scene_id)
                status = str(event.get("status") or "running")
                result = event.get("result") or {}
                error = event.get("error")
                if status == "running":
                    title = f"正在执行 SQL：{scene_name}"
                    detail = "正在查询业务数据"
                elif status == "done":
                    title = f"SQL 执行成功：{scene_name}"
                    detail = f"{result.get('row_count', 0)} 行"
                elif status == "cancelled":
                    title = f"SQL 执行已取消：{scene_name}"
                    detail = "查询已取消"
                else:
                    title = f"SQL 执行失败：{scene_name}"
                    detail = str((error or {}).get("message") or "查询失败")
                await emit_stage(
                    "sql_exec",
                    status=status,
                    title=title,
                    detail=detail,
                    started_at=str(event.get("started_at") or _utc_now()),
                    started_monotonic=query_started,
                    scope="task",
                    task_id=str(event.get("task_id") or "unknown"),
                    scene_id=scene_id,
                    scene_name=scene_name,
                    result=result or None,
                    error=error,
                    duration_ms=(
                        int(event["duration_ms"])
                        if isinstance(event.get("duration_ms"), (int, float))
                        else None
                    ),
                )

            combine_started = None

            async def on_combine_event(event: dict[str, Any]) -> None:
                nonlocal combine_started
                if combine_started is None:
                    combine_started = (_utc_now(), monotonic())
                status = event["status"]
                counts = event["result"]
                detail = {
                    "running": "正在汇集子任务结果",
                    "done": f"已汇集 {counts['result_count']}/{counts['task_count']} 个子任务的结果",
                    "failed": "结果合并失败",
                }
                await emit_stage(
                    "combine",
                    status=status,
                    title="合并查数结果",
                    detail=detail[status],
                    started_at=combine_started[0],
                    started_monotonic=combine_started[1],
                    result=counts,
                )

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
                on_task_event=on_sql_task_event,
                on_combine_event=on_combine_event,
                authorized_scene_ids=sorted(principal.scene_ids),
                is_platform_admin=principal.is_platform_admin,
                authz_version=principal.authz_version,
            )
            formatting_started = monotonic()
            formatting_started_at = _utc_now()
            await emit_stage(
                "formatting",
                status="running",
                title="正在整理查询结果",
                detail="正在构造查询响应",
                started_at=formatting_started_at,
                started_monotonic=formatting_started,
            )
            payload = scenes._query_response(query, result, plan)
        except CancelledError:
            if not routing_terminal:
                await emit_stage(
                    "routing",
                    status="cancelled",
                    title="业务场景语义选择已取消",
                    detail="查询已取消",
                    started_at=routing_started_at,
                    started_monotonic=routing_started,
                )
                routing_terminal = True
            if formatting_started is not None and formatting_started_at is not None:
                await emit_stage(
                    "formatting",
                    status="cancelled",
                    title="查询结果整理已取消",
                    detail="查询已取消",
                    started_at=formatting_started_at,
                    started_monotonic=formatting_started,
                )
            await emit_stage(
                "query",
                status="cancelled",
                title="业务问数已取消",
                detail="查询已取消",
                started_at=query_started_at,
                started_monotonic=query_started,
            )
            raise
        except Exception as exc:
            logger.warning(
                "AskData query failed call_id=%s stage=%s error_type=%s",
                call_id,
                "routing" if not routing_terminal else "execution",
                type(exc).__name__,
                exc_info=True,
            )
            if not routing_terminal:
                await emit_stage(
                    "routing",
                    status="failed",
                    title="业务场景语义选择失败",
                    detail=str(exc) or "场景路由失败",
                    started_at=routing_started_at,
                    started_monotonic=routing_started,
                    error={"code": type(exc).__name__, "message": str(exc)},
                )
                routing_terminal = True
            if formatting_started is not None and formatting_started_at is not None:
                await emit_stage(
                    "formatting",
                    status="failed",
                    title="查询结果整理失败",
                    detail=str(exc),
                    started_at=formatting_started_at,
                    started_monotonic=formatting_started,
                    error={"code": type(exc).__name__, "message": str(exc)},
                )
            payload = {
                "status": "failed",
                "errors": [{"code": "ASK_DATA_EXECUTION_FAILED", "message": str(exc)}],
            }

        payload["call_id"] = call_id
        clarification = payload.get("clarification")
        if payload.get("status") == "clarification_required" and clarification:
            react_state["ask_data_pending_query_id"] = payload.get("query_id")
            await call_stream_callback(
                "ask_data.clarification",
                {"query_id": payload.get("query_id"), "clarification": clarification},
            )
            content = clarification.get("question") or "需要补充业务查询条件。"
        elif payload.get("status") in {"succeeded", "partial_succeeded"}:
            try:
                evidence = _build_main_agent_result(
                    payload, normalized_question, principal=principal
                )
                content = _encode_main_agent_result(evidence, max_result_tokens)
                react_state.setdefault("ask_data_evidence", {})[call_id] = evidence
                if reply_after_query is True:
                    react_state["ask_data_reply_call_id"] = call_id
            except Exception as exc:
                payload["status"] = "failed"
                payload.setdefault("errors", []).append(
                    {"code": type(exc).__name__, "message": str(exc)}
                )
                content = str(exc) or "查询结果整理失败。"
                if formatting_started is not None and formatting_started_at is not None:
                    await emit_stage(
                        "formatting",
                        status="failed",
                        title="查询结果整理失败",
                        detail=content,
                        started_at=formatting_started_at,
                        started_monotonic=formatting_started,
                        error={"code": type(exc).__name__, "message": content},
                    )
        else:
            error = (payload.get("errors") or [{}])[0]
            content = error.get("message") or "业务查询未能完成。"

        if (
            formatting_started is not None
            and formatting_started_at is not None
            and payload.get("status") != "failed"
        ):
            await emit_stage(
                "formatting",
                status="done",
                title="查询结果整理完成",
                detail="已构造查询响应和业务答复内容",
                started_at=formatting_started_at,
                started_monotonic=formatting_started,
                result={
                    "result_count": len(payload.get("results") or []),
                    "error_count": len(payload.get("errors") or []),
                },
            )

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
        query_status = (
            "done"
            if payload.get("status")
            in {"succeeded", "partial_succeeded", "clarification_required"}
            else "failed"
        )
        await emit_stage(
            "query",
            status=query_status,
            title=(
                "业务问数失败"
                if query_status == "failed"
                else "需要补充查询条件"
                if payload.get("status") == "clarification_required"
                else "业务问数完成"
            ),
            detail=str(payload.get("status") or "completed"),
            started_at=query_started_at,
            started_monotonic=query_started,
            result={
                "query_id": payload.get("query_id"),
                "status": payload.get("status"),
                "result_count": len(payload.get("results") or []),
                "error_count": len(payload.get("errors") or []),
            },
            error=(payload.get("errors") or [None])[0]
            if query_status == "failed"
            else None,
        )
        await call_stream_callback("ask_data.result", payload)
        return json.dumps(
            {"chunks": [{"output_type": "text", "content": content}]},
            ensure_ascii=False,
        )

    # Do not expose `query` in the public tool schema.  This parser is an
    # execution-time compatibility shim for older prompts/model outputs only.
    getattr(
        ask_data_query, "_tool"
    )._parse_execute_args_func = _parse_ask_data_query_args

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
    from dbgpt_app.scene.ask_data.schemas.plan import (
        CombinePlan,
        MainAgentPlan,
        PlanTask,
    )
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
    call_id: str | None = None,
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
        task_started_at = _utc_now()
        scene_name = _scene_display_name(task.scene_id)
        task_id = _task_id(task)
        node_id = _stage_node_id(call_id or "internal", "sql_gen", task_id)

        async def emit(status: str, title: str, detail: str, **extra: Any) -> None:
            terminal = status != "running"
            await stream_callback(
                "ask_data.stage",
                {
                    **({"call_id": call_id} if call_id else {}),
                    "schema_version": 2,
                    "node_id": node_id,
                    "parent_node_id": _stage_node_id(call_id or "internal", "query"),
                    "stage": "sql_gen",
                    "scope": "task",
                    "task_id": task_id,
                    "scene_id": str(task.scene_id),
                    "scene_name": scene_name,
                    "status": status,
                    "title": title,
                    "detail": detail,
                    "started_at": task_started_at,
                    "ended_at": _utc_now() if terminal else None,
                    "duration_ms": _duration_ms(task_started) if terminal else None,
                    "result": None,
                    "error": None,
                    **extra,
                },
            )

        await emit(
            "running",
            f"正在生成 SQL：{scene_name}",
            "读取场景语义并生成查询语句",
        )
        try:
            snapshot = scenes._snapshot_service.active(task.scene_id)
            if snapshot is None:
                raise ValueError("SCENE_NOT_ACTIVE")
            scene_name = _scene_display_name(task.scene_id, snapshot)
            async with semaphore:
                schema = snapshot.runtime_config.get("schema", {})
                dialect = schema.get("dialect") if isinstance(schema, dict) else None
                sql = await scene_query_agent.build_sql(
                    snapshot, task.question, dialect=dialect
                )
            await emit(
                "done",
                f"SQL 生成成功：{scene_name}",
                "已通过绑定表/视图校验",
                scene_name=scene_name,
                result={"generated": True, "validated": True},
            )
            return task_id, sql
        except Exception as exc:
            await emit(
                "failed",
                f"SQL 生成失败：{scene_name}",
                str(exc),
                scene_name=scene_name,
                error={"code": type(exc).__name__, "message": str(exc)},
            )
            raise
        except CancelledError:
            await emit(
                "cancelled",
                f"SQL 生成已取消：{scene_name}",
                "查询已取消",
            )
            raise

    outcomes = await gather(
        *(build_task(task) for task in plan.tasks), return_exceptions=True
    )
    errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    if errors:
        raise errors[0]
    return dict(outcomes)


def _main_agent_result_content(
    payload: dict[str, Any],
    max_result_tokens: int | None,
    question: str | None = None,
    *,
    principal: AskDataPrincipal | None = None,
) -> str:
    return _encode_main_agent_result(
        _build_main_agent_result(payload, question, principal=principal),
        max_result_tokens,
    )


def _build_main_agent_result(
    payload: dict[str, Any],
    question: str | None,
    *,
    principal: AskDataPrincipal | None = None,
) -> dict[str, Any]:
    scene_items = [
        item for item in payload.get("results", []) if isinstance(item, dict)
    ]
    scene_ids = [
        str(item.get("scene_id")) for item in scene_items if item.get("scene_id")
    ]
    result_columns = [
        {
            **column,
            "scene_id": str(item.get("scene_id") or ""),
            **(
                {"scene_snapshot_id": str(item.get("snapshot_id"))}
                if item.get("snapshot_id")
                else {}
            ),
        }
        for item in scene_items
        for column in item.get("columns", [])
        if isinstance(column, dict)
    ]
    result_profiles = []
    result_lineage = []
    for item in scene_items:
        result_profiles.append(
            {
                "scene_id": str(item.get("scene_id") or ""),
                **(
                    {"scene_snapshot_id": str(item.get("snapshot_id"))}
                    if item.get("snapshot_id")
                    else {}
                ),
                "grain": item.get("grain") or [],
                "grain_semantic_keys": [
                    str(column.get("semantic_key") or column.get("key"))
                    for column in item.get("columns", [])
                    if isinstance(column, dict)
                    and str(column.get("type") or "").lower() == "dimension"
                    and (column.get("semantic_key") or column.get("key"))
                ],
                "time_grain": item.get("time_grain"),
                "time_basis": item.get("time_basis"),
                "time_range": item.get("time_range"),
            }
        )
        for lineage in item.get("field_lineage", []) or []:
            if not isinstance(lineage, dict):
                continue
            for result_key in lineage.get("result_keys", []) or []:
                result_lineage.append(
                    {
                        **lineage,
                        "result_key": str(result_key),
                        "scene_id": str(item.get("scene_id") or ""),
                        "scene_snapshot_id": str(item.get("snapshot_id") or ""),
                    }
                )
    compact = {
        "status": payload.get("status"),
        "query_id": payload.get("query_id"),
        "call_id": payload.get("call_id"),
        "question": question,
        "answer": payload.get("answer"),
        "scenes": [
            {
                "task_id": item.get("task_id"),
                "scene_id": item.get("scene_id"),
                "scene_name": _scene_display_name(str(item.get("scene_id") or "")),
                "scene_snapshot_id": item.get("snapshot_id"),
                "status": item.get("status"),
                "row_count": item.get("row_count"),
                "truncated": item.get("truncated"),
                "data_caliber": item.get("sql_explanation"),
                "time_basis": item.get("time_basis"),
                "time_range": item.get("time_range"),
                "grain": item.get("grain"),
                "columns": [
                    {
                        key: column.get(key)
                        for key in (
                            "key",
                            "type",
                            "semantic_key",
                            "source_field",
                            "aggregation",
                            "unit",
                            "label",
                        )
                        if column.get(key) is not None
                    }
                    for column in item.get("columns", [])
                    if isinstance(column, dict)
                ],
                "field_lineage": item.get("field_lineage", []),
                "rows": item.get("rows", []),
            }
            for item in scene_items
        ],
        "errors": payload.get("errors", []),
        "warnings": payload.get("warnings", []),
    }
    combined = payload.get("combined")
    if isinstance(combined, dict) and combined.get("mode") in {"compare", "derived"}:
        for scene in compact["scenes"]:
            scene["rows"] = []
            scene["columns"] = []
        compact["combined"] = combined
    if payload.get("status") == "succeeded":
        compact["query_complete"] = True
        compact["next_action"] = (
            "本次业务查询已成功完成。直接基于这些结果回答用户；"
            "不要用相同问题再次调用 ask_data_query。"
        )
    ontology_kwargs = {
        "scene_ids": scene_ids,
        "principal": principal,
        "result_columns": result_columns,
        "result_profiles": result_profiles,
    }
    if result_lineage:
        ontology_kwargs["result_lineage"] = result_lineage
    ontology_context = _active_ontology_analysis_context(question, **ontology_kwargs)
    has_data_caliber = any(
        isinstance(scene.get("data_caliber"), dict) for scene in compact["scenes"]
    )
    if ontology_context:
        compact["ontology_context_for_analysis"] = ontology_context
    if ontology_context or has_data_caliber:
        compact["analysis_guidance_for_final_answer"] = (
            _ASK_DATA_RESULT_ANALYSIS_GUIDANCE
        )
    return compact


def _encode_main_agent_result(
    evidence: dict[str, Any], max_result_tokens: int | None
) -> str:
    compact = {**evidence, "scenes": [dict(scene) for scene in evidence["scenes"]]}
    ontology_context = compact.get("ontology_context_for_analysis")
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
    combined = compact.get("combined")
    if combined:
        combined_rows = combined.get("rows") or []
        compact["combined"] = {
            **combined,
            "rows": combined_rows[:20],
        }
    else:
        combined_rows = []
    encoded = json.dumps(compact, ensure_ascii=False, default=str)
    while len(encoded) > max_chars and combined_rows:
        combined_rows = combined_rows[: len(combined_rows) // 2]
        compact["combined"]["rows"] = combined_rows
        encoded = json.dumps(compact, ensure_ascii=False, default=str)
    if len(encoded) <= max_chars:
        return encoded
    for scene in compact["scenes"]:
        rows = scene.get("rows") or []
        if isinstance(rows, list):
            scene["rows"] = rows[: max(1, min(len(rows), 20))]
    encoded = json.dumps(compact, ensure_ascii=False, default=str)
    while len(encoded) > max_chars and compact["scenes"]:
        largest = max(
            compact["scenes"],
            key=lambda item: len(
                json.dumps(item.get("rows", []), ensure_ascii=False, default=str)
            ),
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
                "data_caliber": scene.get("data_caliber"),
            }
            for scene in compact.get("scenes", [])
        ],
        "errors": compact.get("errors", []),
    }
    if ontology_context:
        minimal["ontology_context_for_analysis"] = ontology_context
    if ontology_context or any(
        isinstance(scene.get("data_caliber"), dict)
        for scene in minimal["scene_summaries"]
    ):
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
    principal: AskDataPrincipal | None = None,
    result_columns: list[dict[str, Any]] | None = None,
    result_lineage: list[dict[str, Any]] | None = None,
    result_profiles: list[dict[str, Any]] | None = None,
) -> str:
    try:
        from dbgpt_app.scene.ask_data.api.ontology import get_ontology_service
        from dbgpt_app.scene.ask_data.ontology.analysis_service import (
            OntologyAnalysisService,
        )

        ontology_service = get_ontology_service()
        ontology_snapshot, retrieval_source = ontology_service.analysis_snapshot_with_source(
            scene_ids=scene_ids,
            result_columns=result_columns,
            result_lineage=result_lineage,
            result_profiles=result_profiles,
            authorized_scene_ids=(
                sorted(principal.scene_ids) if principal is not None else []
            ),
            is_platform_admin=(
                principal.is_platform_admin if principal is not None else False
            ),
        )
        return OntologyAnalysisService().render(
            ontology_snapshot,
            question,
            scene_ids=scene_ids,
            authorized_scene_ids=(
                sorted(principal.scene_ids) if principal is not None else []
            ),
            is_platform_admin=(
                principal.is_platform_admin if principal is not None else False
            ),
            result_columns=result_columns,
            result_lineage=result_lineage,
            result_profiles=result_profiles,
            retrieval_source=retrieval_source,
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
            authorized_scene_ids=sorted(principal.scene_ids),
            is_platform_admin=principal.is_platform_admin,
            authz_version=principal.authz_version,
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
