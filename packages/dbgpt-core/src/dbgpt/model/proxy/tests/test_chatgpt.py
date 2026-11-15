"""Tests for OpenAI-compatible proxy LLM client response handling."""

import asyncio
from types import SimpleNamespace

import pytest

from dbgpt.core import ModelMessage, ModelRequest
from dbgpt.core.interface.message import ModelMessageRoleType
from dbgpt.model.adapter.model_adapter import get_llm_model_adapter
from dbgpt.model.cluster.worker.default_worker import DefaultModelWorker
from dbgpt.model.proxy.llms.chatgpt import (
    OpenAILLMClient,
    chatgpt_generate,
)


class _FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
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


def test_non_stream_generate_uses_non_stream_request_and_preserves_effort():
    message = SimpleNamespace(content="SELECT 1", model_extra={})
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=None,
    )
    fake_client = _FakeClient(response)
    client = OpenAILLMClient(
        openai_client=fake_client,
        model="Qwen3.8-27B-FP8",
    )
    model = SimpleNamespace(proxy_llm_client=client)
    params = {
        "model": "Qwen3.8-27B-FP8",
        "messages": [{"role": "user", "content": "query"}],
        "reasoning_effort": "medium",
    }

    out = asyncio.run(chatgpt_generate(model, None, params, "cpu"))

    request = fake_client.chat.completions.requests[0]
    assert out.text == "SELECT 1"
    assert request["stream"] is False
    assert request["extra_body"] == {
        "enable_thinking": True,
        "reasoning_effort": "medium",
    }
    assert OpenAILLMClient.generate_function() is chatgpt_generate


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


def test_openai_proxy_adapter_selects_non_stream_generate_function():
    adapter = get_llm_model_adapter(
        "Qwen3.8-27B-FP8",
        use_fastchat=False,
        model_type="proxy/openai",
    )

    assert adapter.support_async()
    assert adapter.support_generate_function()
    assert adapter.get_async_generate_function(None, None) is chatgpt_generate


def test_default_worker_uses_async_non_stream_function_for_proxy_model():
    worker = DefaultModelWorker()
    worker.model_name = "Qwen3.8-27B-FP8"
    worker.model_path = None
    worker.model = object()
    worker.tokenizer = None
    worker.ml = SimpleNamespace(prompt_template=None)
    worker._model_params = SimpleNamespace(reasoning_model=False)
    worker.llm_adapter = get_llm_model_adapter(
        worker.model_name,
        use_fastchat=False,
        model_type="proxy/openai",
    )
    worker._support_async = True
    worker._support_generate_func = True

    _, _, generate_function, _ = worker._prepare_generate_stream(
        {
            "model": worker.model_name,
            "messages": [
                {"role": "user", "content": "select the matching scene"}
            ],
        },
        span_operation_name="test.scene_router",
        is_stream=False,
    )

    assert generate_function is chatgpt_generate


def test_qwen38_defaults_to_xhigh_reasoning_effort():
    client = OpenAILLMClient(
        openai_client=_FakeClient(None), model="Qwen3.8-flash-next-fp8"
    )

    payload = client._build_request(_request("Qwen3.8-flash-next-fp8"))

    assert payload["extra_body"] == {
        "enable_thinking": True,
        "reasoning_effort": "xhigh",
    }


def test_qwen38_request_context_overrides_reasoning_effort():
    client = OpenAILLMClient(
        openai_client=_FakeClient(None), model="Qwen3.8-flash-next-fp8"
    )

    request = ModelRequest.build_request(
        "Qwen3.8-flash-next-fp8",
        messages=[ModelMessage(role=ModelMessageRoleType.HUMAN, content="hi")],
        context={"extra": {"reasoning_effort": "medium"}},
    )

    payload = client._build_request(request)

    assert payload["extra_body"] == {
        "enable_thinking": True,
        "reasoning_effort": "medium",
    }


def test_qwen38_typed_request_effort_overrides_legacy_and_deployment_values():
    client = OpenAILLMClient(
        openai_client=_FakeClient(None),
        model="Qwen3.8-flash-next-fp8",
        reasoning_effort="low",
    )

    request = ModelRequest.build_request(
        "Qwen3.8-flash-next-fp8",
        messages=[ModelMessage(role=ModelMessageRoleType.HUMAN, content="hi")],
        reasoning_effort="xhigh",
        context={"extra": {"reasoning_effort": "medium"}},
    )

    payload = client._build_request(request)

    assert payload["extra_body"]["reasoning_effort"] == "xhigh"


def test_qwen38_uses_deployment_effort_before_platform_default():
    client = OpenAILLMClient(
        openai_client=_FakeClient(None),
        model="Qwen3.8-flash-next-fp8",
        reasoning_effort="medium",
    )

    payload = client._build_request(_request("Qwen3.8-flash-next-fp8"))

    assert payload["extra_body"]["reasoning_effort"] == "medium"


def test_qwen38_rejects_unknown_reasoning_effort():
    client = OpenAILLMClient(
        openai_client=_FakeClient(None), model="Qwen3.8-flash-next-fp8"
    )
    request = ModelRequest.build_request(
        "Qwen3.8-flash-next-fp8",
        messages=[ModelMessage(role=ModelMessageRoleType.HUMAN, content="hi")],
        reasoning_effort="ultra",
    )

    with pytest.raises(ValueError, match="Unsupported Qwen3.8 reasoning_effort"):
        client._build_request(request)


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
