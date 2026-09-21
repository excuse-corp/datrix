import asyncio
import json

from dbgpt.agent import ActionOutput
from dbgpt_app.openapi.api_v1.tools.code_interpreter import make_code_interpreter


def test_code_interpreter_runtime_error_is_failed_action_output():
    tool = make_code_interpreter(
        {"conv_id": "test-code-interpreter-runtime-error-is-failed"}
    )

    result = asyncio.run(tool("raise RuntimeError('boom')"))

    assert isinstance(result, ActionOutput)
    assert result.is_exe_success is False
    assert result.error_type == "tool_error"
    assert "RuntimeError" in (result.error_message or "")
    assert json.loads(result.content)["chunks"][-1]["output_type"] == "text"
