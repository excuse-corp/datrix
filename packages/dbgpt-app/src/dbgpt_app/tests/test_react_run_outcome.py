from types import SimpleNamespace

from dbgpt_app.openapi.api_v1 import agentic_data_api as api


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


def test_react_max_steps_defaults_to_100(monkeypatch):
    for key in (
        "DATAMAN_REACT_MAX_STEPS_HARD_LIMIT",
        "DATAMAN_REACT_MAX_STEPS_DEFAULT",
        "DATAMAN_REACT_MAX_STEPS_SKILL",
    ):
        monkeypatch.delenv(key, raising=False)

    assert api._react_max_steps_for("normal task", None) == 100
    assert api._react_max_steps_for("优化 data-report 技能", None) == 100


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


def test_terminate_with_open_plan_is_incomplete():
    outcome = api._compute_react_run_outcome(
        _reply(action_report=_action_report(terminate=True)),
        history_steps=[{"status": "done"}],
        todo_list=[{"status": "in_progress"}],
        max_steps=12,
    )

    assert outcome["status"] == api.REACT_STATUS_INCOMPLETE
    assert outcome["termination_reason"] == api.REACT_REASON_TASK_PLAN_INCOMPLETE


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
