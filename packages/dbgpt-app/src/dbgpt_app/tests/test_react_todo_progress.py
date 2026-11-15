import json

from dbgpt_app.openapi.api_v1.agentic_data_api import (
    _should_advance_todo_after_action,
)


def _rendered_html_observation():
    return json.dumps(
        {"chunks": [{"output_type": "html", "content": "<html>report</html>"}]}
    )


def test_html_render_does_not_complete_an_active_comparison_task():
    todos = [
        {"content": "计算并核对项目指标", "status": "in_progress"},
        {"content": "生成 HTML 对比报告", "status": "pending"},
    ]

    assert not _should_advance_todo_after_action(
        todos, "html_interpreter", _rendered_html_observation()
    )


def test_successful_html_render_completes_an_active_report_task():
    todos = [{"content": "渲染 HTML 对比报告", "status": "in_progress"}]

    assert _should_advance_todo_after_action(
        todos, "html_interpreter", _rendered_html_observation()
    )


def test_html_error_observation_does_not_complete_report_task():
    todos = [{"content": "渲染 HTML 对比报告", "status": "in_progress"}]
    observation = json.dumps(
        {"chunks": [{"output_type": "text", "content": "File not found"}]}
    )

    assert not _should_advance_todo_after_action(
        todos, "html_interpreter", observation
    )


def test_unrelated_tool_cannot_complete_todo_from_next_step_text():
    todos = [
        {"content": "核对项目指标", "status": "in_progress"},
        {"content": "生成报告", "status": "pending"},
    ]

    assert not _should_advance_todo_after_action(
        todos,
        "code_interpreter",
        "下一步生成报告",
    )
