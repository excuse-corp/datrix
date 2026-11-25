import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from dbgpt_app.openapi.api_v1 import agentic_data_api as api


def test_react_run_registry_allows_parallel_conversations_and_serializes_one_session(
    monkeypatch,
):
    monkeypatch.setattr(api, "REACT_AGENT_RUNS", {})
    monkeypatch.setattr(api, "REACT_ACTIVE_RUNS_BY_SESSION", {})

    assert api._claim_react_run("run-a1", "alice", "conv-a") is None
    assert api._claim_react_run("run-b1", "alice", "conv-b") is None
    assert api._claim_react_run("run-other-user", "bob", "conv-a") is None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda run_id: api._claim_react_run(run_id, "alice", "conv-a"),
                ["run-a2", "run-a3"],
            )
        )

    assert results == ["run-a1", "run-a1"]
    assert api._release_react_run("run-a1") is not None
    assert api._claim_react_run("run-a4", "alice", "conv-a") is None
    assert api._release_react_run("run-a4") is not None
    assert api._release_react_run("run-b1") is not None
    assert api._release_react_run("run-other-user") is not None


def test_react_run_registry_rejects_run_id_collision(monkeypatch):
    monkeypatch.setattr(api, "REACT_AGENT_RUNS", {})
    monkeypatch.setattr(api, "REACT_ACTIVE_RUNS_BY_SESSION", {})

    assert api._claim_react_run("same-id", "alice", "conv-a") is None
    assert api._claim_react_run("same-id", "alice", "conv-b") == "same-id"
    api._release_react_run("same-id")


def test_chat_endpoint_returns_conflict_for_a_second_run_in_one_conversation(monkeypatch):
    from fastapi import FastAPI, HTTPException, Request

    async def empty_stream(*_args):
        yield "data: {}\n\n"

    monkeypatch.setattr(api, "REACT_AGENT_RUNS", {})
    monkeypatch.setattr(api, "REACT_ACTIVE_RUNS_BY_SESSION", {})
    monkeypatch.setattr(api, "_react_agent_stream", empty_stream)

    app = FastAPI()
    app.state.auth_required = False
    app.state.ask_data_principal_resolver = None
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/chat/react-agent",
            "headers": [],
            "query_string": b"",
            "app": app,
        }
    )
    user_token = SimpleNamespace(user_id="alice")
    first_dialogue = SimpleNamespace(
        conv_uid="conv-a",
        ext_info={"client_run_id": "run-a"},
        chat_mode="chat_react_agent",
        select_param=None,
        model_name=None,
        user_name=None,
    )
    second_dialogue = SimpleNamespace(
        conv_uid="conv-a",
        ext_info={"client_run_id": "run-b"},
        chat_mode="chat_react_agent",
        select_param=None,
        model_name=None,
        user_name=None,
    )

    first_response = asyncio.run(
        api.chat_react_agent(request, first_dialogue, user_token)
    )
    assert first_response.status_code == 200

    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(api.chat_react_agent(request, second_dialogue, user_token))

        assert error.value.status_code == 409
        assert error.value.detail["run_id"] == "run-a"
    finally:
        api._release_react_run("run-a")


def test_cancel_endpoint_only_cancels_the_requested_run(monkeypatch):
    from dbgpt_app.openapi.api_v1.tools.question_manager import question_manager

    cancel_event_a = asyncio.Event()
    cancel_event_b = asyncio.Event()
    monkeypatch.setattr(
        api,
        "REACT_AGENT_RUNS",
        {
            "run-a": {
                "conv_uid": "conv-a",
                "user_id": "alice",
                "cancel_event": cancel_event_a,
            },
            "run-b": {
                "conv_uid": "conv-a",
                "user_id": "alice",
                "cancel_event": cancel_event_b,
            },
        },
    )
    question_a = question_manager.create(
        "conv-a", [{"question": "A"}], user_id="alice", run_id="run-a"
    )
    question_b = question_manager.create(
        "conv-a", [{"question": "B"}], user_id="alice", run_id="run-b"
    )
    request = SimpleNamespace(
        state=SimpleNamespace(user=None),
        app=SimpleNamespace(
            state=SimpleNamespace(
                auth_required=False, ask_data_principal_resolver=None
            )
        ),
    )

    try:
        asyncio.run(
            api.chat_react_agent_cancel(
                request,
                api._ReactAgentCancelBody(conv_uid="conv-a", run_id="run-a"),
                SimpleNamespace(user_id="alice"),
            )
        )

        assert cancel_event_a.is_set()
        assert not cancel_event_b.is_set()
        assert question_a.rejected is True
        assert question_b.rejected is False
    finally:
        question_manager.remove(question_a.request_id)
        question_manager.remove(question_b.request_id)


def _reply(success=True, action_report=None, content=""):
    return SimpleNamespace(
        success=success,
        action_report=action_report,
        content=content,
    )


def _action_report(
    terminate=False,
    is_exe_success=True,
    error_type=None,
    error_message=None,
    content="",
):
    return SimpleNamespace(
        terminate=terminate,
        is_exe_success=is_exe_success,
        error_type=error_type,
        error_message=error_message,
        content=content,
    )


def test_non_terminate_at_max_steps_is_incomplete():
    outcome = api._compute_react_run_outcome(
        _reply(action_report=_action_report()),
        history_steps=[{"status": "done"}, {"status": "done"}],
        todo_list=[],
        max_steps=2,
    )

    assert outcome["status"] == api.REACT_STATUS_INCOMPLETE
    assert outcome["termination_reason"] == api.REACT_REASON_MAX_STEPS


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, True),
        ({"has_open_tasks": True}, False),
        ({"has_active_skill": True}, False),
        ({"cancelled": True}, False),
        ({"has_pending_query": True}, False),
        ({"evidence": {"scenes": []}}, False),
    ],
)
def test_direct_ask_data_finalization_only_hands_off_complete_simple_queries(
    changes, expected
):
    values = {
        "requested_call_id": "call-1",
        "evidence": {"scenes": [{"rows": [{"amount": 100}]}]},
        "cancelled": False,
        "has_open_tasks": False,
        "has_active_skill": False,
        "has_pending_query": False,
    }
    values.update(changes)
    output = SimpleNamespace(action="ask_data_query", is_exe_success=True)

    assert api._should_directly_finalize_ask_data(output, **values) is expected


def test_direct_ask_data_finalization_requires_an_explicit_reply_flag():
    output = SimpleNamespace(action="ask_data_query", is_exe_success=True)

    assert not api._should_directly_finalize_ask_data(
        output,
        requested_call_id=None,
        evidence={"scenes": [{"rows": [{"amount": 100}]}]},
        cancelled=False,
        has_open_tasks=False,
        has_active_skill=False,
        has_pending_query=False,
    )


def test_react_max_steps_defaults_to_100(monkeypatch):
    for key in (
        "DATAMAN_REACT_MAX_STEPS_HARD_LIMIT",
        "DATAMAN_REACT_MAX_STEPS_DEFAULT",
        "DATAMAN_REACT_MAX_STEPS_SKILL",
    ):
        monkeypatch.delenv(key, raising=False)

    assert api._react_max_steps_for("normal task", None) == 100
    assert api._react_max_steps_for("优化 data-report 技能", None) == 100


def test_react_stream_idle_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv("DATAMAN_REACT_STREAM_IDLE_TIMEOUT", "17")

    assert api._react_stream_idle_timeout() == 17


def test_react_parse_error_limit_defaults_to_one_correction_retry(monkeypatch):
    monkeypatch.delenv("DATAMAN_REACT_PARSE_ERROR_LIMIT", raising=False)

    assert api._react_parse_error_limit() == 2


def test_recovered_parse_error_keeps_original_failure_details():
    history = [
        {
            "id": "step-parse",
            "status": "failed",
            "error_type": api.REACT_REASON_PARSE_ERROR,
            "error_message": "No Action line found",
        },
        {
            "id": "step-tool",
            "status": "failed",
            "error_type": api.REACT_REASON_TOOL_ERROR,
            "error_message": "tool failed",
        },
    ]

    assert api._mark_recovered_parse_errors(history) == ["step-parse"]
    assert history[0]["status"] == "failed"
    assert history[0]["error_message"] == "No Action line found"
    assert history[0]["retry_resolved"] is True
    assert "retry_resolved" not in history[1]


def test_open_plan_at_max_steps_preserves_max_steps_reason():
    outcome = api._compute_react_run_outcome(
        _reply(action_report=_action_report()),
        history_steps=[{"status": "done"}, {"status": "done"}],
        todo_list=[{"status": "in_progress"}],
        max_steps=2,
    )

    assert outcome["status"] == api.REACT_STATUS_INCOMPLETE
    assert outcome["termination_reason"] == api.REACT_REASON_MAX_STEPS


def test_failed_llm_reply_is_failed_llm_error():
    outcome = api._compute_react_run_outcome(
        _reply(
            success=False,
            content="LLM Chat Generrate Error!(Connection error)",
        ),
        history_steps=[],
        todo_list=[],
        max_steps=12,
    )

    assert outcome["status"] == api.REACT_STATUS_FAILED
    assert outcome["termination_reason"] == api.REACT_REASON_LLM_ERROR


def test_parse_error_action_report_is_failed_parse_error():
    outcome = api._compute_react_run_outcome(
        _reply(
            success=False,
            action_report=_action_report(
                is_exe_success=False,
                error_type=api.REACT_REASON_PARSE_ERROR,
                error_message="No correct response found.",
            ),
        ),
        history_steps=[
            {"status": "failed", "error_type": api.REACT_REASON_PARSE_ERROR}
        ],
        todo_list=[],
        max_steps=12,
    )

    assert outcome["status"] == api.REACT_STATUS_FAILED
    assert outcome["termination_reason"] == api.REACT_REASON_PARSE_ERROR


def test_terminate_with_complete_plan_is_completed():
    outcome = api._compute_react_run_outcome(
        _reply(action_report=_action_report(terminate=True)),
        history_steps=[{"status": "done"}],
        todo_list=[{"status": "completed"}],
        max_steps=12,
    )

    assert outcome["status"] == api.REACT_STATUS_COMPLETED
    assert outcome["termination_reason"] == api.REACT_REASON_TERMINATE


def test_terminate_with_open_plan_is_completed():
    outcome = api._compute_react_run_outcome(
        _reply(action_report=_action_report(terminate=True)),
        history_steps=[{"status": "done"}],
        todo_list=[
            {"status": "completed"},
            {"status": "completed"},
            {"status": "completed"},
            {"status": "completed"},
            {"status": "completed"},
            {"status": "in_progress"},
        ],
        max_steps=12,
    )

    assert outcome["status"] == api.REACT_STATUS_COMPLETED
    assert outcome["termination_reason"] == api.REACT_REASON_TERMINATE


def test_stopped_without_terminate_is_incomplete_with_open_plan():
    outcome = api._compute_react_run_outcome(
        _reply(action_report=_action_report(terminate=False)),
        history_steps=[{"status": "done"}],
        todo_list=[{"status": "in_progress"}],
        max_steps=12,
    )

    assert outcome["status"] == api.REACT_STATUS_INCOMPLETE
    assert outcome["termination_reason"] == api.REACT_REASON_STOPPED_WITHOUT_TERMINATE


def test_task_plan_incomplete_keeps_partial_result_for_user():
    partial_result = "已整理丁宇龙的 3 个项目，另一人的项目尚未比较。"
    outcome = {
        "status": api.REACT_STATUS_INCOMPLETE,
        "termination_reason": api.REACT_REASON_TASK_PLAN_INCOMPLETE,
    }

    assert api._final_content_for_outcome(partial_result, outcome) == partial_result


def test_pending_ask_data_does_not_consume_active_task_continuation():
    pending = {"query_id": "query-1", "user_id": "user-1"}

    assert not api._should_handle_pending_ask_data(
        pending,
        "user-1",
        is_continuation=True,
        active_task={"task_id": "task-1"},
    )
    assert api._should_handle_pending_ask_data(
        pending,
        "user-1",
        is_continuation=False,
        active_task={"task_id": "task-1"},
    )
    assert not api._should_handle_pending_ask_data(
        pending,
        "another-user",
        is_continuation=False,
        active_task=None,
    )


def test_repeated_action_is_incomplete():
    outcome = api._compute_react_run_outcome(
        _reply(
            success=False,
            action_report=_action_report(
                is_exe_success=False,
                error_type=api.REACT_REASON_REPEATED_ACTION,
                error_message="Repeated the same action without producing new output.",
            ),
        ),
        history_steps=[
            {"status": "failed", "error_type": api.REACT_REASON_REPEATED_ACTION}
        ],
        todo_list=[],
        max_steps=12,
    )

    assert outcome["status"] == api.REACT_STATUS_INCOMPLETE
    assert outcome["termination_reason"] == api.REACT_REASON_REPEATED_ACTION
