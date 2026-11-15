import asyncio
from types import SimpleNamespace

from dbgpt.agent.util.llm.llm_client import AIWrapper
from dbgpt.agent.util.llm.llm import _build_model_request
from dbgpt.core import ModelMessage, ModelOutput, ModelRequest
from dbgpt.core.interface.message import ModelMessageRoleType
from dbgpt.model.proxy.llms.chatgpt import OpenAILLMClient
from dbgpt.model.utils.llm_utils import parse_model_request


class _FakeCompletions:
    async def create(self, **kwargs):
        return None


class _FakeClient:
    default_headers = {}

    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


def test_worker_request_preserves_context_extra_for_qwen_reasoning_effort():
    request = ModelRequest.build_request(
        "Qwen3.8-flash-next-fp8",
        messages=[ModelMessage(role=ModelMessageRoleType.HUMAN, content="query")],
        context={"extra": {"reasoning_effort": "medium"}},
    )

    worker_request = _build_model_request(request.to_dict())
    client = OpenAILLMClient(
        openai_client=_FakeClient(), model="Qwen3.8-flash-next-fp8"
    )

    payload = client._build_request(worker_request)

    assert worker_request.context.extra == {"reasoning_effort": "medium"}
    assert payload["extra_body"] == {
        "enable_thinking": True,
        "reasoning_effort": "medium",
    }


def test_worker_request_preserves_typed_reasoning_effort():
    request = ModelRequest.build_request(
        "Qwen3.8-flash-next-fp8",
        messages=[ModelMessage(role=ModelMessageRoleType.HUMAN, content="query")],
        reasoning_effort="medium",
    )

    worker_request = _build_model_request(request.to_dict())
    client = OpenAILLMClient(
        openai_client=_FakeClient(), model="Qwen3.8-flash-next-fp8"
    )
    payload = client._build_request(worker_request)

    assert worker_request.reasoning_effort == "medium"
    assert payload["extra_body"]["reasoning_effort"] == "medium"


def test_proxy_model_request_preserves_typed_reasoning_effort():
    request = ModelRequest.build_request(
        "Qwen3.8-27B-FP8",
        messages=[ModelMessage(role=ModelMessageRoleType.HUMAN, content="query")],
        reasoning_effort="medium",
    )

    worker_request = _build_model_request(request.to_dict())
    proxy_request = parse_model_request(
        worker_request.to_dict(), "Qwen3.8-27B-FP8", stream=True
    )
    client = OpenAILLMClient(openai_client=_FakeClient(), model="Qwen3.8-27B-FP8")

    payload = client._build_request(proxy_request, stream=True)

    assert proxy_request.reasoning_effort == "medium"
    assert payload["extra_body"] == {
        "enable_thinking": True,
        "reasoning_effort": "medium",
    }


def test_unspecified_proxy_model_effort_uses_qwen38_default_xhigh():
    request = ModelRequest.build_request(
        "Qwen3.8-flash-next-fp8",
        messages=[ModelMessage(role=ModelMessageRoleType.HUMAN, content="route")],
    )

    worker_request = _build_model_request(request.to_dict())
    proxy_request = parse_model_request(
        worker_request.to_dict(), "Qwen3.8-flash-next-fp8", stream=True
    )
    payload = OpenAILLMClient(
        openai_client=_FakeClient(), model="Qwen3.8-flash-next-fp8"
    )._build_request(proxy_request, stream=True)

    assert proxy_request.reasoning_effort is None
    assert payload["extra_body"] == {
        "enable_thinking": True,
        "reasoning_effort": "xhigh",
    }


def test_agent_llm_wrapper_forwards_xhigh_with_streaming_enabled():
    class _RecordingLLMClient:
        def __init__(self):
            self.requests = []

        async def generate_stream(self, request):
            self.requests.append(request)
            yield ModelOutput(error_code=0, text="done")

    llm_client = _RecordingLLMClient()
    wrapper = AIWrapper(llm_client)

    async def run():
        return await wrapper.create(
            messages=[{"role": "user", "content": "query"}],
            llm_model="Qwen3.8-flash-next-fp8",
            temperature=0.2,
            max_new_tokens=128,
            reasoning_effort="xhigh",
        )

    result = asyncio.run(run())

    assert result == "done"
    agent_request = llm_client.requests[0]
    worker_request = _build_model_request(agent_request.to_dict())
    proxy_request = parse_model_request(
        worker_request.to_dict(), "Qwen3.8-flash-next-fp8", stream=True
    )
    payload = OpenAILLMClient(
        openai_client=_FakeClient(), model="Qwen3.8-flash-next-fp8"
    )._build_request(proxy_request, stream=True)

    assert agent_request.reasoning_effort == "xhigh"
    assert payload["stream"] is True
    assert payload["extra_body"] == {
        "enable_thinking": True,
        "reasoning_effort": "xhigh",
    }
