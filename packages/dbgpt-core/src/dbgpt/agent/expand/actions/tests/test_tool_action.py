import asyncio

from dbgpt.agent import ActionOutput
from dbgpt.agent.resource.tool.base import tool
from dbgpt.agent.resource.tool.pack import ToolPack

from ..tool_action import run_tool


@tool(description="Return a failed ActionOutput")
async def failing_tool() -> ActionOutput:
    return ActionOutput(
        is_exe_success=False,
        content="payload",
        observations="payload",
        error_type="tool_error",
        error_message="boom",
    )


def test_run_tool_propagates_action_output():
    resource = ToolPack([failing_tool._tool])

    result = asyncio.run(run_tool("failing_tool", {}, resource))

    assert result.is_exe_success is False
    assert result.content == "payload"
    assert result.observations == "payload"
    assert result.error_type == "tool_error"
    assert result.error_message == "boom"
    assert result.action == "failing_tool"
