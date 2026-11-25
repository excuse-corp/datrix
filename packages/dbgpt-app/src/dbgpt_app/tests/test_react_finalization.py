import json
from types import SimpleNamespace

import pytest

from dbgpt_app.openapi.api_v1.agentic_data_api import _resolve_llm_model_name
from dbgpt_app.openapi.api_v1.react_finalization import (
    build_react_final_answer_request,
    render_ask_data_fallback,
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


def test_final_answer_receives_ask_data_and_ontology_without_a_draft():
    ask_data_evidence = [
        {
            "call_id": "call-1",
            "query_id": "query-1",
            "status": "succeeded",
            "question": "合同金额是多少？",
            "scenes": [
                {
                    "scene_id": "contracts",
                    "scene_name": "合同分析",
                    "status": "succeeded",
                    "row_count": 1,
                    "columns": [{"key": "contract_amount", "label": "合同金额"}],
                    "rows": [{"contract_amount": 100}],
                }
            ],
            "ontology_context_for_analysis": "合同金额指合同签订总额。",
            "analysis_guidance_for_final_answer": ["参考口径"],
            "errors": [],
            "warnings": [],
        }
    ]
    request = build_react_final_answer_request(
        model="selected",
        user_request="合同金额是多少？",
        answer_draft=None,
        history_steps=[
            {
                "title": "ask_data_query",
                "status": "done",
                "ask_data_call_id": "call-1",
                "outputs": [{"output_type": "text", "content": "duplicate result"}],
            }
        ],
        ask_data_evidence=ask_data_evidence,
    )

    payload = json.loads(request.messages[-1].content)
    assert "answer_draft" not in payload
    assert payload["ask_data_results"][0]["scenes"][0]["rows"] == [
        {"contract_amount": 100}
    ]
    assert payload["ontology_context_for_analysis"] == [
        {"query_id": "query-1", "context": "合同金额指合同签订总额。"}
    ]
    assert "结合 ontology_context_for_analysis" in payload[
        "analysis_guidance_for_final_answer"
    ][4]
    assert "duplicate result" not in request.messages[-1].content
    assert "analysis rules" in request.messages[0].content
    assert ask_data_evidence[0]["scenes"][0]["rows"] == [
        {"contract_amount": 100}
    ]


def test_ask_data_fallback_reports_partial_failures_and_scope():
    fallback = render_ask_data_fallback(
        [
            {
                "status": "partial_succeeded",
                "scenes": [
                    {
                        "scene_name": "合同分析",
                        "row_count": 1,
                        "columns": [{"key": "amount", "label": "合同金额"}],
                        "rows": [{"amount": 100}],
                        "data_caliber": {"summary": "按合同签订金额统计。"},
                    }
                ],
                "errors": [{"message": "发票场景查询失败。"}],
                "warnings": [],
            }
        ]
    )
    assert "合同金额：100" in fallback
    assert "按合同签订金额统计。" in fallback
    assert "部分子任务未返回结果" in fallback
    assert "发票场景查询失败。" in fallback


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
