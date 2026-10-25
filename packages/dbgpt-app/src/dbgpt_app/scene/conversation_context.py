"""Shared conversation history projection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from dbgpt.core.interface.message import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ViewMessage,
)

MAX_RECENT_CONTEXT_MESSAGES = 8
DEFAULT_CONTEXT_TOKEN_BUDGET = 8192


@dataclass
class ConversationContext:
    history_messages: List[BaseMessage]
    recent_history_text: str

    def as_chat_history(self) -> List[BaseMessage]:
        return list(self.history_messages)

    def as_react_context(self) -> str:
        sections = ["## Session Context"]
        if self.recent_history_text:
            sections.extend(["### Recent Conversation", self.recent_history_text])
        return "\n\n".join(sections)


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value).strip()


def _parse_payload(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _react_payload(message: Any) -> Optional[Dict[str, Any]]:
    payload = _parse_payload(getattr(message, "content", ""))
    if payload and payload.get("type") == "react-agent":
        return payload
    return None


def _assistant_view_text(message: Any) -> str:
    payload = _react_payload(message)
    if payload:
        return _content_text(payload.get("final_content"))
    return ""


def _conversation_history(messages: Iterable[Any]) -> List[BaseMessage]:
    message_list = list(messages or [])
    rounds_with_ai = {
        getattr(message, "round_index", 0)
        for message in message_list
        if getattr(message, "type", None) == "ai"
    }
    history: List[BaseMessage] = []
    for message in message_list:
        message_type = getattr(message, "type", None)
        if message_type in {"human", "ai"}:
            history.append(message)
        elif message_type == "view":
            final_text = _assistant_view_text(message)
            round_index = getattr(message, "round_index", 0)
            if final_text and round_index not in rounds_with_ai:
                history.append(
                    AIMessage(content=final_text, round_index=round_index)
                )
    return history


def _history_lines(history: List[BaseMessage]) -> List[str]:
    lines = []
    for message in history[-MAX_RECENT_CONTEXT_MESSAGES:]:
        text = _content_text(message.content)
        if not text:
            continue
        role = "User" if isinstance(message, HumanMessage) else "Assistant"
        lines.append(f"- {role}: {text}")
    return lines


async def _count_tokens(
    text: str, llm_client: Any = None, model_name: Optional[str] = None
) -> int:
    if llm_client is not None and model_name:
        try:
            count = await llm_client.count_token(model_name, text)
            if isinstance(count, int) and count >= 0:
                return count
        except Exception:
            pass
    return max(1, (len(text) + 3) // 4)


async def _fit_history_lines(
    lines: List[str], budget: int, llm_client: Any, model_name: Optional[str]
) -> List[str]:
    selected = list(lines)
    while selected and await _count_tokens(
        "\n".join(selected), llm_client, model_name
    ) > budget:
        selected.pop(0)
    return selected


async def build_conversation_context(
    messages: Iterable[Any],
    *,
    llm_client: Any = None,
    model_name: Optional[str] = None,
    max_tokens: int = DEFAULT_CONTEXT_TOKEN_BUDGET,
) -> ConversationContext:
    message_list = list(messages or [])
    history = _conversation_history(message_list)
    recent_lines = await _fit_history_lines(
        _history_lines(history), max(256, max_tokens), llm_client, model_name
    )
    return ConversationContext(
        history_messages=history,
        recent_history_text="\n".join(recent_lines),
    )
