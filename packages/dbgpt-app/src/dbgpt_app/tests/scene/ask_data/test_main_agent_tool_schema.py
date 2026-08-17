import json
from types import SimpleNamespace

import pytest

from dbgpt.agent.resource.tool.pack import ToolPack
from dbgpt_app.openapi.api_v1.tools import ask_data as ask_data_tools
from dbgpt_app.openapi.api_v1.tools.ask_data import (
    _ask_data_idempotency_key,
    _route_ask_data_plan,
    make_ask_data_tools,
)
from dbgpt_app.scene.ask_data.api import scenes
from dbgpt_app.scene.ask_data.security import AskDataPrincipal
from dbgpt_app.scene.ask_data.snapshot import InMemorySnapshotService
from dbgpt_app.scene.ask_data.testsupport import build_test_snapshot


async def _noop_stream_callback(_event_type, _payload):
    return None


def test_ask_data_query_public_schema_keeps_question_only():
    ask_data_query, _ = make_ask_data_tools(
        {},
        _noop_stream_callback,
        AskDataPrincipal(user_id="user-1"),
    )

    assert list(getattr(ask_data_query, "_tool").args.keys()) == ["question"]


def test_ask_data_query_accepts_legacy_query_action_input():
    ask_data_query, _ = make_ask_data_tools(
        {},
        _noop_stream_callback,
        AskDataPrincipal(user_id="user-1"),
    )
    tool_pack = ToolPack([ask_data_query])

    parsed = tool_pack.parse_execute_args(
        resource_name="ask_data_query",
        input_str='{"query": "任博寒有哪些信息化项目"}',
    )

    assert parsed == ((), {"question": "任博寒有哪些信息化项目"})


@pytest.mark.asyncio
async def test_ask_data_route_uses_llm_scene_selector(monkeypatch):
    snapshot_service = InMemorySnapshotService()
    snapshot = build_test_snapshot("contracts", "r1").model_copy(
        update={
            "routing_projection": {
                "name": "Contracts",
                "description": "Contracts analysis",
                "keywords": ["contracts"],
            }
        }
    )
    snapshot_service.save_ready(snapshot)
    snapshot_service.activate(snapshot.snapshot_id)
    monkeypatch.setattr(scenes, "_snapshot_service", snapshot_service)
    monkeypatch.setattr(scenes, "_router_agent", None)
    monkeypatch.setattr(scenes, "_authorizer", None)

    class FakeSceneQueryAgent:
        def __init__(self):
            self.calls = []

        async def select_scenes(self, snapshots, question, **kwargs):
            self.calls.append((snapshots, question, kwargs))
            return ["contracts"]

    scene_query_agent = FakeSceneQueryAgent()

    plan = await _route_ask_data_plan(
        "show contracts by department",
        principal=AskDataPrincipal(user_id="user-1"),
        scene_query_agent=scene_query_agent,
        max_scenes=10,
    )

    assert plan.action == "execute"
    assert [task.scene_id for task in plan.tasks] == ["contracts"]
    assert len(scene_query_agent.calls) == 1
    assert scene_query_agent.calls[0][1] == "show contracts by department"


@pytest.mark.asyncio
async def test_ask_data_route_rejects_when_llm_selector_is_unavailable():
    plan = await _route_ask_data_plan(
        "show contracts by department",
        principal=AskDataPrincipal(user_id="user-1"),
        scene_query_agent=None,
        max_scenes=10,
    )

    assert plan.action == "reject"
    assert plan.reason_code == "SCENE_ROUTER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_ask_data_query_reuses_success_in_same_turn_before_sql(monkeypatch):
    calls = {"route": 0, "build_sql": 0, "execute": 0}
    events = []
    plan = SimpleNamespace(
        action="execute", tasks=[SimpleNamespace(scene_id="projects")]
    )

    async def stream_callback(event_type, payload):
        events.append((event_type, payload))

    async def fake_route(*_args, **_kwargs):
        calls["route"] += 1
        return plan

    async def fake_build_sqls(*_args, **_kwargs):
        calls["build_sql"] += 1
        return {"task-projects": "SELECT project_name FROM projects"}

    class FakeRunService:
        async def execute(self, **kwargs):
            calls["execute"] += 1
            calls["idempotency_key"] = kwargs["idempotency_key"]
            return object(), object()

    monkeypatch.setattr(ask_data_tools, "_route_ask_data_plan", fake_route)
    monkeypatch.setattr(ask_data_tools, "_build_scene_sqls", fake_build_sqls)
    monkeypatch.setattr(
        ask_data_tools,
        "_active_ontology_analysis_context",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(scenes, "_require_plan_scene_access", lambda *_args: None)
    monkeypatch.setattr(scenes, "get_run_service", lambda: FakeRunService())
    monkeypatch.setattr(
        scenes,
        "_query_response",
        lambda *_args: {
            "status": "succeeded",
            "query_id": "qry_projects",
            "answer": "查询完成",
            "results": [
                {
                    "scene_id": "projects",
                    "status": "succeeded",
                    "row_count": 1,
                    "columns": [{"key": "project_name"}],
                    "rows": [{"project_name": "项目A"}],
                }
            ],
            "errors": [],
        },
    )

    react_state = {"conv_id": "conv-1", "turn_id": "turn-1"}
    query_tool, _ = make_ask_data_tools(
        react_state,
        stream_callback,
        AskDataPrincipal(user_id="user-1"),
        scene_query_agent=object(),
    )
    tool = getattr(query_tool, "_tool")

    first = await tool.async_execute(question=" 人工智能部门   有哪些项目 ")
    second = await tool.async_execute(question="人工智能部门 有哪些项目")

    first_payload = json.loads(json.loads(first)["chunks"][0]["content"])
    second_payload = json.loads(json.loads(second)["chunks"][0]["content"])
    assert calls["route"] == 1
    assert calls["build_sql"] == 1
    assert calls["execute"] == 1
    assert first_payload["query_complete"] is True
    assert second_payload["reused"] is True
    assert second_payload["call_id"] == first_payload["call_id"]
    assert calls["idempotency_key"] == _ask_data_idempotency_key(
        react_state, "user-1", "人工智能部门 有哪些项目"
    )
    stage_call_ids = {
        payload["call_id"]
        for event_type, payload in events
        if event_type == "ask_data.stage"
    }
    assert stage_call_ids == {first_payload["call_id"]}
    assert [event_type for event_type, _ in events].count("ask_data.reused") == 1


@pytest.mark.asyncio
async def test_ask_data_query_different_question_executes_again(monkeypatch):
    calls = {"execute": 0}
    plan = SimpleNamespace(action="execute", tasks=[])

    async def fake_route(*_args, **_kwargs):
        return plan

    async def fake_build_sqls(*_args, **_kwargs):
        return {}

    class FakeRunService:
        async def execute(self, **_kwargs):
            calls["execute"] += 1
            return object(), object()

    monkeypatch.setattr(ask_data_tools, "_route_ask_data_plan", fake_route)
    monkeypatch.setattr(ask_data_tools, "_build_scene_sqls", fake_build_sqls)
    monkeypatch.setattr(
        ask_data_tools,
        "_active_ontology_analysis_context",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(scenes, "_require_plan_scene_access", lambda *_args: None)
    monkeypatch.setattr(scenes, "get_run_service", lambda: FakeRunService())
    monkeypatch.setattr(
        scenes,
        "_query_response",
        lambda *_args: {
            "status": "succeeded",
            "query_id": f"qry_{calls['execute']}",
            "results": [],
            "errors": [],
        },
    )

    query_tool, _ = make_ask_data_tools(
        {"conv_id": "conv-1", "turn_id": "turn-1"},
        _noop_stream_callback,
        AskDataPrincipal(user_id="user-1"),
        scene_query_agent=object(),
    )
    tool = getattr(query_tool, "_tool")

    await tool.async_execute(question="人工智能部门有哪些项目")
    await tool.async_execute(question="人工智能部门付款阶段有哪些项目")

    assert calls["execute"] == 2
