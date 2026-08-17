from dbgpt.agent.resource.tool.pack import ToolPack
from dbgpt_app.openapi.api_v1.tools.ask_data import make_ask_data_tools
from dbgpt_app.scene.ask_data.security import AskDataPrincipal


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

