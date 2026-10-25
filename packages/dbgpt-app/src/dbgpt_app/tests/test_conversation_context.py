import json

import pytest

from dbgpt.core.interface.message import HumanMessage, ViewMessage
from dbgpt_app.scene.conversation_context import build_conversation_context


class CharacterTokenCounter:
    async def count_token(self, model_name, text):
        return len(text)


def _react_result(final_content, tool_result, round_index):
    payload = {
        "type": "react-agent",
        "status": "completed",
        "final_content": final_content,
        "steps": [
            {
                "action": "ask_data_query",
                "title": "查询业务数据",
                "status": "done",
                "outputs": [
                    {
                        "output_type": "text",
                        "content": json.dumps(
                            {
                                "chunks": [
                                    {
                                        "output_type": "text",
                                        "content": json.dumps(
                                            {
                                                "status": "succeeded",
                                                "answer": tool_result,
                                                "scenes": [
                                                    {
                                                        "rows": [
                                                            {
                                                                "project_name": "工具内部行明细"
                                                            }
                                                        ]
                                                    }
                                                ],
                                            },
                                            ensure_ascii=False,
                                        ),
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
            }
        ],
    }
    return ViewMessage(
        content=json.dumps(payload, ensure_ascii=False),
        round_index=round_index,
    )


@pytest.mark.asyncio
async def test_context_uses_final_responses_not_tool_results_across_rounds():
    messages = [
        HumanMessage(content="丁宇龙有哪些项目？", round_index=1),
        _react_result(
            "丁宇龙最终答复：项目 A、项目 B。",
            "工具摘要：3 行结果。",
            1,
        ),
        HumanMessage(content="任博寒有哪些项目？", round_index=2),
        _react_result(
            "任博寒最终答复：项目 C、项目 D。",
            "工具摘要：5 行结果。",
            2,
        ),
    ]

    context = await build_conversation_context(
        messages,
        llm_client=CharacterTokenCounter(),
        model_name="test-model",
        max_tokens=4000,
    )

    assert "丁宇龙最终答复：项目 A、项目 B。" in context.recent_history_text
    assert "任博寒最终答复：项目 C、项目 D。" in context.recent_history_text
    assert "工具摘要" not in context.recent_history_text
    assert "工具内部行明细" not in context.recent_history_text
    assert "工具摘要" not in context.as_react_context()
    assert "工具内部行明细" not in context.as_react_context()

    chat_history = context.as_chat_history()
    assert len(chat_history) == 4
    assert chat_history[-1].content == "任博寒最终答复：项目 C、项目 D。"


@pytest.mark.asyncio
async def test_context_budget_keeps_newer_final_content_and_drops_older_first():
    messages = [
        HumanMessage(content="查询 A", round_index=1),
        _react_result("A" * 600, "内部工具结果 A", 1),
        HumanMessage(content="查询 B", round_index=2),
        _react_result("B" * 600, "内部工具结果 B", 2),
    ]

    context = await build_conversation_context(
        messages,
        llm_client=CharacterTokenCounter(),
        model_name="test-model",
        max_tokens=1000,
    )

    assert "A" * 600 not in context.recent_history_text
    assert "B" * 600 in context.recent_history_text
    assert "内部工具结果" not in context.recent_history_text
