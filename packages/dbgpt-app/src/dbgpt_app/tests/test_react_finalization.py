import json
from types import SimpleNamespace

import pytest

from dbgpt_app.openapi.api_v1.agentic_data_api import _resolve_llm_model_name
from dbgpt_app.openapi.api_v1.react_finalization import (
    build_react_final_answer_request,
)


def test_final_answer_request_uses_xhigh_stream_and_completed_evidence():
    request = build_react_final_answer_request(
        model="Qwen3.8-27B-FP8",
        user_request="项目情况如何？",
        answer_draft="找到一个项目。",
        history_steps=[
            {
                "title": "ask_data_query",
                "status": "done",
                "thought": "internal intermediate reasoning",
                "outputs": [
                    {"output_type": "text", "content": "项目名称：平台升级"}
                ],
            },
            {
                "title": "failed step",
                "status": "failed",
                "outputs": [{"output_type": "text", "content": "ignore me"}],
            },
        ],
        temperature=0.2,
    )

    payload = json.loads(request.messages[-1].content)
    assert request.reasoning_effort == "xhigh"
    assert request.stream is True
    assert payload["user_request"] == "项目情况如何？"
    assert payload["answer_draft"] == "找到一个项目。"
    assert payload["completed_tool_evidence"] == [
        {
            "step": "ask_data_query",
            "outputs": [{"type": "text", "content": "项目名称：平台升级"}],
        }
    ]
    assert "internal intermediate reasoning" not in request.messages[-1].content


@pytest.mark.asyncio
async def test_resolve_llm_model_name_skips_lookup_for_selected_model():
    class FakeLLMClient:
        calls = 0

        async def models(self):
            self.calls += 1
            return [SimpleNamespace(model="fallback")]

    client = FakeLLMClient()

    assert await _resolve_llm_model_name(client, "selected") == "selected"
    assert client.calls == 0


@pytest.mark.asyncio
async def test_resolve_llm_model_name_uses_first_available_model_as_fallback():
    class FakeLLMClient:
        async def models(self):
            return [SimpleNamespace(model="fallback")]

    assert await _resolve_llm_model_name(FakeLLMClient(), None) == "fallback"
