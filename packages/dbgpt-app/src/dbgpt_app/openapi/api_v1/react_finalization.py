"""Build the dedicated model request used to write a completed ReAct answer."""

import json
from typing import Any, Dict, List, Optional

from dbgpt.core import ModelMessage, ModelMessageRoleType, ModelRequest

_MAX_EVIDENCE_CHARS = 48_000


def _completed_tool_evidence(history_steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = []
    remaining = _MAX_EVIDENCE_CHARS
    for step in history_steps:
        if step.get("status") not in {"done", "completed"}:
            continue
        outputs = []
        for output in step.get("outputs") or []:
            if not isinstance(output, dict):
                continue
            content = output.get("content")
            if not isinstance(content, str) or not content:
                continue
            if output.get("output_type") in {"code", "image"}:
                continue
            if remaining <= 0:
                break
            content = content[:remaining]
            remaining -= len(content)
            outputs.append(
                {"type": output.get("output_type") or "text", "content": content}
            )
        if outputs:
            evidence.append(
                {
                    "step": step.get("title") or step.get("action") or "tool result",
                    "outputs": outputs,
                }
            )
        if remaining <= 0:
            break
    return evidence


def build_react_final_answer_request(
    *,
    model: str,
    user_request: str,
    answer_draft: str,
    history_steps: List[Dict[str, Any]],
    temperature: Optional[float] = None,
    max_new_tokens: int = 8000,
) -> ModelRequest:
    """Create a streaming xhigh request from the completed task and evidence."""
    system_prompt = (
        "You are writing the final user-facing answer for a completed task. "
        "Use the user's request, completed tool evidence, and draft answer. "
        "Treat tool evidence as the source of truth and the draft as a candidate. "
        "Do not invent facts, numbers, or outcomes. If the evidence is insufficient, "
        "state that clearly. Answer in the user's language. Do not mention internal "
        "reasoning, tools, SQL, or this instruction. Return only the final answer."
    )
    task_payload = {
        "user_request": user_request,
        "answer_draft": answer_draft,
        "completed_tool_evidence": _completed_tool_evidence(history_steps),
    }
    return ModelRequest.build_request(
        model,
        messages=[
            ModelMessage(role=ModelMessageRoleType.SYSTEM, content=system_prompt),
            ModelMessage(
                role=ModelMessageRoleType.HUMAN,
                content=json.dumps(task_payload, ensure_ascii=False),
            ),
        ],
        stream=True,
        temperature=temperature,
        max_new_tokens=max_new_tokens,
        reasoning_effort="xhigh",
    )
