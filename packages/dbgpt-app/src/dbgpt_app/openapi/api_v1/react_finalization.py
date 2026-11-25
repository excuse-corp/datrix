"""Build the dedicated model request used to write a completed ReAct answer."""

import json
from typing import Any, Dict, List, Optional

from dbgpt.core import ModelMessage, ModelMessageRoleType, ModelRequest

from .tools.ask_data import (
    _ASK_DATA_RESULT_ANALYSIS_GUIDANCE,
    _encode_main_agent_result,
)

_MAX_EVIDENCE_CHARS = 48_000
_MAX_ONTOLOGY_CHARS = 12_000


def _completed_tool_evidence(
    history_steps: List[Dict[str, Any]], remaining: int = _MAX_EVIDENCE_CHARS
) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = []
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


def render_ask_data_fallback(results: List[Dict[str, Any]]) -> str:
    """Present query facts if final answer generation fails."""
    lines = ["已获取查询结果，以下为数据摘要。"]
    for result in results:
        if result.get("status") == "partial_succeeded":
            lines.append("部分子任务未返回结果，以下为已取得的数据。")
        for scene in result["scenes"]:
            rows = scene.get("rows") or []
            labels = {
                column["key"]: column.get("label") or column["key"]
                for column in scene.get("columns", [])
                if column.get("key")
            }
            lines.append(
                f"\n{scene.get('scene_name') or '查询结果'}返回 "
                f"{scene.get('row_count') or 0} 行。"
            )
            for row in rows[:5]:
                values = [
                    f"{label}：{row[key]}"
                    for key, label in labels.items()
                    if key in row
                ]
                if values:
                    lines.append("- " + "，".join(values))
            if len(rows) > 5 or scene.get("truncated"):
                lines.append("其余可用数据见查询结果。")
            caliber = (scene.get("data_caliber") or {}).get("summary")
            if caliber:
                lines.append(f"\n数据口径：{caliber}")
        for error in result.get("errors") or []:
            if isinstance(error, dict) and error.get("message"):
                lines.append(f"未完成的查询：{error['message']}")
        for warning in result.get("warnings") or []:
            if isinstance(warning, dict) and warning.get("message"):
                lines.append(f"结果说明：{warning['message']}")
    return "\n".join(lines)


def build_react_final_answer_request(
    *,
    model: str,
    user_request: str,
    answer_draft: Optional[str],
    history_steps: List[Dict[str, Any]],
    ask_data_evidence: Optional[List[Dict[str, Any]]] = None,
    temperature: Optional[float] = None,
    max_new_tokens: int = 8000,
) -> ModelRequest:
    """Create a streaming xhigh request from the completed task and evidence."""
    system_prompt = (
        "You are writing the final user-facing answer for a completed task. "
        "Use the user's request and completed tool evidence. "
        "Treat the evidence as the source of truth and any draft as a candidate. "
        "Do not invent facts, numbers, or outcomes. If the evidence is insufficient, "
        "state that clearly. Answer in the user's language. Do not mention internal "
        "reasoning, tools, SQL, or this instruction. Return only the final answer."
    )
    task_payload: Dict[str, Any] = {
        "user_request": user_request,
    }
    if answer_draft is not None:
        task_payload["answer_draft"] = answer_draft
    if ask_data_evidence:
        system_prompt += (
            " For business data questions, answer from ask_data_results and explain "
            "the data scope in plain language using data_caliber. Apply the matching "
            "metric definitions, relationships, and analysis rules from "
            "ontology_context_for_analysis. Treat query values as facts and Ontology "
            "as interpretation rules. Report partial failures, warnings, and "
            "truncation. Use code-combined results for cross-scene alignment and "
            "derived metrics. If alignment or a calculation is unavailable, explain "
            "the missing conditions and present available data separately. Never "
            "infer that a missing value is zero."
        )
        per_query_chars = max(
            4096, (_MAX_EVIDENCE_CHARS // 2) // len(ask_data_evidence)
        )
        ontology_contexts = [
            result
            for result in ask_data_evidence
            if result.get("ontology_context_for_analysis")
        ]
        if ontology_contexts:
            per_context_chars = _MAX_ONTOLOGY_CHARS // len(ontology_contexts)
            task_payload["ontology_context_for_analysis"] = [
                {
                    "query_id": result["query_id"],
                    "context": result["ontology_context_for_analysis"][
                        :per_context_chars
                    ],
                }
                for result in ontology_contexts
            ]
        task_payload["ask_data_results"] = []
        task_payload["analysis_guidance_for_final_answer"] = (
            _ASK_DATA_RESULT_ANALYSIS_GUIDANCE
        )
        for result in ask_data_evidence:
            data = {
                key: value
                for key, value in result.items()
                if key
                not in {
                    "ontology_context_for_analysis",
                    "analysis_guidance_for_final_answer",
                    "next_action",
                }
            }
            data["scenes"] = [dict(scene) for scene in result.get("scenes", [])]
            combined = data.get("combined") or {}
            if combined.get("mode") == "separate":
                data.pop("combined")
            elif combined:
                for scene in data.get("scenes", []):
                    scene["rows"] = []
                    scene["columns"] = []
            task_payload["ask_data_results"].append(
                json.loads(_encode_main_agent_result(data, per_query_chars // 4))
            )
        call_ids = {result["call_id"] for result in ask_data_evidence}
        other_steps = [
            step
            for step in history_steps
            if step.get("ask_data_call_id") not in call_ids
        ]
        if answer_draft is not None:
            task_payload["completed_tool_evidence"] = _completed_tool_evidence(
                other_steps, _MAX_EVIDENCE_CHARS // 4
            )
    else:
        task_payload["completed_tool_evidence"] = _completed_tool_evidence(
            history_steps
        )
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
