"""Tests for ReAct agent model selection."""

import json
from types import SimpleNamespace

import pytest

from dbgpt.core import LLMClient
from dbgpt.agent.core.base_agent import ConversableAgent
from dbgpt.agent.core.profile.base import ProfileConfig
from dbgpt.agent.util.llm.llm import LLMConfig, LLMStrategyType


class _FakeLLMClient(LLMClient):
    def __init__(self, model_names):
        self._model_names = model_names

    async def generate(self, request, message_converter=None):
        raise NotImplementedError

    async def generate_stream(self, request, message_converter=None):
        if False:
            yield None
        raise NotImplementedError

    async def models(self):
        return [SimpleNamespace(model=name) for name in self._model_names]

    async def count_token(self, model: str, prompt: str) -> int:
        return 0


def _agent_with_models(model_names, priority):
    return ConversableAgent(
        profile=ProfileConfig(name="assistant", role="AI Assistant"),
        llm_config=LLMConfig(
            llm_client=_FakeLLMClient(model_names),
            llm_strategy=LLMStrategyType.Priority,
            strategy_context=json.dumps(priority),
        ),
    )


@pytest.mark.asyncio
async def test_select_llm_model_retries_registered_model_when_only_model_excluded():
    model_name = "Qwen3.8-flash-next-fp8"
    agent = _agent_with_models([model_name], [model_name])

    selected = await agent._a_select_llm_model([model_name])

    assert selected == model_name


@pytest.mark.asyncio
async def test_select_llm_model_uses_registered_backup_when_priority_excluded():
    primary = "Qwen3.8-flash-next-fp8"
    backup = "Qwen3.8-27B-FP8"
    agent = _agent_with_models([primary, backup], [primary])

    selected = await agent._a_select_llm_model([primary])

    assert selected == backup


@pytest.mark.asyncio
async def test_select_llm_model_does_not_fallback_to_unregistered_deepseek():
    agent = _agent_with_models([], ["Qwen3.8-flash-next-fp8"])

    with pytest.raises(ValueError, match="No available LLM model service"):
        await agent._a_select_llm_model()
