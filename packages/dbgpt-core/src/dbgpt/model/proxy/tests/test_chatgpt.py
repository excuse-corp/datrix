"""Tests for OpenAI-compatible proxy LLM client response handling."""

import asyncio
from types import SimpleNamespace

from dbgpt.core import ModelMessage, ModelRequest
from dbgpt.core.interface.message import ModelMessageRoleType
from dbgpt.model.proxy.llms.chatgpt import OpenAILLMClient


class _FakeCompletions:
    def __init__(self, response):
        self.response = response

    async def create(self, **kwargs):
        return self.response


class _FakeClient:
    default_headers = {}

    def __init__(self, response):
        self.chat = SimpleNamespace(completions=_FakeCompletions(response))


def _usage(data=None):
    return SimpleNamespace(dict=lambda: data or {})


def _request(model="qwen-test"):
    return ModelRequest(
        model=model,
        messages=[ModelMessage(role=ModelMessageRoleType.HUMAN, content="hi")],
    )


async def _async_iter(items):
    for item in items:
        yield item


def test_generate_accepts_reasoning_field_without_content():
    message = SimpleNamespace(content=None, model_extra={"reasoning": "thought"})
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=_usage(),
    )
    client = OpenAILLMClient(openai_client=_FakeClient(response), model="qwen-test")

    out = asyncio.run(client.generate(_request()))

    assert not out.has_text
    assert out.thinking_text == "thought"


def test_generate_stream_yields_reasoning_field_chunks():
    chunks = _async_iter(
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            model_extra={"reasoning": "think"},
                        )
                    )
                ],
                usage=None,
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=" answer",
                            model_extra={},
                        )
                    )
                ],
                usage=_usage({"total_tokens": 2}),
            ),
        ]
    )
    client = OpenAILLMClient(openai_client=_FakeClient(chunks), model="qwen-test")

    async def run():
        return [out async for out in client.generate_stream(_request())]

    outputs = asyncio.run(run())

    assert len(outputs) == 2
    assert outputs[0].thinking_text == "think"
    assert outputs[-1].thinking_text == "think"
    assert outputs[-1].text == " answer"
    assert outputs[-1].usage == {"total_tokens": 2}


def test_qwen38_defaults_to_xhigh_reasoning_effort():
    client = OpenAILLMClient(
        openai_client=_FakeClient(None), model="Qwen3.8-flash-next-fp8"
    )

    payload = client._build_request(_request("Qwen3.8-flash-next-fp8"))

    assert payload["extra_body"] == {
        "enable_thinking": True,
        "reasoning_effort": "xhigh",
    }


def test_qwen38_normalizes_unsupported_reasoning_effort_to_xhigh():
    for effort in ("high", "max"):
        client = OpenAILLMClient(
            openai_client=_FakeClient(None),
            model="Qwen/Qwen3.8-27B-FP8",
            reasoning_effort=effort,
        )

        payload = client._build_request(_request("Qwen/Qwen3.8-27B-FP8"))

        assert payload["extra_body"]["reasoning_effort"] == "xhigh"


def test_qwen38_disable_thinking_removes_reasoning_controls():
    client = OpenAILLMClient(
        openai_client=_FakeClient(None),
        model="Qwen3.8-flash-next-fp8",
        thinking_enabled=False,
        reasoning_effort="medium",
        thinking_budget=4096,
    )

    payload = client._build_request(_request("Qwen3.8-flash-next-fp8"))

    assert payload["extra_body"] == {"enable_thinking": False}


def test_qwen38_drops_thinking_budget_when_reasoning_effort_is_present():
    client = OpenAILLMClient(
        openai_client=_FakeClient(None),
        model="Qwen3.8-flash-next-fp8",
        openai_kwargs={
            "extra_body": {"reasoning_effort": "low", "thinking_budget": 4096}
        },
    )

    payload = client._build_request(_request("Qwen3.8-flash-next-fp8"))

    assert payload["extra_body"] == {
        "enable_thinking": True,
        "reasoning_effort": "low",
    }
