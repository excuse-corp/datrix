# ruff: noqa: E501

import io
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from dbgpt._private.config import Config
from dbgpt._private.pydantic import BaseModel as _BaseModel
from dbgpt.agent.core.context import ContextBudgetConfig
from dbgpt.agent.resource.tool.base import tool
from dbgpt.agent.skill.manage import get_skill_manager
from dbgpt.component import ComponentType
from dbgpt.configs.model_config import SKILLS_DIR, resolve_root_path
from dbgpt.core import PromptTemplate
from dbgpt.model.cluster import WorkerManagerFactory
from dbgpt_app.openapi.api_view_model import (
    ConversationVo,
    Result,
)
from dbgpt_app.openapi.api_v1.react_session_state import (
    infer_active_skill_from_messages,
    is_skill_maintenance_request,
    load_react_session_state,
    record_active_skill,
    record_uploaded_attachment,
    render_react_session_context,
    save_react_session_state,
    update_react_session_after_turn,
)
from dbgpt_app.openapi.api_v1.skill_state import (
    normalize_skill_path,
    remove_skill_state,
    set_skill_enabled,
    skill_enabled,
)
from dbgpt_serve.utils.auth import UserRequest, get_user_from_headers

router = APIRouter()
CFG = Config()
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from dbgpt.agent.core.memory.gpts import GptsMemory
    from dbgpt.agent.resource.connector.manager import ConnectorManager
    from dbgpt.agent.resource.tool.base import BaseTool

REACT_AGENT_MEMORY_CACHE: Dict[str, "GptsMemory"] = {}
REACT_ASK_DATA_PENDING: Dict[str, Dict[str, str]] = {}
REACT_AGENT_RUNS: Dict[str, Dict[str, Any]] = {}

DEFAULT_SKILLS_DIR = SKILLS_DIR
PROTECTED_SKILL_NAMES = {"csv-data-analysis", "skill-creator"}
AUTO_DATA_MARKER_PATTERN = re.compile(
    r"###([A-Z0-9_]+)_START###\s*(.*?)\s*###\1_END###", re.DOTALL
)

REACT_STATUS_COMPLETED = "completed"
REACT_STATUS_INCOMPLETE = "incomplete"
REACT_STATUS_FAILED = "failed"
REACT_STATUS_CANCELLED = "cancelled"

REACT_REASON_TERMINATE = "terminate"
REACT_REASON_MAX_STEPS = "max_steps_exceeded"
REACT_REASON_LLM_ERROR = "llm_error"
REACT_REASON_PARSE_ERROR = "parse_error"
REACT_REASON_TOOL_ERROR = "tool_error"
REACT_REASON_TASK_PLAN_INCOMPLETE = "task_plan_incomplete"
REACT_REASON_REPEATED_ACTION = "repeated_action"
REACT_REASON_CANCELLED = "cancelled"
REACT_REASON_EXCEPTION = "exception"
REACT_REASON_STOPPED_WITHOUT_TERMINATE = "stopped_without_terminate"

_LLM_ERROR_TEXT_MARKERS = (
    "LLMServer Generate Error",
    "LLM Chat Generrate Error",
    "LLM generate stream is null",
    "APIConnectionError",
    "Connection error",
    "ModelNotFound",
)


def _env_int(
    name: str,
    default: int,
    *,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw not in (None, "") else default
    except Exception:
        value = default
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _looks_like_llm_error_text(text: Any) -> bool:
    if not isinstance(text, str) or not text:
        return False
    return any(marker in text for marker in _LLM_ERROR_TEXT_MARKERS) or bool(
        "Model " in text and " not found" in text
    )


def _is_skill_like_react_task(
    user_input: str,
    pre_matched_skill: Optional[Any] = None,
) -> bool:
    if pre_matched_skill is not None:
        return True
    text = (user_input or "").lower()
    return any(
        marker in text
        for marker in (
            "skill",
            "技能",
            "data-report",
            "report",
            "模板",
            "脚本",
            "优化开发",
        )
    )


def _react_max_steps_for(user_input: str, pre_matched_skill: Optional[Any]) -> int:
    hard_limit = _env_int("DATAMAN_REACT_MAX_STEPS_HARD_LIMIT", 100, min_value=1)
    default_steps = _env_int(
        "DATAMAN_REACT_MAX_STEPS_DEFAULT", 100, min_value=1, max_value=hard_limit
    )
    skill_steps = _env_int(
        "DATAMAN_REACT_MAX_STEPS_SKILL", 100, min_value=1, max_value=hard_limit
    )
    return (
        skill_steps
        if _is_skill_like_react_task(user_input, pre_matched_skill)
        else default_steps
    )


def _react_llm_error_limit() -> int:
    return _env_int("DATAMAN_REACT_LLM_ERROR_LIMIT", 2, min_value=1)


def _react_parse_error_limit() -> int:
    return _env_int("DATAMAN_REACT_PARSE_ERROR_LIMIT", 3, min_value=1)


def _react_same_action_repeat_limit() -> int:
    return _env_int("DATAMAN_REACT_SAME_ACTION_REPEAT_LIMIT", 3, min_value=1)


def _react_step_counts(history_steps: List[Dict[str, Any]]) -> Dict[str, int]:
    completed_steps = sum(1 for item in history_steps if item.get("status") == "done")
    failed_steps = sum(1 for item in history_steps if item.get("status") == "failed")
    cancelled_steps = sum(
        1 for item in history_steps if item.get("status") == REACT_STATUS_CANCELLED
    )
    return {
        "steps_count": len(history_steps),
        "completed_steps": completed_steps,
        "failed_steps": failed_steps,
        "cancelled_steps": cancelled_steps,
    }


def _has_open_task_plan(todo_list: List[Dict[str, Any]]) -> bool:
    return any(
        item.get("status") in {"pending", "in_progress", "failed"}
        for item in todo_list or []
    )


def _last_failed_step_error_type(history_steps: List[Dict[str, Any]]) -> Optional[str]:
    for item in reversed(history_steps):
        if item.get("status") != "failed":
            continue
        error_type = item.get("error_type")
        if error_type:
            return str(error_type)
        step_text = "\n".join(
            str(part)
            for part in (
                item.get("thought"),
                item.get("detail"),
                item.get("action"),
                item.get("error_message"),
                item.get("outputs"),
            )
            if part
        )
        if _looks_like_llm_error_text(step_text):
            return REACT_REASON_LLM_ERROR
        if not item.get("action"):
            return REACT_REASON_PARSE_ERROR
        return REACT_REASON_TOOL_ERROR
    return None


def _react_outcome_payload(
    *,
    status: str,
    termination_reason: str,
    history_steps: List[Dict[str, Any]],
    max_steps: int,
    error_message: Optional[str] = None,
) -> Dict[str, Any]:
    summary = _react_step_counts(history_steps)
    return {
        "status": status,
        "termination_reason": termination_reason,
        "error_message": error_message,
        "max_steps": max_steps,
        **summary,
    }


def _compute_react_run_outcome(
    reply: Any,
    history_steps: List[Dict[str, Any]],
    todo_list: List[Dict[str, Any]],
    max_steps: int,
) -> Dict[str, Any]:
    action_report = getattr(reply, "action_report", None)
    reply_content = getattr(reply, "content", "") or ""
    reply_success = bool(getattr(reply, "success", True))

    if not reply_success:
        error_message = getattr(action_report, "error_message", None) or reply_content
        error_type = getattr(action_report, "error_type", None)
        if error_type == REACT_REASON_REPEATED_ACTION:
            return _react_outcome_payload(
                status=REACT_STATUS_INCOMPLETE,
                termination_reason=REACT_REASON_REPEATED_ACTION,
                history_steps=history_steps,
                max_steps=max_steps,
                error_message=error_message,
            )
        if error_type == REACT_REASON_PARSE_ERROR:
            reason = REACT_REASON_PARSE_ERROR
        elif error_type == REACT_REASON_LLM_ERROR or _looks_like_llm_error_text(
            error_message
        ):
            reason = REACT_REASON_LLM_ERROR
        else:
            reason = _last_failed_step_error_type(history_steps) or REACT_REASON_EXCEPTION
        return _react_outcome_payload(
            status=REACT_STATUS_FAILED,
            termination_reason=reason,
            history_steps=history_steps,
            max_steps=max_steps,
            error_message=error_message,
        )

    if action_report and getattr(action_report, "terminate", False):
        if _has_open_task_plan(todo_list):
            return _react_outcome_payload(
                status=REACT_STATUS_INCOMPLETE,
                termination_reason=REACT_REASON_TASK_PLAN_INCOMPLETE,
                history_steps=history_steps,
                max_steps=max_steps,
            )
        return _react_outcome_payload(
            status=REACT_STATUS_COMPLETED,
            termination_reason=REACT_REASON_TERMINATE,
            history_steps=history_steps,
            max_steps=max_steps,
        )

    if action_report and not getattr(action_report, "is_exe_success", True):
        error_type = getattr(action_report, "error_type", None)
        if error_type == REACT_REASON_REPEATED_ACTION:
            return _react_outcome_payload(
                status=REACT_STATUS_INCOMPLETE,
                termination_reason=REACT_REASON_REPEATED_ACTION,
                history_steps=history_steps,
                max_steps=max_steps,
                error_message=getattr(action_report, "error_message", None)
                or getattr(action_report, "content", None),
            )
        reason = (
            REACT_REASON_LLM_ERROR
            if error_type == REACT_REASON_LLM_ERROR
            else REACT_REASON_PARSE_ERROR
            if error_type == REACT_REASON_PARSE_ERROR
            else REACT_REASON_TOOL_ERROR
        )
        return _react_outcome_payload(
            status=REACT_STATUS_FAILED,
            termination_reason=reason,
            history_steps=history_steps,
            max_steps=max_steps,
            error_message=getattr(action_report, "error_message", None)
            or getattr(action_report, "content", None),
        )

    reason = (
        REACT_REASON_MAX_STEPS
        if len(history_steps) >= max_steps
        else REACT_REASON_STOPPED_WITHOUT_TERMINATE
    )
    if reason != REACT_REASON_MAX_STEPS and _has_open_task_plan(todo_list):
        reason = REACT_REASON_TASK_PLAN_INCOMPLETE
    return _react_outcome_payload(
        status=REACT_STATUS_INCOMPLETE,
        termination_reason=reason,
        history_steps=history_steps,
        max_steps=max_steps,
    )


def _final_content_for_outcome(final_content: str, outcome: Dict[str, Any]) -> str:
    status = outcome.get("status")
    reason = outcome.get("termination_reason")
    error_message = (outcome.get("error_message") or "").strip()
    if status == REACT_STATUS_COMPLETED:
        return final_content or "任务已完成。"
    if status == REACT_STATUS_FAILED:
        if reason == REACT_REASON_LLM_ERROR:
            base = "任务失败：模型调用失败，已停止执行。"
        elif reason == REACT_REASON_PARSE_ERROR:
            base = "任务失败：模型连续返回无法解析的 ReAct 格式。"
        else:
            base = "任务失败：执行过程中出现错误。"
        return f"{base}\n{error_message}".strip() if error_message else base
    if reason == REACT_REASON_MAX_STEPS:
        return "任务未完成：已达到最大执行轮数，未收到 terminate。"
    if reason == REACT_REASON_TASK_PLAN_INCOMPLETE:
        return "任务未完成：执行流结束时仍有计划项未完成。"
    if reason == REACT_REASON_REPEATED_ACTION:
        return "任务未完成：检测到连续重复动作且没有新的有效产出。"
    return final_content or "任务未完成：执行流结束但未收到 terminate。"


def _skill_root_name(file_path: str) -> str:
    parts = PurePosixPath(str(file_path or "")).parts
    if not parts:
        return ""
    if parts[0] == "user" and len(parts) > 1:
        return parts[1]
    if parts[0] == "claude" and len(parts) > 1:
        return parts[1]
    return parts[0]


def _is_protected_skill_path(file_path: str, skill_name: Optional[str] = None) -> bool:
    root_name = _skill_root_name(file_path)
    name = skill_name or root_name
    parts = PurePosixPath(str(file_path or "")).parts
    return bool(
        name in PROTECTED_SKILL_NAMES
        or root_name in PROTECTED_SKILL_NAMES
        or (parts and parts[0] == "claude")
    )


def _skill_category(file_path: str, skill_name: Optional[str] = None) -> str:
    parts = PurePosixPath(str(file_path or "")).parts
    if _is_protected_skill_path(file_path, skill_name):
        return "official"
    if parts and parts[0] == "claude":
        return "official"
    return "personal"


def _resolve_skill_delete_target(file_path: str) -> Tuple[Path, str]:
    skills_dir = Path(DEFAULT_SKILLS_DIR).expanduser().resolve()
    rel_path = normalize_skill_path(file_path, skills_dir=str(skills_dir))
    target = (skills_dir / rel_path).resolve()
    try:
        target.relative_to(skills_dir)
    except Exception as exc:
        raise ValueError("Invalid skill file path") from exc

    if not target.exists():
        raise FileNotFoundError("Skill file not found")

    delete_target = target
    if target.is_file() and target.name == "SKILL.md":
        delete_target = target.parent
    return delete_target, rel_path


def _skill_tool_text_result(content: str) -> str:
    return json.dumps(
        {"chunks": [{"output_type": "text", "content": content}]},
        ensure_ascii=False,
    )


def _skill_file_for_state(skill_path: str) -> str:
    path = Path(skill_path).expanduser().resolve()
    skill_md = path / "SKILL.md"
    return str(skill_md if skill_md.is_file() else path)


def _skill_unavailable_message(skill_name: str, skill_manager: Any) -> Optional[str]:
    try:
        skill_path = skill_manager._get_skill_path(skill_name)
    except Exception:
        return None

    if not skill_path or not Path(skill_path).expanduser().exists():
        return f"Skill '{skill_name}' no longer exists"
    try:
        if not skill_enabled(_skill_file_for_state(skill_path), skills_dir=DEFAULT_SKILLS_DIR):
            return f"Skill '{skill_name}' is disabled"
    except Exception:
        return None
    return None


async def _resolve_model_context_tokens(
    llm_client: Any, model_name: Optional[str]
) -> Optional[int]:
    """Resolve model context window from runtime model metadata."""
    if not llm_client or not model_name:
        return None

    try:
        metadata = await llm_client.get_model_metadata(model_name)
        context_length = getattr(metadata, "context_length", None)
        if isinstance(context_length, int) and context_length > 0:
            return context_length
    except Exception:
        logger.debug(
            "Failed to resolve context window for model %s", model_name, exc_info=True
        )
    return None


async def _load_context_budget_config(
    llm_client: Any = None,
    model_name: Optional[str] = None,
) -> ContextBudgetConfig:
    """Build context budget config from app TOML and model metadata."""
    defaults = ContextBudgetConfig()

    def _value(agent_context: Any, field_name: str, default: Any) -> Any:
        if agent_context is None:
            return default
        value = getattr(agent_context, field_name, default)
        return default if value is None else value

    try:
        app_config = CFG.SYSTEM_APP.config.configs.get("app_config")
        web_config = getattr(getattr(app_config, "service", None), "web", None)
        agent_context = getattr(web_config, "agent_context", None)
        max_context_tokens = _value(
            agent_context, "max_context_tokens", defaults.max_context_tokens
        )
        return ContextBudgetConfig(
            max_context_tokens=max_context_tokens,
            warning_threshold=_value(
                agent_context, "warning_threshold", defaults.warning_threshold
            ),
            error_threshold=_value(
                agent_context, "error_threshold", defaults.error_threshold
            ),
            critical_threshold=_value(
                agent_context, "critical_threshold", defaults.critical_threshold
            ),
            reserved_tokens=_value(
                agent_context, "reserved_tokens", defaults.reserved_tokens
            ),
            min_keep_recent_rounds=_value(
                agent_context,
                "min_keep_recent_rounds",
                defaults.min_keep_recent_rounds,
            ),
            max_compact_failures=_value(
                agent_context,
                "max_compact_failures",
                defaults.max_compact_failures,
            ),
            max_observation_age_rounds=_value(
                agent_context,
                "max_observation_age_rounds",
                defaults.max_observation_age_rounds,
            ),
            truncated_observation_max_chars=(
                _value(
                    agent_context,
                    "truncated_observation_max_chars",
                    defaults.truncated_observation_max_chars,
                )
            ),
            min_keep_tokens=_value(
                agent_context,
                "min_keep_tokens",
                defaults.min_keep_tokens,
            ),
        )
    except Exception:
        logger.debug(
            "Failed to load agent context config; using defaults", exc_info=True
        )
        return defaults


def _json_load_object(value: str) -> Any:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        if start < 0:
            return None
        try:
            payload, _ = json.JSONDecoder().raw_decode(text[start:])
            return payload
        except Exception:
            return None


def _extract_ask_data_payload(value: Any, *, depth: int = 0) -> Optional[Dict[str, Any]]:
    if depth > 5 or value is None:
        return None
    if isinstance(value, str):
        parsed = _json_load_object(value)
        if parsed is None:
            return None
        return _extract_ask_data_payload(parsed, depth=depth + 1)
    if not isinstance(value, dict):
        return None
    if (
        value.get("status")
        and (
            isinstance(value.get("scenes"), list)
            or isinstance(value.get("results"), list)
            or isinstance(value.get("scene_summaries"), list)
        )
    ):
        return value
    chunks = value.get("chunks")
    if isinstance(chunks, list):
        for chunk in reversed(chunks):
            if isinstance(chunk, dict):
                nested = _extract_ask_data_payload(chunk.get("content"), depth=depth + 1)
                if nested:
                    return nested
    for key in ("content", "data", "payload", "result"):
        nested = _extract_ask_data_payload(value.get(key), depth=depth + 1)
        if nested:
            return nested
    return None


def _compact_recent_ask_data_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        compact = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        return payload
    for key in ("scenes", "results"):
        items = compact.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            rows = item.get("rows")
            if isinstance(rows, list) and len(rows) > 80:
                item["rows"] = rows[:80]
                item["truncated_for_recent_context"] = True
                item["original_row_count"] = len(rows)
    return compact


def _render_recent_ask_data_context(messages: List[Any], *, max_chars: int = 24000) -> str:
    """Render the latest AskData result from stored view history for reuse."""

    for message in reversed(messages or []):
        if getattr(message, "type", None) != "view":
            continue
        content = getattr(message, "content", "")
        content_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
        payload = _json_load_object(content_text)
        if not isinstance(payload, dict):
            continue

        candidates: List[Any] = []
        if payload.get("type") == "react-agent":
            steps = payload.get("steps") or []
            if isinstance(steps, list):
                for step in reversed(steps):
                    if not isinstance(step, dict):
                        continue
                    action = str(step.get("action") or "")
                    step_id = str(step.get("id") or "")
                    title = str(step.get("title") or "")
                    if action != "ask_data_query" and "ask-data" not in step_id and "AskData" not in title:
                        continue
                    outputs = step.get("outputs") or []
                    if isinstance(outputs, list):
                        for output in reversed(outputs):
                            if isinstance(output, dict):
                                candidates.append(output.get("content"))
            candidates.append(payload.get("final_content"))
        else:
            candidates.append(payload)

        for candidate in candidates:
            ask_payload = _extract_ask_data_payload(candidate)
            if not ask_payload:
                continue
            compact = _compact_recent_ask_data_payload(ask_payload)
            data = json.dumps(compact, ensure_ascii=False, default=str)
            if len(data) > max_chars:
                data = data[:max_chars] + "\n...<truncated recent AskData context>"
            return f"""
## Recent AskData Result From This Session
The following data was produced by a previous `ask_data_query` in this same conversation.
- This block has higher priority than the general business-data query rule for report/visualization/formatting follow-ups.
- If the latest user request asks to create an HTML report, visualization, summary, or formatted deliverable based on already queried data, use this data directly.
- Do not call `ask_data_query` again unless the user asks for refreshed data, changes filters/scope, or this data is insufficient.

```json
{data}
```
""".strip()
    return ""


def _extract_auto_data_markers(text: str) -> tuple[str, Dict[str, str]]:
    """Extract generic marker blocks from script output text.

    Marker format:
        ###KEY_START###...###KEY_END###
    """

    if not text or "###" not in text:
        return text, {}

    extracted: Dict[str, str] = {}

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        value = match.group(2).strip()
        if value:
            extracted[key] = value
        return ""

    cleaned = AUTO_DATA_MARKER_PATTERN.sub(_replace, text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, extracted


def _parse_connector_ids(ext_info: Optional[Dict[str, Any]]) -> List[str]:
    """Extract connector IDs from ``ext_info``.

    Supports two shapes:
    - ``ext_info.connector_ids`` — a list of UUID strings (preferred).
    - ``ext_info.connector_id``  — a single UUID string (legacy back-compat).

    Returns an empty list when no valid IDs are found.
    """
    if not ext_info or not isinstance(ext_info, dict):
        return []
    raw_ids = ext_info.get("connector_ids")
    if isinstance(raw_ids, list):
        return [cid for cid in raw_ids if isinstance(cid, str) and cid]
    legacy = ext_info.get("connector_id")
    if isinstance(legacy, str) and legacy:
        return [legacy]
    return []


def _select_connector_tools(
    connector_ids: List[str],
    connector_manager: Optional["ConnectorManager"],
) -> Tuple[List["BaseTool"], List[str]]:
    """Resolve connector_ids to flat list of BaseTool ready for ToolPack injection.

    Internally each connector is an MCPToolPack containing multiple BaseTool
    (one per MCP server tool). We flatten here so callers can directly compose
    them into a parent ToolPack without nesting issues -- nested ResourcePack
    would otherwise be inserted as a single dict entry under pack.name,
    making prefixed tool names un-lookup-able from the outer ToolPack.

    Args:
        connector_ids: IDs the user selected in the frontend.
        connector_manager: A :class:`ConnectorManager` instance (or ``None``).

    Returns:
        A tuple of ``(tools, missing_ids)`` where *tools* is the flat list
        of :class:`BaseTool` objects (each one a single MCP tool with its
        server URL captured in the call closure) and *missing_ids* lists
        IDs that could not be resolved (e.g. deleted mid-conversation).
    """
    from dbgpt.agent.resource.tool.base import BaseTool

    tools: List["BaseTool"] = []
    missing_ids: List[str] = []
    if connector_manager is None or not connector_ids:
        return tools, missing_ids
    for cid in connector_ids:
        pack = connector_manager.get_connector_tools(cid)
        if pack is None:
            missing_ids.append(cid)
            continue
        # Flatten: extract BaseTool instances from the pack
        for sub_tool in pack.sub_resources:
            if isinstance(sub_tool, BaseTool):
                tools.append(sub_tool)
    return tools, missing_ids


def _as_base_tool(tool_like: Any) -> Optional["BaseTool"]:
    """Return the underlying BaseTool for direct tool callables or BaseTool objects."""
    from dbgpt.agent.resource.tool.base import BaseTool

    if isinstance(tool_like, BaseTool):
        return tool_like
    wrapped = getattr(tool_like, "_tool", None)
    if isinstance(wrapped, BaseTool):
        return wrapped
    return None


def _render_tool_parameters(tool_resource: "BaseTool") -> str:
    """Render exact Action Input schema from the registered tool metadata."""
    if not tool_resource.args:
        return "{}"
    parts: List[str] = []
    for name, meta in tool_resource.args.items():
        arg_type = getattr(meta, "type", None) or "any"
        required = "required" if getattr(meta, "required", True) else "optional"
        description = (
            getattr(meta, "description", None)
            or getattr(meta, "title", None)
            or name
        )
        if len(description) > 120:
            description = description[:117] + "..."
        parts.append(f'"{name}": <{arg_type}, {required}, {description}>')
    return "{" + ", ".join(parts) + "}"


def _render_direct_tool_schema_prompt(
    title: str,
    tools: List[Any],
    *,
    extra_rules: Optional[List[str]] = None,
) -> str:
    """Render direct-call tool schemas that are otherwise easy to omit from prompts.

    The ReAct agent uses text protocol instead of native function-calling, so every
    directly callable runtime tool must have its exact Action Input schema in the
    system prompt.  This renderer consumes the same Tool metadata used by ToolPack
    execution to avoid hand-written schema drift.
    """
    rendered_tools = []
    for item in tools:
        tool_resource = _as_base_tool(item)
        if tool_resource is None:
            continue
        rendered_tools.append(
            f"- **{tool_resource.name}**: {tool_resource.description}\n"
            f"  Parameters: {_render_tool_parameters(tool_resource)}"
        )
    if not rendered_tools:
        return ""
    lines = [
        f"## {title}",
        "These tools are directly callable with `Action: <tool_name>`. Use the exact parameter names shown below.",
        *rendered_tools,
    ]
    if extra_rules:
        lines.extend(extra_rules)
    return "\n".join(lines)


async def _execute_skill_script_impl(
    skill_name: str, script_name: str, args: dict
) -> str:
    """Execute a script from a skill (implementation)."""
    skill_manager = get_skill_manager(CFG.SYSTEM_APP)
    unavailable = _skill_unavailable_message(skill_name, skill_manager)
    if unavailable:
        return _skill_tool_text_result(unavailable)
    result = await skill_manager.execute_script(skill_name, script_name, args)
    return result


@tool(
    description='执行技能中的脚本。参数: {"skill_name": "技能名称", '
    '"script_name": "脚本名称", "args": {参数}}'
)
async def execute_skill_script(skill_name: str, script_name: str, args: dict) -> str:
    """Execute a script from a skill."""
    return await _execute_skill_script_impl(skill_name, script_name, args)


@tool(
    description="获取技能资源文件内容。"
    "根据路径读取技能中的参考文档、配置文件等非脚本资源。"
    '参数: {"skill_name": "技能名称", "resource_path": "资源路径"}'
    "\\n示例:"
    '\\n- 读取参考文档: {"skill_name": "my-skill", '
    '"resource_path": "references/analysis_framework.md"}'
    "\n注意: 执行脚本请使用 shell_interpreter 工具"
)
async def get_skill_resource(
    skill_name: str, resource_path: str, args: Optional[dict] = None
) -> str:
    from dbgpt.agent.skill.manage import get_skill_manager

    try:
        sm = get_skill_manager(CFG.SYSTEM_APP)
        unavailable = _skill_unavailable_message(skill_name, sm)
        if unavailable:
            return json.dumps(
                {"error": True, "message": unavailable},
                ensure_ascii=False,
            )
        result = await sm.get_skill_resource(skill_name, resource_path, args or {})
        return result
    except Exception as e:
        import json

        return json.dumps(
            {"error": True, "message": f"Error: {str(e)}"},
            ensure_ascii=False,
        )


@tool(
    description="执行技能scripts目录下的脚本文件。参数: "
    '{"skill_name": "技能名称", "script_file_name": "脚本文件名", "args": {参数}}'
)
async def execute_skill_script_file(
    skill_name: str, script_file_name: str, args: Optional[dict] = None
) -> str:
    """Execute a script file from a skill's scripts directory."""
    from dbgpt.agent.skill.manage import get_skill_manager

    try:
        sm = get_skill_manager(CFG.SYSTEM_APP)
        unavailable = _skill_unavailable_message(skill_name, sm)
        if unavailable:
            return _skill_tool_text_result(unavailable)
        result = await sm.execute_skill_script_file(
            skill_name, script_file_name, args or {}
        )
        return result
    except Exception as e:
        import json

        return json.dumps(
            {"chunks": [{"output_type": "text", "content": f"Error: {str(e)}"}]},
            ensure_ascii=False,
        )


@router.get("/v1/skills/list", response_model=Result)
async def list_skills(
    user_token: UserRequest = Depends(get_user_from_headers),
):
    """List all available skills from the skills directory.

    Returns a list of skills with their metadata, including:
    - id: Unique identifier for the skill
    - name: Display name of the skill
    - description: Brief description of what the skill does
    - version: Skill version
    - author: Skill author
    - skill_type: Type of skill (e.g., data_analysis, chat, coding)
    - tags: List of tags for categorization
    - type: 'official' for claude/ directory, 'personal' for user/ directory
    - file_path: Relative path to the skill file
    """
    from dbgpt.agent.skill.loader import SkillLoader

    skills_data = []
    skills_dir = DEFAULT_SKILLS_DIR
    skills_dir_resolved = Path(skills_dir).expanduser().resolve()

    try:
        loader = SkillLoader()
        skills = loader.load_skills_from_directory(skills_dir, recursive=True)

        for skill in skills:
            if not skill or not skill.metadata:
                continue

            metadata = skill.metadata
            # Determine if the skill is official or personal based on file path
            file_path = getattr(metadata, "file_path", None) or ""
            if not file_path and hasattr(skill, "_config"):
                file_path = skill._config.get("file_path", "")

            # Convert absolute file_path to relative (relative to skills_dir)
            if file_path:
                try:
                    file_path = str(
                        Path(file_path)
                        .expanduser()
                        .resolve()
                        .relative_to(skills_dir_resolved)
                    )
                except Exception:
                    pass

            skill_type_category = _skill_category(file_path, metadata.name)
            is_protected = _is_protected_skill_path(file_path, metadata.name)
            enabled = True
            if file_path:
                try:
                    enabled = skill_enabled(file_path, skills_dir=str(skills_dir_resolved))
                except Exception:
                    enabled = True

            # Get skill_type value
            skill_type_val = metadata.skill_type
            if hasattr(skill_type_val, "value"):
                skill_type_val = skill_type_val.value

            skill_info = {
                "id": metadata.name,
                "name": metadata.name,
                "description": metadata.description or "",
                "version": getattr(metadata, "version", "1.0.0") or "1.0.0",
                "author": getattr(metadata, "author", None),
                "skill_type": skill_type_val,
                "tags": getattr(metadata, "tags", []) or [],
                "type": skill_type_category,
                "file_path": file_path,
                "enabled": enabled,
                "deletable": not is_protected,
            }
            skills_data.append(skill_info)

        # Sort skills: official first, then by name
        skills_data.sort(key=lambda x: (0 if x["type"] == "official" else 1, x["name"]))

        return Result.succ(skills_data)
    except Exception as e:
        logger.exception("Failed to load skills from directory")
        return Result.failed(code="E5001", msg=f"Failed to load skills: {str(e)}")


@router.get("/v1/skills/detail", response_model=Result)
async def skill_detail(
    skill_name: str = Query("", description="Skill name"),
    file_path: str = Query("", description="Skill file path"),
    user_token: UserRequest = Depends(get_user_from_headers),
):
    """Load a skill detail, including file tree and SKILL.md content."""
    if not file_path:
        return Result.failed(code="E4001", msg="file_path is required")

    skills_dir = Path(DEFAULT_SKILLS_DIR).expanduser().resolve()

    # Always treat file_path as relative to skills_dir.
    # If an absolute path was provided (legacy), try to make it relative first.
    fp = Path(file_path).expanduser()
    if fp.is_absolute():
        try:
            fp = fp.resolve().relative_to(skills_dir)
        except Exception:
            return Result.failed(code="E4002", msg="Invalid skill file path")
    target = (skills_dir / fp).resolve()

    # Security: ensure target is under skills_dir
    try:
        target.relative_to(skills_dir)
    except Exception:
        return Result.failed(code="E4002", msg="Invalid skill file path")

    if not target.exists():
        return Result.failed(code="E4040", msg="Skill file not found")

    root_dir = target if target.is_dir() else target.parent

    def build_tree(path: Path, base: Path) -> Dict[str, Any]:
        rel = path.relative_to(base)
        node: Dict[str, Any] = {
            "title": path.name,
            "key": str(rel),
        }
        if path.is_dir():
            children = sorted(
                [p for p in path.iterdir() if not p.name.startswith(".")],
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
            node["children"] = [build_tree(child, base) for child in children]
        return node

    tree = build_tree(root_dir, root_dir)

    skill_md_path = root_dir / "SKILL.md"
    frontmatter = ""
    instructions = ""
    raw_content = ""
    content_type = ""

    if skill_md_path.exists():
        raw_content = skill_md_path.read_text(encoding="utf-8")
        content_type = "skill_md"
        content = raw_content.strip()
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = parts[1].strip()
                instructions = parts[2].strip()
            else:
                instructions = content
        else:
            instructions = content
    elif target.is_file():
        raw_content = target.read_text(encoding="utf-8")
        suffix = target.suffix.lower()
        if suffix in {".yaml", ".yml"}:
            content_type = "yaml"
            frontmatter = raw_content
        elif suffix == ".json":
            content_type = "json"
            frontmatter = raw_content
        else:
            content_type = "text"
            instructions = raw_content

    metadata: Dict[str, Any] = {}
    try:
        from dbgpt.agent.skill.loader import SkillLoader

        loader = SkillLoader()
        skill = loader.load_skill_from_file(str(target))
        if skill and getattr(skill, "metadata", None):
            try:
                metadata = skill.metadata.to_dict()  # type: ignore[attr-defined]
            except Exception:
                metadata = {
                    "name": getattr(skill.metadata, "name", ""),
                    "description": getattr(skill.metadata, "description", ""),
                    "version": getattr(skill.metadata, "version", ""),
                    "author": getattr(skill.metadata, "author", ""),
                    "skill_type": getattr(skill.metadata, "skill_type", ""),
                    "tags": getattr(skill.metadata, "tags", []) or [],
                }
    except Exception:
        metadata = {}

    if not frontmatter and metadata:
        frontmatter = "\n".join(
            [
                f"name: {metadata.get('name', '')}",
                f"description: {metadata.get('description', '')}",
                f"version: {metadata.get('version', '')}",
                f"author: {metadata.get('author', '')}",
                f"skill_type: {metadata.get('skill_type', '')}",
            ]
        ).strip()

    display_path = str(target)
    display_root = str(root_dir)
    try:
        display_path = str(target.relative_to(skills_dir))
        display_root = str(root_dir.relative_to(skills_dir))
    except Exception:
        pass

    return Result.succ(
        {
            "skill_name": skill_name or metadata.get("name", ""),
            "file_path": display_path,
            "root_dir": display_root,
            "tree": tree,
            "frontmatter": frontmatter,
            "instructions": instructions,
            "raw_content": raw_content,
            "content_type": content_type,
            "metadata": metadata,
        }
    )


@router.post("/v1/skills/toggle", response_model=Result)
async def toggle_skill(
    payload: Dict[str, Any] = Body(...),
    user_token: UserRequest = Depends(get_user_from_headers),
):
    """Enable or disable a skill for selection and ReAct auto-matching."""
    file_path = str(payload.get("file_path") or "").strip()
    skill_name = str(payload.get("skill_name") or payload.get("name") or "").strip()
    if not file_path:
        return Result.failed(code="E4001", msg="file_path is required")
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return Result.failed(code="E4002", msg="enabled must be a boolean")

    try:
        state_entry = set_skill_enabled(
            file_path,
            enabled,
            skill_name=skill_name or None,
            skills_dir=DEFAULT_SKILLS_DIR,
        )
    except ValueError as exc:
        return Result.failed(code="E4003", msg=str(exc))
    except Exception as exc:
        logger.exception("Failed to update skill enabled state")
        return Result.failed(code="E5003", msg=f"Toggle failed: {str(exc)}")

    return Result.succ(state_entry)


@router.delete("/v1/skills/delete", response_model=Result)
async def delete_skill(
    skill_name: str = Query("", description="Skill name"),
    file_path: str = Query("", description="Skill file path"),
    user_token: UserRequest = Depends(get_user_from_headers),
):
    """Delete a non-protected skill directory or skill file."""
    if not file_path:
        return Result.failed(code="E4001", msg="file_path is required")

    try:
        rel_path = normalize_skill_path(file_path, skills_dir=DEFAULT_SKILLS_DIR)
    except ValueError as exc:
        return Result.failed(code="E4002", msg=str(exc))

    if _is_protected_skill_path(rel_path, skill_name or None):
        return Result.failed(code="E4030", msg="Official built-in skills cannot be deleted")

    try:
        target, normalized = _resolve_skill_delete_target(rel_path)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        remove_skill_state(normalized, skills_dir=DEFAULT_SKILLS_DIR)
        return Result.succ(
            {
                "skill_name": skill_name or _skill_root_name(normalized),
                "file_path": normalized,
                "message": f"Skill deleted successfully: {normalized}",
            }
        )
    except FileNotFoundError:
        remove_skill_state(rel_path, skills_dir=DEFAULT_SKILLS_DIR)
        return Result.succ(
            {
                "skill_name": skill_name or _skill_root_name(rel_path),
                "file_path": rel_path,
                "message": f"Skill already removed: {rel_path}",
            }
        )
    except Exception as exc:
        logger.exception("Failed to delete skill")
        return Result.failed(code="E5004", msg=f"Delete failed: {str(exc)}")


def _install_skill_from_dir(src_dir: Path, skill_name: str, user_dir: Path) -> str:
    """Copy an extracted skill directory into the user skills directory.

    Args:
        src_dir (Path): Directory containing the skill's files (already extracted).
        skill_name (str): Name to use for the skill directory under ``user_dir``.
        user_dir (Path): The ``skills/user/`` directory.

    Returns:
        str: Path of the installed skill directory relative to the skills root
             (i.e. ``user/<skill_name>``).
    """
    dest = user_dir / skill_name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src_dir, dest)
    # Return path relative to skills_dir (parent of user_dir)
    return str(dest.relative_to(user_dir.parent))


@router.post("/v1/skills/upload", response_model=Result)
async def skill_upload(
    file: UploadFile = File(...),
    user_token: UserRequest = Depends(get_user_from_headers),
):
    """Upload a skill package (.zip, .skill) or a single file to pilot/tmp/."""
    if not file.filename:
        return Result.failed(code="E4001", msg="No file provided")

    upload_dir = Path(resolve_root_path("pilot/tmp") or "pilot/tmp").resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)

    skills_dir = Path(DEFAULT_SKILLS_DIR).expanduser().resolve()
    user_dir = skills_dir / "user"
    user_dir.mkdir(parents=True, exist_ok=True)

    filename = file.filename
    normalized_filename = filename.replace("\\", "/")
    filename_path = PurePosixPath(normalized_filename)
    if (
        filename_path.is_absolute()
        or ".." in filename_path.parts
        or len(filename_path.parts) != 1
        or filename_path.name != filename
    ):
        return Result.failed(code="E4002", msg="Unsafe upload filename")
    suffix = Path(filename).suffix.lower()
    stem = Path(filename).stem

    try:
        content_bytes = await file.read()

        tmp_file = upload_dir / filename
        tmp_file.write_bytes(content_bytes)

        is_archive = False
        if suffix == ".zip":
            is_archive = True
        elif suffix == ".skill":
            buf = io.BytesIO(content_bytes)
            is_archive = zipfile.is_zipfile(buf)

        if is_archive:
            # Reuse the robust _extract_skill_from_zip helper (same one used
            # by the GitHub import endpoint) to avoid the nested-directory bug
            # that the old inline extractall logic suffered from.
            #
            # strict=False: uploaded packages may not contain a SKILL.md yet.
            tmp_zip = upload_dir / f"{uuid.uuid4().hex}.zip"
            tmp_zip.write_bytes(content_bytes)
            try:
                with tempfile.TemporaryDirectory(dir=upload_dir) as tmp_extract:
                    dest_in_tmp = Path(tmp_extract) / "skill"
                    try:
                        dest_name = _extract_skill_from_zip(
                            tmp_zip, subpath=None, dest_dir=dest_in_tmp, strict=False
                        )
                    except ValueError as exc:
                        return Result.failed(code="E4002", msg=str(exc))

                    rel_path = _install_skill_from_dir(dest_in_tmp, dest_name, user_dir)
            finally:
                tmp_zip.unlink(missing_ok=True)

        else:
            dest = user_dir / stem
            dest.mkdir(parents=True, exist_ok=True)

            if suffix in (".md", ".skill"):
                target_name = "SKILL.md"
            else:
                target_name = filename
            target_file = dest / target_name

            target_file.write_bytes(content_bytes)

            rel_path = str(dest.relative_to(skills_dir))

        return Result.succ(
            {
                "file_path": rel_path,
                "tmp_path": str(tmp_file),
                "message": f"Skill uploaded successfully: {rel_path}",
            }
        )
    except Exception as e:
        logger.exception("Failed to upload skill")
        return Result.failed(code="E5002", msg=f"Upload failed: {str(e)}")


def _parse_github_url(
    github_url: str,
) -> "tuple[str, str, str, Optional[str]]":
    """Parse a GitHub or skills.sh URL into (owner, repo, branch, subdir).

    Supported formats:
      - https://github.com/owner/repo
      - https://github.com/owner/repo/tree/<branch>[/optional/sub/dir]
      - https://github.com/owner/repo/blob/<branch>/path/to/FILE.md
      - https://skills.sh/owner/repo
      - https://skills.sh/owner/repo[/skill-name]

    Returns:
        tuple[str, str, str, Optional[str]]
          (owner, repo, branch, subdir) — branch is always a str (defaults to "main")

    Raises:
        ValueError: if the URL is not a recognisable GitHub/skills.sh repo URL.
    """
    parsed = urlparse(github_url)
    is_skills_sh = parsed.netloc in ("skills.sh", "www.skills.sh")
    is_github = parsed.netloc in ("github.com", "www.github.com")

    if not is_github and not is_skills_sh:
        raise ValueError(f"Not a GitHub URL: {github_url!r}")

    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"Cannot extract owner/repo from URL: {github_url!r}")

    owner, repo = parts[0], parts[1]
    # Strip '.git' suffix if present
    if repo.endswith(".git"):
        repo = repo[:-4]

    branch: str = "main"
    subdir: Optional[str] = None

    if is_skills_sh:
        # skills.sh: /owner/repo[/skill-name[/more]]
        # Everything after owner/repo is treated as subpath
        if len(parts) >= 3:
            subdir = "/".join(parts[2:])
    else:
        # GitHub
        if len(parts) >= 4 and parts[2] == "tree":
            # /owner/repo/tree/<branch>[/path/to/subdir]
            branch = parts[3]
            if len(parts) >= 5:
                subdir = "/".join(parts[4:])
        elif len(parts) >= 4 and parts[2] == "blob":
            # /owner/repo/blob/<branch>/path/to/FILE.md — strip filename
            branch = parts[3]
            if len(parts) >= 6:
                # Keep everything except the last component (the filename)
                subdir = "/".join(parts[4:-1])
            # If exactly 5 parts: blob/<branch>/filename — no subdir

    return owner, repo, branch, subdir


def _construct_download_url(owner: str, repo: str, branch: str) -> str:
    """Return the GitHub archive ZIP download URL for the given branch.

    Args:
        owner (str): Repository owner/organisation.
        repo (str): Repository name.
        branch (str): Branch name.

    Returns:
        str: URL pointing to the ZIP archive for the branch.
    """
    return f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"


def _is_macos_junk(name: str) -> bool:
    """Return True if the archive entry is a macOS metadata artifact."""
    parts = name.split("/")
    return any(p == "__MACOSX" or p.startswith("._") for p in parts)


def _extract_skill_from_zip(
    zip_path: "Path",
    subpath: "Optional[str]",
    dest_dir: "Path",
    strict: bool = True,
) -> str:
    """Extract a skill from a ZIP archive into ``dest_dir``.

    The ZIP is expected to have a single top-level directory (e.g.
    ``repo-main/``).  That top-level directory is stripped when extracting so
    that the files inside it land directly in ``dest_dir``.

    When ``subpath`` is given, only the files under
    ``{top_dir}/{subpath}/`` are extracted (again, stripped to ``dest_dir``).

    macOS metadata entries (``__MACOSX/`` directories and ``._*`` files) are
    automatically filtered out before any directory-structure analysis so they
    do not cause spurious nested directories.

    Args:
        zip_path (Path): Path to the ZIP file on disk.
        subpath (Optional[str]): Relative sub-directory inside the archive
            (after stripping the top-level dir) that contains the skill.
            Pass ``None`` to use the root of the archive.
        dest_dir (Path): Directory into which the skill files are extracted.
            It is created if it does not exist; if it already exists its
            contents are removed before extraction.
        strict (bool): When ``True`` (default), raise ``ValueError`` if no
            ``SKILL.md`` is found in the archive.  When ``False``, skip the
            ``SKILL.md`` validation — useful for uploading generic skill
            packages that may not yet contain a ``SKILL.md``.

    Returns:
        str: The skill name derived from ``subpath`` (last component) or from
        the top-level archive directory name.

    Raises:
        ValueError: If the archive contains path-traversal sequences.
        ValueError: If no ``SKILL.md`` is found after extraction (only when
            ``strict=True``).
        ValueError: If the archive root contains multiple sub-directories with
            ``SKILL.md`` files and no ``subpath`` was specified (the error
            message lists the available sub-directory names).
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        all_names = zf.namelist()

        # Security: reject any path-traversal entries
        for name in all_names:
            normalized = os.path.normpath(name)
            if normalized.startswith("..") or ".." in normalized.split(os.sep):
                raise ValueError(f"Unsafe path in archive: {name!r}")

        # Filter out macOS metadata artifacts before analysing structure
        valid_names = [n for n in all_names if not _is_macos_junk(n)]

        # Detect the single top-level directory (GitHub archives always have one)
        top_dirs = {n.split("/")[0] for n in valid_names if "/" in n}
        archive_root: Optional[str] = top_dirs.pop() if len(top_dirs) == 1 else None

        # Build the prefix inside the archive that maps to dest_dir
        if subpath:
            skill_prefix = (
                f"{archive_root}/{subpath}/" if archive_root else f"{subpath}/"
            )
            skill_name = subpath.split("/")[-1]
        else:
            skill_prefix = f"{archive_root}/" if archive_root else ""
            skill_name = archive_root or dest_dir.name

        # Check whether SKILL.md exists under the chosen prefix
        skill_md_entry = next(
            (n for n in valid_names if n == skill_prefix + "SKILL.md"),
            None,
        )

        if skill_md_entry is None and not subpath:
            # No SKILL.md at root — scan one level of subdirectories
            subdirs_with_skill = []
            for name in valid_names:
                if not name.startswith(skill_prefix):
                    continue
                rel = name[len(skill_prefix) :]
                parts = rel.split("/")
                if len(parts) == 2 and parts[1] == "SKILL.md":
                    subdirs_with_skill.append(parts[0])

            if len(subdirs_with_skill) > 1:
                raise ValueError(
                    "Multiple skills found. Specify a subpath. "
                    "Available: " + ", ".join(sorted(subdirs_with_skill))
                )

            # If exactly one sub-directory has SKILL.md, use it automatically
            if len(subdirs_with_skill) == 1:
                only_subdir = subdirs_with_skill[0]
                skill_prefix = f"{skill_prefix}{only_subdir}/"
                skill_name = only_subdir
                skill_md_entry = skill_prefix + "SKILL.md"

        if strict and skill_md_entry is None:
            raise ValueError(
                "No SKILL.md found in the archive"
                + (f" under '{subpath}'" if subpath else "")
                + ". Make sure the skill directory contains a SKILL.md file."
            )

        # Prepare dest_dir: remove existing content then (re-)create
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Extract valid members individually (no extractall) for security
        for member in valid_names:
            if not member.startswith(skill_prefix) or member == skill_prefix:
                continue
            rel = member[len(skill_prefix) :]
            if not rel:
                continue
            target = dest_dir / rel
            if member.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))

    return skill_name


@router.post("/v1/skills/import_github", response_model=Result)
async def skill_import_from_github_v2(
    request: Request,
    user_token: UserRequest = Depends(get_user_from_headers),
):
    """Import a skill from a GitHub or skills.sh URL.

    Accepts ``{ "url": "..." }`` from the frontend, downloads the repository
    ZIP, extracts the skill, installs it to ``skills/user/<name>/``, and
    returns a success response.

    This endpoint:

    - Accepts a raw JSON body ``{ "url": "..." }`` (no Pydantic model).
    - Supports branch fallback: tries ``main`` first, then ``master`` if 404.
    - Enforces a 50 MB download size limit.
    - Delegates extraction/installation to the modular helpers
      ``_extract_skill_from_zip`` and ``_install_skill_from_dir``.

    Error codes:
        - ``E4001``: Empty URL.
        - ``E4003``: Malformed or non-GitHub/skills.sh URL.
        - ``E4004``: ``SKILL.md`` not found in the downloaded content.
        - ``E4005``: Download failed or size limit exceeded.
        - ``E5002``: Unexpected server-side error.
    """
    import httpx

    # --- parse JSON body --------------------------------------------------------
    body = await request.json()
    url = body.get("url", "").strip()
    if not url:
        return Result.failed(code="E4001", msg="URL must not be empty")

    # --- parse URL --------------------------------------------------------------
    try:
        owner, repo, branch, subpath = _parse_github_url(url)
    except ValueError as exc:
        return Result.failed(code="E4003", msg=str(exc))

    # --- resolve dirs -----------------------------------------------------------
    skills_dir = Path(DEFAULT_SKILLS_DIR).expanduser().resolve()
    user_dir = skills_dir / "user"
    user_dir.mkdir(parents=True, exist_ok=True)

    upload_dir = Path(resolve_root_path("pilot/tmp") or "pilot/tmp").resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)

    # --- download with branch fallback (main → master) --------------------------
    zip_path: Optional[Path] = None
    tmp_dir_obj = None  # tempfile.TemporaryDirectory kept alive until finally

    try:
        zip_url = _construct_download_url(owner, repo, branch)

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(120.0),
        ) as client:
            response = await client.get(zip_url)

            # Branch fallback: if the resolved branch gives 404, try "master"
            if response.status_code == 404 and branch == "main":
                fallback_branch = "master"
                fallback_url = _construct_download_url(owner, repo, fallback_branch)
                response = await client.get(fallback_url)
                if response.status_code == 200:
                    branch = fallback_branch
                    zip_url = fallback_url

            if response.status_code != 200:
                return Result.failed(
                    code="E4005",
                    msg=(
                        f"Failed to download {zip_url!r}: HTTP {response.status_code}"
                    ),
                )

            content_bytes = response.content

        # --- size limit check ---------------------------------------------------
        if len(content_bytes) > 50 * 1024 * 1024:
            return Result.failed(
                code="E4005",
                msg=(
                    f"Download size {len(content_bytes) // (1024 * 1024)} MB "
                    "exceeds the 50 MB limit"
                ),
            )

        # --- save raw zip to tmp ------------------------------------------------
        zip_filename = f"{repo}-{branch}.zip"
        zip_path = upload_dir / zip_filename
        zip_path.write_bytes(content_bytes)

        # --- extract into a temp directory, then install ------------------------
        tmp_dir_obj = tempfile.TemporaryDirectory(dir=upload_dir)
        dest_dir_in_temp = Path(tmp_dir_obj.name) / "skill"
        dest_dir_in_temp.mkdir(parents=True, exist_ok=True)

        try:
            skill_name = _extract_skill_from_zip(zip_path, subpath, dest_dir_in_temp)
        except ValueError as exc:
            err_msg = str(exc)
            if "SKILL.md" in err_msg:
                return Result.failed(code="E4004", msg=err_msg)
            return Result.failed(code="E4003", msg=err_msg)

        rel_path = _install_skill_from_dir(dest_dir_in_temp, skill_name, user_dir)

        return Result.succ(
            {
                "file_path": rel_path,
                "message": f"Skill imported successfully from GitHub: {rel_path}",
            }
        )

    except httpx.RequestError as exc:
        logger.exception("Network error while downloading skill from GitHub")
        return Result.failed(
            code="E4005", msg=f"Network error downloading skill: {str(exc)}"
        )
    except Exception as exc:
        logger.exception("Failed to import skill from GitHub (v2)")
        return Result.failed(code="E5002", msg=f"Import failed: {str(exc)}")
    finally:
        # Clean up temp zip file
        if zip_path is not None:
            try:
                zip_path.unlink(missing_ok=True)
            except Exception:
                pass
        # Clean up temp extraction directory
        if tmp_dir_obj is not None:
            try:
                tmp_dir_obj.cleanup()
            except Exception:
                pass


def _sse_event(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _react_agent_stream(
    dialogue: ConversationVo,
    user_token: UserRequest | None = None,
) -> AsyncGenerator[str, None]:
    import asyncio

    from dbgpt.agent import AgentContext, AgentMemory, AgentMessage
    from dbgpt.agent.claude_skill import get_registry, load_skills_from_dir
    from dbgpt.agent.core.memory.gpts import (
        DefaultGptsPlansMemory,
        GptsMemory,
    )
    from dbgpt.agent.expand.actions.react_action import Terminate
    from dbgpt.agent.expand.react_agent import ReActAgent
    from dbgpt.agent.resource import ToolPack
    from dbgpt.agent.resource.manage import get_resource_manager
    from dbgpt.agent.util.llm.llm import LLMConfig, LLMStrategyType
    from dbgpt.agent.util.react_parser import ReActOutputParser
    from dbgpt.core import (
        ModelMessage,
        ModelMessageRoleType,
        ModelRequest,
        StorageConversation,
    )
    from dbgpt.model.cluster.client import DefaultLLMClient
    from dbgpt_serve.agent.agents.db_gpts_memory import MetaDbGptsMessageMemory
    from dbgpt_serve.conversation.serve import Serve as ConversationServe

    step = 0
    user_input = dialogue.user_input
    if not isinstance(user_input, str):
        user_input = str(user_input or "")

    file_path = None
    skill_name = None
    run_id = None
    if dialogue.ext_info and isinstance(dialogue.ext_info, dict):
        file_path = dialogue.ext_info.get("file_path")
        skill_name = dialogue.ext_info.get("skill_name")
        run_id = dialogue.ext_info.get("client_run_id") or dialogue.ext_info.get(
            "run_id"
        )
    run_id = str(run_id or uuid.uuid4().hex)
    conv_id = dialogue.conv_uid or str(uuid.uuid4())
    skill_maintenance = is_skill_maintenance_request(user_input)
    session_state = load_react_session_state(conv_id)
    if file_path:
        record_uploaded_attachment(session_state, file_path)
    cancel_event = asyncio.Event()

    # Connector selection (Task C): only inject user-selected connectors.
    connector_ids: List[str] = _parse_connector_ids(dialogue.ext_info)
    ask_data_enabled = os.getenv("DBGPT_REACT_ASK_DATA_TOOL_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    def build_step(title: str, detail: str, phase: str = None):
        nonlocal step
        step += 1
        step_id = f"step-{step}"
        event_data = {
            "type": "step.start",
            "step": step,
            "id": step_id,
            "title": title,
            "detail": detail,
        }
        if phase:
            event_data["phase"] = phase
        return step_id, _sse_event(event_data)

    def step_output(detail: str):
        return _sse_event({"type": "step.output", "step": step, "detail": detail})

    def step_chunk(step_id: str, output_type: str, content: Any):
        return _sse_event(
            {
                "type": "step.chunk",
                "id": step_id,
                "output_type": output_type,
                "content": content,
            }
        )

    def step_done(
        step_id: str,
        status: str = "done",
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        payload = {"type": "step.done", "id": step_id, "status": status}
        if error_type:
            payload["error_type"] = error_type
        if error_message:
            payload["error_message"] = error_message
        return _sse_event(payload)

    def step_meta(
        step_id: str,
        thought: Optional[str],
        action: Optional[str],
        action_input: Optional[str],
        title: Optional[str] = None,
        action_intention: Optional[str] = None,
        action_reason: Optional[str] = None,
        todo_meta: Optional[Dict[str, Any]] = None,
        ask_data_call_id: Optional[str] = None,
        ask_data_reused: bool = False,
    ):
        payload = {
            "type": "step.meta",
            "id": step_id,
            "thought": thought,
            "action_intention": action_intention,
            "action_reason": action_reason,
            "action": action,
            "action_input": action_input,
            "title": title,
        }
        if todo_meta:
            payload["todo_meta"] = todo_meta
        if ask_data_call_id:
            payload["ask_data_call_id"] = ask_data_call_id
        if ask_data_reused:
            payload["ask_data_reused"] = True
        return _sse_event(payload)

    def chunk_text(text: str, max_len: int = 800) -> List[str]:
        if not text:
            return []
        chunks: List[str] = []
        start = 0
        while start < len(text):
            chunks.append(text[start : start + max_len])
            start += max_len
        return chunks

    def emit_tool_chunks(step_id: str, content: Any) -> List[str]:
        raw_chunks: List[str] = []
        if content is None:
            return raw_chunks
        parsed = None
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except Exception:
                parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("chunks"), list):
            for item in parsed["chunks"]:
                if not isinstance(item, dict):
                    continue
                output_type = item.get("output_type") or "text"
                payload = item.get("content")
                if output_type in ["code", "markdown"] and isinstance(payload, str):
                    # Send code and markdown as a single chunk — never split it.
                    raw_chunks.append(step_chunk(step_id, output_type, payload))
                elif output_type in ["text"] and isinstance(payload, str):
                    for chunk in chunk_text(payload, max_len=800):
                        raw_chunks.append(step_chunk(step_id, output_type, chunk))
                else:
                    raw_chunks.append(step_chunk(step_id, output_type, payload))
            return raw_chunks
        if isinstance(content, str) and content:
            for chunk in chunk_text(content, max_len=800):
                raw_chunks.append(step_chunk(step_id, "text", chunk))
        return raw_chunks

    def normalize_display_text(value: Optional[str]) -> Optional[str]:
        """Normalize a model-provided display field."""
        if not value:
            return None

        text = re.sub(r"\s+", " ", value).strip()
        text = re.sub(
            r"^(phase|status|状态|action\s+intention|action\s+reason)\s*:\s*",
            "",
            text,
            flags=re.I,
        ).strip()
        text = text.strip(" .,:;，。；：")
        if not text:
            return None
        return text

    def summarize_thought(
        thought: Optional[str], action: Optional[str] = None
    ) -> Optional[str]:
        """Fallback compressor when the model does not provide a short status."""
        if not thought:
            return None

        text = re.sub(r"\s+", " ", thought).strip()
        text = re.sub(r"^(thought|phase)\s*:\s*", "", text, flags=re.I).strip()
        if not text:
            return None

        split_markers = [
            r"\baction\b\s*:",
            r"\bobservation\b\s*:",
            r"\bphase\b\s*:",
            r"\bnow i need to\b",
            r"\bnext,?\b",
            r"\bthen\b",
            r"现在需要",
            r"下一步",
            r"接下来",
            r"然后",
        ]
        marker_pattern = "|".join(split_markers)
        text = re.split(marker_pattern, text, maxsplit=1, flags=re.I)[0].strip(
            " .,:;，。；："
        )

        prefixes = [
            "the user wants me to ",
            "i need to ",
            "i should ",
            "let me ",
            "i will ",
            "现在我需要",
            "我需要",
            "接下来我需要",
            "让我",
            "现在开始",
            "好的，",
            "好，",
        ]
        lowered = text.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix.lower()):
                text = text[len(prefix) :].strip(" .,:;，。；：")
                lowered = text.lower()
                break

        action_lower = (action or "").lower()
        if action_lower == "code_interpreter":
            return "正在生成分析代码"
        if action_lower == "html_interpreter":
            return "正在生成并渲染 HTML 报告"
        if action_lower == "todowrite":
            return "正在更新任务计划"
        if action_lower in {"execute_skill_script", "execute_skill_script_file"}:
            return "正在执行分析脚本"

        return text

    skills_dir = DEFAULT_SKILLS_DIR
    registry = get_registry()

    # Step 1: Pre-load skills
    load_skills_from_dir(skills_dir, recursive=True)
    def _skill_meta_path(meta: Any) -> str:
        return getattr(meta, "file_path", None) or f"{getattr(meta, 'name', '')}/SKILL.md"

    def _skill_meta_enabled(meta: Any) -> bool:
        try:
            file_path_value = _skill_meta_path(meta)
            resolved = (Path(skills_dir).expanduser().resolve() / file_path_value).resolve()
            if not resolved.is_file():
                return False
            return skill_enabled(file_path_value, skills_dir=skills_dir)
        except Exception:
            return True

    all_skills = [s for s in registry.list_skills() if _skill_meta_enabled(s)]

    # Step 2: Get business tools from ResourceManager
    rm = get_resource_manager(CFG.SYSTEM_APP)
    business_tools: List[Any] = []
    try:
        # Get all registered tool resources from ResourceManager
        tool_resources = rm._type_to_resources.get("tool", [])
        for reg_resource in tool_resources:
            if reg_resource.resource_instance is not None:
                business_tools.append(reg_resource.resource_instance)
    except Exception:
        pass  # If no business tools, continue with empty list

    react_state: Dict[str, Any] = {
        "skills_loaded": True,  # Skills are pre-loaded now
        "matched": None,
        "skill_prompt": None,
        "file_path": file_path,
        "conv_id": conv_id,
        "run_id": run_id,
        "session_state": session_state,
        "skill_maintenance": skill_maintenance,
        "cancel_event": cancel_event,
        "turn_id": uuid.uuid4().hex,
    }

    # Pre-select skill if skill_name provided in ext_info
    pre_matched_skill = None
    if skill_name:
        pre_matched_skill = registry.get_skill(skill_name)
        if not pre_matched_skill:
            # Try case-insensitive match
            for s in registry.list_skills():
                if s.name.lower() == skill_name.lower():
                    pre_matched_skill = registry.get_skill(s.name)
                    break
        if pre_matched_skill:
            react_state["matched"] = pre_matched_skill
            react_state["skill_prompt"] = pre_matched_skill.get_prompt()
            logger.info(f"Pre-selected skill from ext_info: {skill_name}")
        if pre_matched_skill and not _skill_meta_enabled(pre_matched_skill.metadata):
            logger.info("Pre-selected skill is disabled: %s", skill_name)
            pre_matched_skill = None
            react_state["matched"] = None
            react_state["skill_prompt"] = None

    # Build skills_context based on whether skill is pre-selected
    if pre_matched_skill:
        # User specified a skill: show only the selected skill
        skills_context = (
            f"- {pre_matched_skill.metadata.name}: "
            f"{pre_matched_skill.metadata.description}"
        )
    else:
        # User did not specify a skill: show all available skills
        skills_context = (
            "\n".join([f"- {s.name}: {s.description}" for s in all_skills])
            if all_skills
            else "No skills available."
        )

    def _mentions_excel(text: str) -> bool:
        lowered = text.lower()
        keywords = [
            "excel",
            "xlsx",
            "xls",
            "spreadsheet",
            "workbook",
            "sheet",
            "工作表",
            "表格",
            "电子表格",
        ]
        return any(keyword in lowered for keyword in keywords)

    def _is_excel_skill(meta) -> bool:
        name = (meta.name or "").lower()
        desc = (meta.description or "").lower()
        tags = [tag.lower() for tag in (meta.tags or [])]
        return any(
            token in name or token in desc or token in tags
            for token in ["excel", "xlsx", "xls", "spreadsheet"]
        )

    @tool(
        description="Select the most relevant skill based on user query from the "
        "available skills list in system prompt."
    )
    def select_skill(query: str) -> str:
        match_input = query or ""
        if react_state.get("file_path"):
            match_input = f"{match_input} excel xlsx spreadsheet file"
        matched = registry.match_skill(match_input)
        if (
            matched
            and _is_excel_skill(matched.metadata)
            and not (_mentions_excel(query) or react_state.get("file_path"))
        ):
            matched = None
        if matched and not _skill_meta_enabled(matched.metadata):
            matched = None
        react_state["matched"] = matched
        if matched:
            detail = (
                f"Matched: {matched.metadata.name} - {matched.metadata.description}"
            )
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": detail}]},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "chunks": [
                    {
                        "output_type": "text",
                        "content": "No skill matched; proceed without skill",
                    }
                ]
            },
            ensure_ascii=False,
        )

    @tool(
        description="Load skill content by skill name and file path. "
        "Returns the SKILL.md content of the specified skill. "
        '参数: {"skill_name": "技能名称", "file_path": "技能文件路径"}'
    )
    def load_skill(skill_name: str, file_path: str) -> str:
        """Load the skill content (SKILL.md) by skill name and file path.

        Args:
            skill_name: The name of the skill to load.
            file_path: The file path of the skill.
        """
        from dbgpt.agent.claude_skill import get_registry

        # Try to get skill from registry
        registry = get_registry()
        matched = registry.get_skill(skill_name)

        # If not found, try case-insensitive match
        if not matched:
            for s in registry.list_skills():
                if s.name.lower() == skill_name.lower():
                    matched = registry.get_skill(s.name)
                    break

        if not matched:
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": f"Skill '{skill_name}' not found",
                        }
                    ]
                },
                ensure_ascii=False,
            )

        # Update react_state for compatibility with existing logic
        react_state["matched"] = matched
        react_state["skill_prompt"] = matched.get_prompt()

        # Build response content
        chunks = [
            {
                "output_type": "text",
                "content": f"Skill: {matched.metadata.name}",
            },
            {
                "output_type": "text",
                "content": f"File path: {file_path}",
            },
            {"output_type": "text", "content": "---"},
        ]

        # Add skill content/prompt
        if matched.instructions:
            chunks.append({"output_type": "markdown", "content": matched.instructions})
        elif matched.prompt_template:
            prompt_text = (
                matched.prompt_template.template
                if hasattr(matched.prompt_template, "template")
                else str(matched.prompt_template)
            )
            chunks.append({"output_type": "markdown", "content": prompt_text})

        return json.dumps({"chunks": chunks}, ensure_ascii=False)

    @tool(description="Load uploaded file info if provided.")
    def load_file() -> str:
        if not react_state.get("file_path"):
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": "No file uploaded"}]},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "chunks": [
                    {"output_type": "text", "content": react_state["file_path"]},
                    {
                        "output_type": "text",
                        "content": "File path provided by user upload",
                    },
                ]
            },
            ensure_ascii=False,
        )

    @tool(description="Execute quick analysis on uploaded Excel/CSV file.")
    async def execute_analysis() -> str:
        from dbgpt.util.code.server import get_code_server

        matched = react_state.get("matched")
        if not react_state.get("file_path"):
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": "No file to analyze"}]},
                ensure_ascii=False,
            )
        if matched and not _is_excel_skill(matched.metadata):
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": "Selected skill is not for Excel analysis",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        code_server = await get_code_server(CFG.SYSTEM_APP)
        analysis_code = """
import json
import pandas as pd

file_path = r"{file_path}"
if file_path.lower().endswith((".xls", ".xlsx")):
    df = pd.read_excel(file_path)
else:
    df = pd.read_csv(file_path)
summary = {{
    "shape": list(df.shape),
    "columns": list(df.columns),
    "dtypes": {{col: str(dtype) for col, dtype in df.dtypes.items()}},
    "head": df.head(5).to_dict(orient="records"),
}}
print(json.dumps(summary, ensure_ascii=False))
""".format(file_path=react_state["file_path"])
        result = await code_server.exec(analysis_code, "python")
        output_text = (
            result.output.decode("utf-8") if isinstance(result.output, bytes) else ""
        )
        chunks: List[Dict[str, Any]] = [
            {"output_type": "code", "content": analysis_code.strip()}
        ]
        if output_text:
            try:
                summary = json.loads(output_text)
                chunks.append({"output_type": "json", "content": summary})
                head_rows = summary.get("head")
                columns = summary.get("columns")
                if isinstance(head_rows, list) and isinstance(columns, list):
                    chunks.append(
                        {
                            "output_type": "table",
                            "content": {
                                "columns": [
                                    {"title": col, "dataIndex": col, "key": col}
                                    for col in columns
                                ],
                                "rows": head_rows,
                            },
                        }
                    )
                numeric_columns = [
                    col
                    for col, dtype in (summary.get("dtypes") or {}).items()
                    if "int" in dtype or "float" in dtype
                ]
                if numeric_columns and isinstance(head_rows, list):
                    series_col = numeric_columns[0]
                    data = [
                        {"x": idx + 1, "y": row.get(series_col)}
                        for idx, row in enumerate(head_rows)
                        if row.get(series_col) is not None
                    ]
                    if data:
                        chunks.append(
                            {
                                "output_type": "chart",
                                "content": {
                                    "data": data,
                                    "xField": "x",
                                    "yField": "y",
                                },
                            }
                        )
            except Exception:
                chunks.append({"output_type": "text", "content": output_text})
        return json.dumps({"chunks": chunks}, ensure_ascii=False)

    @tool(description="Resolve required tools for the selected skill.")
    def load_tools() -> str:
        from dbgpt.agent.resource.base import AgentResource, ResourceType

        matched = react_state.get("matched")
        rm = get_resource_manager(CFG.SYSTEM_APP)
        required_tools = matched.metadata.required_tools if matched else []
        if not required_tools:
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": "No required tools specified",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        loaded = []
        failed = []
        for tool_name in required_tools:
            try:
                rm.build_resource_by_type(
                    ResourceType.Tool.value,
                    AgentResource(type=ResourceType.Tool.value, value=tool_name),
                )
                loaded.append(tool_name)
            except Exception as e:
                failed.append(f"{tool_name} ({e})")
        chunks = []
        if loaded:
            chunks.append(
                {"output_type": "text", "content": f"Loaded: {', '.join(loaded)}"}
            )
        if failed:
            chunks.append(
                {"output_type": "text", "content": f"Failed: {', '.join(failed)}"}
            )
        return json.dumps({"chunks": chunks}, ensure_ascii=False)

    @tool(description="Execute a tool by name with JSON args.")
    async def execute_tool(tool_name: str, args: dict) -> str:
        from dbgpt.agent.resource.base import AgentResource, ResourceType

        try:
            from dbgpt.agent.resource.connector.confirmation import (
                _PENDING_CONFIRMATIONS,
            )
            from dbgpt.agent.resource.connector.manager import (
                ConnectorManager as _ConnectorManager,
            )

            _cm = CFG.SYSTEM_APP.get_component(
                "connector_manager", _ConnectorManager, default_component=None
            )
            if _cm is not None:
                _interceptor = _cm.get_confirmation_interceptor()
                _registry = _cm.get_confirmation_registry()
                if _interceptor.should_confirm(tool_name, args):
                    import asyncio as _asyncio
                    import uuid as _uuid

                    _confirm_id = str(_uuid.uuid4())
                    _registry.register(_confirm_id)
                    _PENDING_CONFIRMATIONS[_confirm_id] = {
                        "confirm_id": _confirm_id,
                        "tool_name": tool_name,
                        "args_summary": _interceptor._summarize_args(args),
                        "message": f"即将执行写操作 {tool_name}，是否确认？",
                        "timeout": 300,
                    }
                    try:
                        _approved = await _asyncio.wait_for(
                            _registry.wait_for(_confirm_id), timeout=300
                        )
                    except _asyncio.TimeoutError:
                        _approved = False
                    finally:
                        _PENDING_CONFIRMATIONS.pop(_confirm_id, None)
                    if not _approved:
                        return json.dumps(
                            {
                                "chunks": [
                                    {
                                        "output_type": "text",
                                        "content": "用户拒绝了此操作，工具执行已取消。",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
        except Exception:
            pass

        rm = get_resource_manager(CFG.SYSTEM_APP)
        try:
            # Primary path: lookup via ResourceManager (lazy-registered business tools)
            tool_resource = rm.build_resource_by_type(
                ResourceType.Tool.value,
                AgentResource(type=ResourceType.Tool.value, value=tool_name),
            )
            tool_pack = ToolPack([tool_resource])
            result = await tool_pack.async_execute(resource_name=tool_name, **args)
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": str(result)}]},
                ensure_ascii=False,
            )
        except Exception as primary_exc:
            # Fallback: if ResourceManager doesn't have the tool, try
            # ConnectorManager active packs.  This handles the case where LLM
            # mistakenly routes an MCP connector tool through execute_tool
            # instead of calling it directly.
            try:
                from dbgpt.agent.resource.connector.manager import (
                    ConnectorManager as _ConnectorManager,
                )

                _cm = CFG.SYSTEM_APP.get_component(
                    "connector_manager",
                    _ConnectorManager,
                    default_component=None,
                )
                if _cm is not None:
                    for _cid, _pack in _cm._active_packs.items():
                        if tool_name in _pack._resources:
                            result = await _pack.async_execute(
                                resource_name=tool_name, **args
                            )
                            logger.info(
                                "execute_tool dispatched '%s' via "
                                "ConnectorManager fallback (connector=%s). "
                                "Prefer direct Action call for connector "
                                "tools.",
                                tool_name,
                                _cid,
                            )
                            return json.dumps(
                                {
                                    "chunks": [
                                        {
                                            "output_type": "text",
                                            "content": str(result),
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
            except Exception as fallback_exc:
                logger.warning(
                    "execute_tool fallback to ConnectorManager failed for '%s': %s",
                    tool_name,
                    fallback_exc,
                )
                # When fallback found the tool but execution failed, surface
                # that error to the LLM (more actionable than the
                # ResourceManager primary error)
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": (
                                    f"Tool execute failed: {fallback_exc} "
                                    f"(primary lookup error: {primary_exc})"
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            # Both lookups returned None — primary path's tool-not-found error wins
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": f"Tool execute failed: {primary_exc}",
                        }
                    ]
                },
                ensure_ascii=False,
            )

    # ── Import built-in tools from tools/ directory ──
    from dbgpt_app.openapi.api_v1.tools import (
        ask_data_capability_summary,
        make_ask_data_tools,
        make_code_interpreter,
        make_execute_analysis,
        make_execute_skill_script_file,
        make_execute_tool,
        make_html_interpreter,
        make_load_file,
        make_load_skill,
        make_load_tools,
        make_question,
        make_shell_interpreter,
        make_todowrite,
    )
    # ── Build tool instances via factory functions ──────────────────────────
    # (Inline @tool definitions have been moved to tools/ directory.)
    # Local helper aliases used by the SSE loop are defined below.

    # ── Stream queue (created early so question tool can use it) ────────
    stream_queue: asyncio.Queue = asyncio.Queue()

    async def stream_callback(event_type: str, payload: Dict[str, Any]) -> None:
        await stream_queue.put({"type": event_type, **payload})

    # ── Build tool instances from tools/ directory ───────────────────────
    _todo_list: List[Dict[str, str]] = []
    load_skill_tool = make_load_skill(react_state)
    load_file_tool = make_load_file(react_state)
    execute_analysis_tool = make_execute_analysis(react_state)
    load_tools_tool = make_load_tools(react_state)
    execute_tool_tool = make_execute_tool(react_state)
    code_interpreter_tool = make_code_interpreter(react_state)
    shell_interpreter_tool = make_shell_interpreter(react_state)
    html_interpreter_tool = make_html_interpreter(react_state, DEFAULT_SKILLS_DIR)
    todowrite_tool = make_todowrite(_todo_list, stream_callback)
    question_tool = make_question(react_state, stream_callback)
    ask_data_tools = []
    ask_data_prompt_context = ""
    scene_query_agent = None
    if ask_data_enabled:
        from dbgpt_app.scene.ask_data.agents import SceneQueryAgent
        from dbgpt_app.scene.ask_data.security import AskDataPrincipal

        llm_client = DefaultLLMClient(
            CFG.SYSTEM_APP.get_component(
                ComponentType.WORKER_MANAGER_FACTORY, WorkerManagerFactory
            ).create(),
            auto_convert_message=True,
        )

        async def generate_scene_query_spec(prompt: str) -> str:
            models = await llm_client.models()
            model_name = dialogue.model_name or (models[0].model if models else None)
            if not model_name:
                raise RuntimeError("No models available for SceneQueryAgent")
            request = ModelRequest.build_request(
                model_name,
                messages=[
                    ModelMessage(
                        role=ModelMessageRoleType.HUMAN,
                        content=prompt,
                    )
                ],
                temperature=0,
            )
            response = await llm_client.generate(request)
            if not response.success or not response.has_text:
                raise RuntimeError("SceneQueryAgent model generation failed")
            return response.text

        scene_query_agent = SceneQueryAgent(generate_scene_query_spec)
        ask_data_context_budget = await _load_context_budget_config(
            llm_client, dialogue.model_name
        )
        ask_data_max_result_tokens = max(
            1024, int(ask_data_context_budget.max_context_tokens * 0.5)
        )

        role = getattr(user_token, "role", None)
        ask_data_principal = AskDataPrincipal(
            user_id=(getattr(user_token, "user_id", None) or dialogue.user_name or "anonymous"),
            roles=frozenset({str(role)}) if role else frozenset(),
        )
        ask_data_query_tool, ask_data_capabilities_tool = make_ask_data_tools(
            react_state,
            stream_callback,
            ask_data_principal,
            scene_query_agent,
            max_result_tokens=ask_data_max_result_tokens,
        )
        ask_data_tools = [ask_data_query_tool, ask_data_capabilities_tool]
        capability_summary = ask_data_capability_summary(ask_data_principal, user_input)
        ask_data_tool_schema_context = _render_direct_tool_schema_prompt(
            "AskData Direct Tool Schema",
            ask_data_tools,
            extra_rules=[
                "- `ask_data_query` Action Input MUST be `{\"question\": \"<natural-language business question>\"}`.",
                "- Never call `ask_data_query` with `{\"query\": ...}`; `query` is not a valid parameter name.",
                "- Do not pass SQL, table names, field names, data-source names, or Snapshot IDs to `ask_data_query`.",
                "- After `ask_data_query` returns `query_complete: true`, if no extra tool is explicitly needed, call `terminate` immediately with `Action Input: {\"result\": \"final answer\"}`. Do not answer in plain text and do not call `ask_data_query` again with the same question.",
                "- A broader follow-up query is allowed only when the user's explicit request cannot be answered by the returned columns.",
                "- If a Recent AskData Result block is available and the latest request only asks to report, visualize, summarize, or format already queried data, do not call `ask_data_query` again.",
            ],
        )
        ask_data_prompt_context = f"""
## Trusted Business Data Query
{capability_summary}
- For covered business metrics, statistics, contracts, projects, departments, or time-based aggregation, you MUST call `ask_data_query` unless a Recent AskData Result block already answers the data scope and the latest request only asks for reporting, visualization, summarization, or formatting.
- `ask_data_query` accepts only a natural-language question. Never pass SQL, table names, fields, views, data-source names, or Snapshot IDs.
- Before calling `ask_data_query`, rewrite the user's latest request into a self-contained natural-language business question if chat history is needed for pronouns, ellipses, or follow-up references.
- When AskData returns data JSON, use it as the evidence for your final answer. You may call other built-in tools afterwards when the user asked for formatting or visualization.
- When an AskData Observation contains `query_complete: true`, the query requirement is satisfied. If no extra tool is explicitly needed, the next response MUST be `Action: terminate` with the full answer inside `Action Input: {{"result": "final answer"}}`. Do not output the final answer as plain Markdown, and never call `ask_data_query` again with the same question in this turn.
- Do not broaden a successful query merely to add fields, rankings, or analysis that the user did not request.
- If a "Recent AskData Result From This Session" block is provided and the latest user request asks for a report, visualization, summary, or formatting based on already queried data, this rule overrides the general `ask_data_query` rule: reuse that block directly and do NOT call `ask_data_query` again unless the user requests refreshed data, changes filters/scope, or the provided data is insufficient.

{ask_data_tool_schema_context}
"""
    # Keep local aliases for backward compatibility (SSE loop references these names)
    execute_skill_script_file_tool = make_execute_skill_script_file(react_state)

    def _active_todo_index() -> Optional[int]:
        for idx, item in enumerate(_todo_list):
            if item.get("status") == "in_progress":
                return idx
        return None

    def _normalize_text(value: Optional[str]) -> str:
        return (value or "").strip().lower()

    def _is_report_like(text: str) -> bool:
        keywords = [
            "report",
            "html",
            "dashboard",
            "visual",
            "visualization",
            "图表",
            "报告",
            "报表",
            "可视化",
            "渲染",
            "展示",
        ]
        return any(keyword in text for keyword in keywords)

    def should_advance_todo(
        action_name: Optional[str],
        thought: Optional[str] = None,
        observation_text: Optional[str] = None,
    ) -> bool:
        """Heuristically decide whether the current todo is actually complete."""
        if not _todo_list:
            return False

        active_idx = _active_todo_index()
        if active_idx is None:
            return False

        action_lower = _normalize_text(action_name)
        thought_lower = _normalize_text(thought)
        observation_lower = _normalize_text(observation_text)
        current_todo = _normalize_text(_todo_list[active_idx].get("content"))
        next_todo = (
            _normalize_text(_todo_list[active_idx + 1].get("content"))
            if active_idx + 1 < len(_todo_list)
            else ""
        )

        transition_markers = [
            "next step",
            "now i need",
            "now i should",
            "now let me",
            "现在需要",
            "下一步",
            "接下来",
            "然后",
            "接着",
            "接下来我将",
        ]
        if next_todo and any(marker in thought_lower for marker in transition_markers):
            if any(token and token in thought_lower for token in next_todo.split()):
                return True

        if action_lower == "html_interpreter":
            return True

        if action_lower in {
            "load_skill",
            "execute_skill_script",
            "execute_skill_script_file",
        }:
            if next_todo and next_todo in thought_lower:
                return True
            if _is_report_like(next_todo) and _is_report_like(thought_lower):
                return True

            if current_todo and not _is_report_like(current_todo):
                completion_markers = [
                    "enough information",
                    "collected enough",
                    "gathered enough",
                    "completed metadata",
                    "obtained the overview",
                    "获取了足够",
                    "已经获取了足够",
                    "已完成",
                    "已获取",
                    "整理一下",
                ]
                if any(marker in thought_lower for marker in completion_markers):
                    return True

            return False

        if action_lower in {"code_interpreter", "execute_tool", "shell_interpreter"}:
            if _is_report_like(current_todo):
                return False
            if next_todo and any(
                token and token in thought_lower for token in next_todo.split()
            ):
                return True
            if observation_lower and _is_report_like(observation_lower):
                return True

        return False

    def advance_todo_list() -> Optional[List[Dict[str, str]]]:
        """Advance one todo when the current task appears substantively complete."""
        if not _todo_list:
            return None

        changed = False
        active_idx = _active_todo_index()

        if active_idx is not None:
            _todo_list[active_idx]["status"] = "completed"
            changed = True
            for next_item in _todo_list[active_idx + 1 :]:
                if next_item.get("status") == "pending":
                    next_item["status"] = "in_progress"
                    break
        else:
            for item in _todo_list:
                if item.get("status") == "pending":
                    item["status"] = "in_progress"
                    changed = True
                    break

        return list(_todo_list) if changed else None

    if not ask_data_enabled:
        llm_client = DefaultLLMClient(
            CFG.SYSTEM_APP.get_component(
                ComponentType.WORKER_MANAGER_FACTORY, WorkerManagerFactory
            ).create(),
            auto_convert_message=True,
        )
    if dialogue.model_name:
        llm_config = LLMConfig(
            llm_client=llm_client,
            llm_strategy=LLMStrategyType.Priority,
            strategy_context=json.dumps([dialogue.model_name]),
        )
    else:
        llm_config = LLMConfig(llm_client=llm_client)

    react_max_steps = _react_max_steps_for(user_input, pre_matched_skill)
    react_llm_error_limit = _react_llm_error_limit()
    react_parse_error_limit = _react_parse_error_limit()
    react_same_action_repeat_limit = _react_same_action_repeat_limit()

    REACT_AGENT_RUNS[run_id] = {
        "conv_uid": conv_id,
        "cancel_event": cancel_event,
        "max_steps": react_max_steps,
    }
    pending_ask_data = REACT_ASK_DATA_PENDING.get(conv_id)
    if pending_ask_data and pending_ask_data.get("user_id") == (dialogue.user_name or "anonymous"):
        from dbgpt_app.openapi.api_v1.tools import reply_to_ask_data_query
        from dbgpt_app.scene.ask_data.security import AskDataPrincipal

        role = getattr(user_token, "role", None)
        principal = AskDataPrincipal(
            user_id=dialogue.user_name or "anonymous",
            roles=frozenset({str(role)}) if role else frozenset(),
        )
        yield _sse_event(
            {
                "type": "ask_data.stage",
                "stage": "clarification",
                "title": "正在补充业务查询条件",
                "detail": "继续上一轮业务问数",
            }
        )
        payload = await reply_to_ask_data_query(
            query_id=pending_ask_data["query_id"],
            answer=user_input,
            principal=principal,
            conversation_id=conv_id,
            scene_query_agent=scene_query_agent,
        )
        if payload.get("status") == "clarification_required":
            REACT_ASK_DATA_PENDING[conv_id] = {
                "query_id": str(payload.get("query_id") or pending_ask_data["query_id"]),
                "user_id": principal.user_id,
            }
            yield _sse_event(
                {
                    "type": "ask_data.clarification",
                    "query_id": payload.get("query_id"),
                    "clarification": payload.get("clarification"),
                }
            )
        else:
            REACT_ASK_DATA_PENDING.pop(conv_id, None)
        yield _sse_event({"type": "ask_data.result", **payload})
        yield _sse_event({"type": "final", "content": payload.get("answer") or "业务查询已处理。"})
        yield _sse_event({"type": "done"})
        REACT_AGENT_RUNS.pop(run_id, None)
        return
    if conv_id in REACT_AGENT_MEMORY_CACHE:
        gpt_memory = REACT_AGENT_MEMORY_CACHE[conv_id]
    else:
        gpt_memory = GptsMemory(
            plans_memory=DefaultGptsPlansMemory(),
            message_memory=MetaDbGptsMessageMemory(),
        )
        gpt_memory.init(conv_id, enable_vis_message=False)
        REACT_AGENT_MEMORY_CACHE[conv_id] = gpt_memory
    agent_memory = AgentMemory(gpts_memory=gpt_memory)

    conv_serve = ConversationServe.get_instance(CFG.SYSTEM_APP)
    storage_conv = StorageConversation(
        conv_uid=conv_id,
        chat_mode=dialogue.chat_mode or "chat_react_agent",
        user_name=dialogue.user_name,
        sys_code=dialogue.sys_code,
        summary=dialogue.user_input,
        app_code=dialogue.app_code,
        conv_storage=conv_serve.conv_storage,
        message_storage=conv_serve.message_storage,
    )

    if pre_matched_skill:
        record_active_skill(
            session_state,
            pre_matched_skill.metadata.name,
            getattr(pre_matched_skill.metadata, "file_path", None),
            source="selected_skill",
        )
    elif not session_state.get("active_skill"):
        inferred_skill = infer_active_skill_from_messages(
            storage_conv.messages,
            skills_dir=skills_dir,
        )
        if inferred_skill:
            record_active_skill(
                session_state,
                inferred_skill.get("name"),
                inferred_skill.get("path"),
                source=inferred_skill.get("source") or "history_inference",
            )

    recent_ask_data_context = _render_recent_ask_data_context(storage_conv.messages)
    session_context = render_react_session_context(
        session_state,
        storage_conv.messages,
        current_user_input=user_input,
    )
    storage_conv.save_to_storage()
    storage_conv.start_new_round()
    storage_conv.add_user_message(user_input)
    # Persist the human turn before the long-running stream completes.  The
    # assistant/view payload is still written at terminal state, but a page
    # reload or route change can now restore the latest user input instead of
    # falling back to the previous completed round.
    storage_conv.save_to_storage()
    context = AgentContext(
        conv_id=conv_id,
        gpts_app_code="react_agent",
        gpts_app_name="ReAct",
        language="zh",
        temperature=dialogue.temperature or 0.2,
        enable_context_management=True,
    )

    file_context = ""
    if file_path:
        file_context = f"""
## User Uploaded File
- File path: {file_path}
- Analyze this file if needed for the user's request.
"""

    skill_prompt_context = ""
    execution_instruction = ""
    if pre_matched_skill and react_state.get("skill_prompt"):
        skill_template = react_state["skill_prompt"]
        skill_text = (
            skill_template.template
            if hasattr(skill_template, "template")
            else str(skill_template)
        )
        skill_prompt_context = f"""
## 已加载技能指令（{pre_matched_skill.metadata.name}）
以下是用户选择的技能的完整指令，请严格按照这些指令进行操作：

{skill_text}
"""
        execution_instruction = f"""
## 执行要求
1. 用户已明确选择技能：{pre_matched_skill.metadata.name}
2. 你必须严格按照上述技能指令的步骤执行
3. 阅读技能指令，理解每一步需要调用的工具
4. 按顺序执行工具调用，完成技能目标
"""

    # Build a hint listing all images currently available in
    # STATIC_MESSAGE_IMG_PATH so the LLM can reference them correctly in
    # html_interpreter.
    # NOTE: This is the initial hint at prompt build time. Images generated
    # during the session are tracked in react_state["generated_images"] and
    # appended to html_interpreter output dynamically.
    available_images_hint = ""

    # Check if skill is pre-selected to use simplified prompt
    is_skill_mode = pre_matched_skill is not None and not skill_maintenance
    _skill_name = pre_matched_skill.metadata.name if pre_matched_skill else "skill"

    # Inject connector tools — only the ones the user explicitly selected.
    connector_tool_extras: List[Any] = []
    try:
        from dbgpt.agent.resource.connector.manager import (
            ConnectorManager as _ConnectorManager,
        )

        _connector_manager = CFG.SYSTEM_APP.get_component(
            "connector_manager", _ConnectorManager, default_component=None
        )
        if _connector_manager is not None and connector_ids:
            connector_tool_extras, _missing = _select_connector_tools(
                connector_ids, _connector_manager
            )
            for _mid in _missing:
                logger.warning(
                    "_react_agent_stream: connector_id %s not active, skipping",
                    _mid,
                )
            if connector_tool_extras:
                logger.info(
                    "_react_agent_stream: injected %d connector tool pack(s) "
                    "(selected: %d)",
                    len(connector_tool_extras),
                    len(connector_ids),
                )
    except Exception:
        pass  # graceful degradation — connector tools are optional

    if is_skill_mode:
        # Simplified prompt for skill mode - only skill-related tools +
        # html_interpreter
        workflow_prompt = f"""
You are the Datrix intelligent assistant, executing the skill task selected by the user.
Please always response in the same language as the user's input language.

## Autonomous Decision Principles
1. Strictly follow the instructions of the loaded skill.
2. For each step, output Thought -> Action Intention -> Action Reason -> Action
   -> Action Input.
3. Wait for the system to return Observation before deciding on the next step.
4. **[Mandatory Rule] If the task requires generating an analysis report, you MUST
call `html_interpreter` for HTML rendering.** By default, generate complete HTML
code yourself and pass it via the `html` parameter (include DOCTYPE, html, head,
body, styles, and all content). Only use `template_path` mode if the skill
explicitly provides HTML templates in its `templates/` directory and its
documentation references them. When using template mode, provide ALL required
placeholders in the `data` dictionary.
5. If the task does not require generating a report, directly call terminate to
return the final result. The Action Input format must be
{{"result": "final answer"}}.

{skill_prompt_context}
{execution_instruction}

## Skill Execution Norms
### Resource Usage
- **Need to execute skill script** -> Use `execute_skill_script_file` with
parameters {{"skill_name": "skill name", "script_file_name": "script file name",
"args": {{parameters}}}}. This tool will automatically handle image copying and
data recording.
- **Need to understand indicator definitions/analysis framework** -> Use
`get_skill_resource` and specify the `references/xxx.md` path to read the
reference document.
- **Encounter image file** -> If the model does not support image input, it will
return an error prompt.
- **Need to generate report** -> Call `html_interpreter`. **Default: directly pass
complete HTML via the `html` parameter** — you generate the full HTML code
yourself (including `<!DOCTYPE html>`, `<html>`, `<head>`, `<body>`, styles,
content). The HTML can be as long as needed. **Only use `template_path` if the
skill explicitly provides HTML templates in its `templates/` directory and its
documentation tells you to use them.** Do not use `code_interpreter` to generate
the report.

## Available Tools Description
1. **execute_skill_script_file** (recommended for executing skill scripts): Execute
script files in the skills scripts directory, automatically handling
post-processing such as copying images to the static directory and recording
calculation results.
   Parameters: {{"skill_name": "skill name", "script_file_name": "script file
name", "args": {{parameters}}}}
   - Example: {{"skill_name": "{_skill_name}",
"script_file_name": "calculate_ratios.py",
"args": {{"input_data": "..."}}}}
   - **Must use this tool when executing skill scripts**, do not use
shell_interpreter.
2. **get_skill_resource**: Read reference documents, configurations, templates, and
other non-script resource files in the skill.
   Parameters: {{"skill_name": "skill name", "resource_path": "resource path"}}
   - Read reference document: {{"skill_name": "{_skill_name}",
"resource_path": "references/analysis_framework.md"}}
   - Note: For generating reports, prefer using html_interpreter directly with the
`html` parameter. Only use template_path if the skill explicitly provides
templates.
3. **execute_skill_script**: Execute the inline script defined in the skill
(backup). Parameters: {{"skill_name": "skill name", "script_name": "script name",
"args": {{"parameter name": "parameter value"}}}}
4. **shell_interpreter**: Execute shell/bash commands (only for non-skill script
system commands, such as ls, cat, etc.).
   Parameters: {{"code": "shell command"}}
   - Each call is independent and does not retain state. If multi-step operations
are needed, use `&&` or `;` to connect commands.
   - **Note: Do not use this tool to execute skill scripts**, as it will not
automatically handle images and data recording.
5. **html_interpreter**: Render HTML as an interactive web report. This is the ONLY
way to display reports on the right panel.
   **Default usage (recommended)**: {{"html": "<html>your complete HTML code</html>",
"title": "report title"}}
   - Generate complete HTML yourself (DOCTYPE, html, head, body, CSS styles,
content). No length limit.
   - **Do not** use code_interpreter to write HTML. Directly pass the HTML string
to this tool.
   **Template mode (only when skill has templates/)**: {{"template_path":
"skill-name/templates/template.html", "data": {{"KEY": "value"}}, "title": "title"}}
   - Only use this if the skill's documentation explicitly provides template paths.
If template_path returns "Template not found", immediately switch to the default
`html` parameter usage.
   {available_images_hint}
6. **todowrite**: Create and manage a structured task list. Use for complex tasks
(3+ steps) to plan and track progress. Pass the FULL list every time. Each item:
{{"content": "description", "status": "pending|in_progress|completed|cancelled",
"priority": "high|medium|low"}}. Only ONE task in_progress at a time.
IMPORTANT: You MUST call todowrite again after EACH task completes to update status.
The user sees progress in real time — never skip an update.
Parameters: {{"todos": [{{...}}]}}
7. **question**: Ask the user a question and wait for their response. Use this tool
   when you need user input, clarification, or a decision to proceed. The tool blocks
   until the user answers.
   Parameters: {{"questions": [{{"question": "...", "header": "...", "options": [
   {{"label": "...", "description": "..."}}, ...]}}]}}. Set multiple=true to allow
   multiple selections. The tool returns the user's selected answers.
8. **terminate**: Return the final answer when the task is completed. Action Input
must be {{"result": "your final answer content"}}.

## Task Management
For complex tasks that require 3 or more steps, use the `todowrite` tool to create
a structured task plan BEFORE starting work. This helps users track your progress.
- Call `todowrite` with the FULL todo list (all items) each time you update.
- Mark exactly ONE task as `in_progress` at a time.
- Mark tasks `completed` immediately after finishing each one.
- Do NOT use todowrite for simple single-step tasks.

CRITICAL: You MUST call `todowrite` to update the task list at EVERY transition:
1. BEFORE starting a task: mark it `in_progress` (call todowrite)
2. AFTER finishing a task: mark it `completed` AND mark the next one
   `in_progress` (call todowrite)
3. Never skip updating — the user sees this progress in real time.
Example flow for 3 tasks:
- Create plan: [task1=in_progress, task2=pending, task3=pending] → call todowrite
- Finish task1: [task1=completed, task2=in_progress, task3=pending] → call todowrite
- Finish task2: [task1=completed, task2=completed, task3=in_progress] → call todowrite
- Finish task3: [task1=completed, task2=completed, task3=completed] → call todowrite

{file_context}
{session_context}
{ask_data_prompt_context}
{recent_ask_data_context}
## ReAct Output Format
Must output for each interaction round:
Thought: Analyze current task status and think about what to do next
Action Intention: What this step will do, plain text, MUST be concise and fit in
<= 18 Chinese chars or <= 8 English words. If too long, rewrite shorter.
Do not use ellipsis.
Action Reason: Why this action is needed now, plain text, MUST be concise and fit in
<= 30 Chinese chars or <= 12 English words. If too long, rewrite shorter.
Do not use ellipsis.
Action: The selected tool name (must be one of the tools listed above)
Action Input: The JSON format of tool parameters
For final answers, use `Action: terminate` and put the complete user-facing answer
only inside `Action Input.result`.
""".strip()

        tool_pack = ToolPack(
            [
                execute_skill_script,
                get_skill_resource,
                execute_skill_script_file_tool,
                shell_interpreter_tool,
                html_interpreter_tool,
                todowrite_tool,
                question_tool,
                Terminate(),
            ]
            + ask_data_tools
            + business_tools
            + connector_tool_extras
        )
    else:
        # Full prompt with all tools when no skill is pre-selected
        workflow_prompt = f"""
You are the Datrix intelligent assistant, capable of autonomously selecting tools
to solve problems based on user tasks.
Please always response in the same language as the user's input language.

## Autonomous Decision Principles
1. Carefully analyze the user's task requirements.
2. Autonomously select required tools based on requirements (do not follow a fixed
order, select as needed).
3. For each step, output Thought -> Action Intention -> Action Reason -> Action
   -> Action Input.
4. Wait for the system to return Observation before deciding on the next step.
5. When the task is completed, call the terminate tool to return the final result.
The Action Input format must be {{"result": "final answer"}}.
6. **[Mandatory Rule] If there is a requirement for an analysis report, you MUST call
`html_interpreter` for HTML rendering. When the user requests generating a webpage,
HTML report, or interactive report, the final presentation step must call
`html_interpreter` to render it. It is forbidden to output HTML using only
`code_interpreter` and then directly terminate. Correct process: code_interpreter
writes to .html file -> html_interpreter(file_path=...) renders -> terminate.**

## Task Management
For complex tasks that require 3 or more steps, use the `todowrite` tool to create
a structured task plan BEFORE starting work. This helps users track your progress.
- Call `todowrite` with the FULL todo list (all items) each time you update.
- Mark exactly ONE task as `in_progress` at a time.
- Mark tasks `completed` immediately after finishing each one.
- Do NOT use todowrite for simple single-step tasks.

CRITICAL: You MUST call `todowrite` to update the task list at EVERY transition:
1. BEFORE starting a task: mark it `in_progress` (call todowrite)
2. AFTER finishing a task: mark it `completed` AND mark the next one
   `in_progress` (call todowrite)
3. Never skip updating — the user sees this progress in real time.
Example flow for 3 tasks:
- Create plan: [task1=in_progress, task2=pending, task3=pending] → call todowrite
- Finish task1: [task1=completed, task2=in_progress, task3=pending] → call todowrite
- Finish task2: [task1=completed, task2=completed, task3=in_progress] → call todowrite
- Finish task3: [task1=completed, task2=completed, task3=completed] → call todowrite

## Available Skills List (Pre-loaded)
{skills_context}

## Skill Execution Norms (Important)
When using a skill, the following rules must be followed:

### 1. Understand the Workflow
After loading the skill, carefully read the **Core Workflow** section in SKILL.md
and execute it in order. If a step explicitly states conditions to skip (such as
when user intent is clear), directly skip to the next step; do not force the
execution of every step. Prioritize producing results quickly, and perform
iterative optimization in subsequent steps.

### 2. Resource Usage Timing
- **Need to calculate/process data** -> Use `execute_skill_script_file` to execute
scripts in the skill's scripts directory (this tool automatically handles images
and data recording). Parameters are {{"skill_name": "skill name",
"script_file_name": "script.py", "args": {{parameters}}}}.
- **Need to understand indicator definitions/analysis framework** -> Use
`get_skill_resource` and specify the `references/xxx.md` path to read the
reference document.
- **Encounter image file** -> If the model does not support image input, it will
return an error prompt.

### 3. Execution Order
Complete each workflow step before moving to the next. Do not mix multiple tool
calls in the same step.

### 4. Special Scenarios
- For report generation: Same as the principle above, must finally call
`html_interpreter` to render.

## Available Tools Description
1. **load_skill**: Load skill content by skill name and file path.
Parameters: {{"skill_name": "skill name", "file_path": "skill file path"}}
2. **execute_skill_script_file**: Execute script files in the skill's scripts
directory. Parameters: {{"skill_name": "skill name",
"script_file_name": "script file name", "args": {{parameters}}}}
3. **get_skill_resource**: Read reference documents in the skill.
Parameters: {{"skill_name": "skill name", "resource_path": "resource path"}}
4. **execute_skill_script**: Execute the inline script defined in the skill.
Parameters: {{"skill_name": "skill name", "script_name": "script name",
"args": {{parameters}}}}
5. **shell_interpreter**: Execute shell/bash commands.
Parameters: {{"code": "shell command"}}
6. **code_interpreter**: Execute arbitrary Python code.
Parameters: {{"code": "python code string"}}
7. **load_file**: Load uploaded file info. Parameters: none.
8. **execute_analysis**: Execute quick analysis on uploaded Excel/CSV file.
Parameters: none.
9. **load_tools**: Resolve required tools for the selected skill. Parameters: none.
10. **execute_tool**: Execute a tool by name with JSON args.
Parameters: {{"tool_name": "tool name", "args": {{parameters}}}}
11. **html_interpreter**: Render HTML as an interactive web report (the ONLY way
to display reports on the right panel). Default usage:
{{"html": "<html>complete HTML code</html>", "title": "title"}}. Template mode:
{{"template_path": "skill/templates/xxx.html", "data": {{...}}, "title": "title"}}.
File mode: {{"file_path": "/path/to/report.html"}}
12. **todowrite**: Create and manage a structured task list. Use for complex tasks
(3+ steps) to plan and track progress. Pass the FULL list every time. Each item:
{{"content": "description", "status": "pending|in_progress|completed|cancelled",
"priority": "high|medium|low"}}. Only ONE task in_progress at a time.
IMPORTANT: You MUST call todowrite again after EACH task completes to update status.
The user sees progress in real time — never skip an update.
Parameters: {{"todos": [{{...}}]}}
13. **question**: Ask the user a question and wait for their response. Use this tool
   when you need user input, clarification, or a decision to proceed. The tool blocks
   until the user answers.
   Parameters: {{"questions": [{{"question": "...", "header": "...", "options": [
   {{"label": "...", "description": "..."}}, ...]}}]}}. Set multiple=true to allow
   multiple selections. The tool returns the user's selected answers.
14. **terminate**: Finish the task. Parameters: {{"result": "final answer"}}

{file_context}
{session_context}
{ask_data_prompt_context}
{recent_ask_data_context}

## ReAct Output Format
Must output for each interaction round:
Thought: Analyze current task status and think about what to do next
Action Intention: What this step will do, plain text, MUST be concise and fit in
<= 18 Chinese chars or <= 8 English words. If too long, rewrite shorter.
Do not use ellipsis.
Action Reason: Why this action is needed now, plain text, MUST be concise and fit in
<= 30 Chinese chars or <= 12 English words. If too long, rewrite shorter.
Do not use ellipsis.
Action: The selected tool name
Action Input: The JSON format of tool parameters
For final answers, use `Action: terminate` and put the complete user-facing answer
only inside `Action Input.result`.
""".strip()

        tool_pack = ToolPack(
            [
                load_skill_tool,
                load_tools_tool,
                execute_skill_script,
                get_skill_resource,
                execute_skill_script_file_tool,
                code_interpreter_tool,
                load_file_tool,
                execute_analysis_tool,
                shell_interpreter_tool,
                html_interpreter_tool,
                todowrite_tool,
                execute_tool_tool,
                question_tool,
                Terminate(),
            ]
            + ask_data_tools
            + business_tools
            + connector_tool_extras
        )

    # Debug: print all registered tools
    logger.info(f"ToolPack resources: {list(tool_pack._resources.keys())}")
    if "execute_skill_script" not in tool_pack._resources:
        logger.error("execute_skill_script NOT in ToolPack!")

    # --- Connector system prompt injection (T11) ---
    try:
        from dbgpt.agent.resource.connector.manager import (
            ConnectorManager as _ConnectorManager,
        )

        _cm = CFG.SYSTEM_APP.get_component(
            "connector_manager", _ConnectorManager, default_component=None
        )
        if _cm is not None and connector_ids:
            _active = _cm.list_active()
            # Only describe connectors the user explicitly selected.
            # Iterate connector_ids (not _active) so prompt order matches
            # user selection order and stays consistent with _select_connector_tools.
            _active_map = {c["connector_id"]: c for c in _active if isinstance(c, dict)}
            _selected = [
                _active_map[cid] for cid in connector_ids if cid in _active_map
            ]
            if _selected:
                _connector_lines = []
                for _c in _selected:
                    _tool_lines = []
                    for t in _c.get("tools", []):
                        _name = t.get("name", "unknown")
                        _desc = t.get("description", "") or "(no description)"
                        _args_schema = t.get("args", {}) or {}
                        # Render args schema as concise Parameters description
                        if _args_schema:
                            _param_parts = []
                            for _arg_name, _arg_meta in _args_schema.items():
                                if isinstance(_arg_meta, dict):
                                    _arg_type = _arg_meta.get("type", "any")
                                    _req = (
                                        "required"
                                        if _arg_meta.get("required")
                                        else "optional"
                                    )
                                    _arg_desc = _arg_meta.get("description", "")
                                    if _arg_desc:
                                        _trimmed_desc = (
                                            _arg_desc[:120] + "..."
                                            if len(_arg_desc) > 120
                                            else _arg_desc
                                        )
                                        _param_parts.append(
                                            f'"{_arg_name}": <{_arg_type}, {_req}, '
                                            f"{_trimmed_desc}>"
                                        )
                                    else:
                                        _param_parts.append(
                                            f'"{_arg_name}": <{_arg_type}, {_req}>'
                                        )
                                else:
                                    _param_parts.append(f'"{_arg_name}": <any>')
                            _params_str = "{" + ", ".join(_param_parts) + "}"
                        else:
                            _params_str = "{}"
                        _tool_lines.append(
                            f"  - **{_name}**: {_desc}\n    Parameters: {_params_str}"
                        )
                    _connector_lines.append(
                        f"### {_c.get('name', 'unknown')} "
                        f"({_c.get('connector_type', 'unknown')})\n"
                        f"{_c.get('description', '') or '(no description)'}\n\n"
                        f"Tools (call directly with `Action: <tool_name>`):\n"
                        + "\n".join(_tool_lines)
                        + "\n\nNote: Write operations require user confirmation."
                    )
                _connector_prompt = (
                    "\n\n## Available MCP Connector Tools\n"
                    "You have access to the following external MCP connectors. "
                    "All listed tools are pre-registered — invoke them directly "
                    "with `Action: <tool_name>`, NOT through `execute_tool`.\n"
                    + "\n\n".join(_connector_lines)
                )
                workflow_prompt += _connector_prompt
    except Exception:
        pass  # graceful degradation
    # --- End connector system prompt injection ---

    # Convert workflow_prompt to PromptTemplate so it is used as system prompt
    # Use jinja2 format to avoid issues with JSON braces { } in the prompt
    workflow_prompt_template = PromptTemplate(
        template=workflow_prompt,
        input_variables=[],
        template_format="jinja2",
    )

    agent_builder = (
        ReActAgent(
            max_retry_count=react_max_steps,
            llm_inference_retry_count=react_llm_error_limit,
            llm_error_retry_limit=react_llm_error_limit,
            parse_error_retry_limit=react_parse_error_limit,
            same_action_repeat_limit=react_same_action_repeat_limit,
        )
        .bind(context)
        .bind(agent_memory)
        .bind(llm_config)
        .bind(tool_pack)
        .bind(workflow_prompt_template)
    )

    agent = await agent_builder.build()

    parser = ReActOutputParser()
    received = AgentMessage(content=user_input)
    # stream_queue and stream_callback were created earlier (before ToolPack)
    # so that the question tool can use them.

    # Wire up context-management status events into the SSE stream.
    async def _context_status_callback(status: Dict[str, Any]) -> None:
        await stream_queue.put({"type": "context.status", **status})

    agent.init_context_management(
        config=await _load_context_budget_config(
            llm_client=llm_client,
            model_name=dialogue.model_name,
        ),
        model_name=dialogue.model_name,
        on_status_event=_context_status_callback,
    )

    async def run_agent():
        return await agent.generate_reply(
            received_message=received,
            sender=agent,
            stream_callback=stream_callback,
        )

    agent_task = asyncio.create_task(run_agent())
    round_step_map: Dict[int, str] = {}
    pending_thoughts: Dict[
        int, List[str]
    ] = {}  # Buffer thinking content for delayed step creation
    pending_action_intentions: Dict[int, str] = {}
    pending_action_reasons: Dict[int, str] = {}
    # --- History persistence: collect step data during streaming ---
    history_steps: List[Dict[str, Any]] = []
    current_history_step: Optional[Dict[str, Any]] = None

    def record_ask_data_stage_for_history(event: Dict[str, Any]) -> None:
        stage = str(event.get("stage") or "query")
        call_id = str(event.get("call_id") or "legacy")
        title = str(event.get("title") or "AskData")
        detail = str(event.get("detail") or "")
        duration_ms = event.get("duration_ms")
        status_raw = str(event.get("status") or "").lower()
        status = "failed" if status_raw in {"failed", "error"} else "done"
        action = "ask_data_stage"
        step_id = f"ask-data-{call_id}-{stage}"
        output_lines = [f"### {title}", f"- 阶段：`{stage}`", f"- 状态：`{status}`"]
        if isinstance(duration_ms, (int, float)):
            output_lines.append(f"- 耗时：{duration_ms / 1000:.2f} 秒")
        if detail:
            output_lines.append(f"- 说明：{detail}")
        payload = {
            "id": step_id,
            "title": title,
            "detail": detail,
            "thought": None,
            "action_intention": None,
            "action_reason": None,
            "action": action,
            "action_input": None,
            "ask_data_call_id": call_id,
            "parent_id": f"ask-data-{call_id}",
            "outputs": [
                {
                    "output_type": "markdown",
                    "content": "\n".join(output_lines),
                }
            ],
            "status": status,
        }
        for idx, item in enumerate(history_steps):
            if item.get("id") == step_id:
                history_steps[idx] = payload
                return
        history_steps.append(payload)

    # Emit pre-loaded skill as an SSE step before agent starts processing
    if pre_matched_skill:
        skill_step_id, skill_step_event = build_step(
            f"Load Skill: {pre_matched_skill.metadata.name}",
            "Pre-loaded skill from user selection",
            phase="加载技能",
        )
        current_history_step = {
            "id": skill_step_id,
            "title": f"Load Skill: {pre_matched_skill.metadata.name}",
            "detail": "Pre-loaded skill from user selection",
            "phase": "加载技能",
            "thought": None,
            "action": None,
            "action_input": None,
            "outputs": [],
            "status": "done",
        }
        yield skill_step_event
        # Emit skill metadata as text chunk
        skill_desc = (
            f"Skill: {pre_matched_skill.metadata.name}"
            f" - {pre_matched_skill.metadata.description}"
        )
        yield step_chunk(skill_step_id, "text", skill_desc)
        current_history_step["outputs"].append(
            {"output_type": "text", "content": skill_desc}
        )
        # Emit skill instructions as markdown content (shows in right panel)
        if pre_matched_skill.instructions:
            yield step_chunk(skill_step_id, "markdown", pre_matched_skill.instructions)
            current_history_step["outputs"].append(
                {
                    "output_type": "markdown",
                    "content": pre_matched_skill.instructions,
                }
            )
        yield step_done(skill_step_id)
        history_steps.append(current_history_step)
        current_history_step = None

    while True:
        if cancel_event.is_set():
            break
        if agent_task.done() and stream_queue.empty():
            break
        try:
            event = await asyncio.wait_for(stream_queue.get(), timeout=0.1)
        except asyncio.CancelledError:
            cancel_event.set()
            if current_history_step is not None:
                current_history_step["status"] = "cancelled"
                history_steps.append(current_history_step)
                current_history_step = None
            for item in history_steps:
                if item.get("status") == "running":
                    item["status"] = "cancelled"
            for item in _todo_list:
                if item.get("status") in {"pending", "in_progress"}:
                    item["status"] = "cancelled"

            final_content = "任务已取消。"
            outcome = _react_outcome_payload(
                status=REACT_STATUS_CANCELLED,
                termination_reason=REACT_REASON_CANCELLED,
                history_steps=history_steps,
                max_steps=react_max_steps,
                error_message="SSE stream disconnected before completion",
            )
            try:
                update_react_session_after_turn(
                    session_state,
                    react_state,
                    history_steps,
                    final_content,
                    skills_dir=skills_dir,
                    status=outcome["status"],
                    termination_reason=outcome["termination_reason"],
                    error_message=outcome.get("error_message"),
                    run_summary=outcome,
                )
                save_react_session_state(session_state)
            except Exception:
                logger.debug(
                    "Failed to save disconnected ReAct session state", exc_info=True
                )
            try:
                history_payload = json.dumps(
                    {
                        "version": 1,
                        "type": "react-agent",
                        **outcome,
                        "final_content": final_content,
                        "steps": history_steps,
                        "task_plan": list(_todo_list),
                        "generated_images": react_state.get("generated_images", []),
                        "session_state": session_state,
                    },
                    ensure_ascii=False,
                )
                storage_conv.add_view_message(history_payload)
                storage_conv.end_current_round()
                storage_conv.save_to_storage()
            except Exception:
                logger.debug(
                    "Failed to persist disconnected ReAct run", exc_info=True
                )
            if not agent_task.done():
                agent_task.cancel()
            REACT_AGENT_RUNS.pop(run_id, None)
            raise
        except asyncio.TimeoutError:
            continue

        event_type = event.get("type")
        if event_type == "context.status":
            # Forward context-management status to frontend as-is.
            yield _sse_event(event)
        elif event_type.startswith("ask_data."):
            if event_type == "ask_data.stage":
                record_ask_data_stage_for_history(event)
            yield _sse_event(event)
        elif event_type in ("question.asked", "question.replied", "question.rejected"):
            # Forward human-in-the-loop question events to frontend as-is.
            yield _sse_event(event)
        elif event_type == "thinking":
            # Parse thinking content but don't create step yet
            # Step will be created when 'act' event arrives with confirmed
            # action
            round_num = int(event.get("round") or (len(round_step_map) + 1))
            llm_reply = event.get("llm_reply") or ""
            thought = None
            action_intention = None
            action_reason = None
            action = None
            action_input = None
            try:
                steps = parser.parse(llm_reply)
                if steps:
                    thought = steps[0].thought
                    action_intention = steps[0].action_intention
                    action_reason = steps[0].action_reason
                    action = steps[0].action
                    action_input = steps[0].action_input
            except Exception:
                pass

            # Store parsed thinking info in pending_thoughts for later use
            if round_num not in pending_thoughts:
                pending_thoughts[round_num] = []
            if thought:
                pending_thoughts[round_num].append(thought)
            intention_text = normalize_display_text(action_intention)
            if intention_text:
                pending_action_intentions[round_num] = intention_text
            reason_text = normalize_display_text(action_reason)
            if reason_text:
                pending_action_reasons[round_num] = reason_text
            # Don't emit anything yet - wait for 'act' event to create step

        elif event_type == "thinking_chunk":
            round_num = int(event.get("round") or (len(round_step_map) + 1))
            delta_thinking = event.get("delta_thinking") or ""
            delta_text = event.get("delta_text") or ""

            chunk = delta_thinking or delta_text
            if chunk:
                # Clean chunk: remove Action Input JSON to keep thought pure
                # Split on Action Input pattern and keep only thought part
                clean_chunk = re.split(
                    r"\n\s*Action\s*Input\s*:\s*\{", chunk, maxsplit=1
                )[0]
                # Also remove Action: lines
                clean_chunk = re.sub(r"\n\s*Action\s*:\s*\w+", "", clean_chunk)
                # Remove Thought: prefix if present
                if clean_chunk.startswith("Thought:"):
                    clean_chunk = clean_chunk[len("Thought:") :].strip()
                if clean_chunk:
                    if round_num not in pending_thoughts:
                        pending_thoughts[round_num] = []
                    pending_thoughts[round_num].append(clean_chunk)
                    if round_num not in round_step_map:
                        pending_step_id, pending_step_event = build_step(
                            "思考中",
                            "Thought/Action/Observation",
                        )
                        round_step_map[round_num] = pending_step_id
                        yield pending_step_event

        elif event_type == "act":
            # Create step ONLY when action is confirmed
            round_num = int(event.get("round") or (len(round_step_map) + 1))

            action_output = event.get("action_output") or {}
            thoughts = action_output.get("thoughts")
            action = action_output.get("action")
            action_input = action_output.get("action_input")
            action_input_data = None
            if action_input is not None:
                if isinstance(action_input, str):
                    try:
                        action_input_data = json.loads(action_input)
                    except Exception:
                        action_input_data = action_input
                else:
                    action_input_data = action_input

            # Skip step display for terminate action — its output will be
            # sent as a streaming "final" event instead of a step card.
            # Also skip emitting the thought for terminate since it's noise.
            # Note: TerminateAction.run() sets terminate=True but does NOT
            # set the action field, so we must check the terminate boolean.
            is_terminate = action_output.get("terminate") or (
                action and action.lower() == "terminate"
            )
            if is_terminate:
                pending_thoughts.pop(round_num, [])
                pending_action_intentions.pop(round_num, None)
                pending_action_reasons.pop(round_num, None)
                # ── Auto-complete all remaining todos on terminate ──
                if _todo_list:
                    for t in _todo_list:
                        if t["status"] in ("pending", "in_progress"):
                            t["status"] = "completed"
                    yield _sse_event({"type": "plan.update", "tasks": list(_todo_list)})
                continue

            # ── TodoWrite: emit plan.update SSE and show step card ──
            if action and action.lower() == "todowrite":
                pending_thoughts.pop(round_num, [])
                pending_action_intentions.pop(round_num, None)
                pending_action_reasons.pop(round_num, None)
                # Extract todos from observation JSON
                obs_text = action_output.get("observations") or action_output.get(
                    "content"
                )
                todos_payload: List[Dict[str, str]] = []
                if obs_text:
                    try:
                        obs_json = (
                            json.loads(obs_text)
                            if isinstance(obs_text, str)
                            else obs_text
                        )
                        if isinstance(obs_json, dict):
                            todos_payload = obs_json.get("__todos__", [])
                    except Exception:
                        pass
                # Fallback: read from the closure variable
                if not todos_payload and _todo_list:
                    todos_payload = list(_todo_list)

                _td_total = len(todos_payload)
                _td_done = sum(
                    1 for t in todos_payload if t.get("status") == "completed"
                )
                if _td_done == 0:
                    todo_state = "init"
                elif _td_done == _td_total and _td_total > 0:
                    todo_state = "done"
                else:
                    todo_state = "progress"

                todo_meta = {
                    "state": todo_state,
                    "done": _td_done,
                    "total": _td_total,
                }
                _todo_step_title = (
                    f"TODO::{todo_state}:{_td_done}/{_td_total}"
                    if _td_total > 0
                    else f"TODO::{todo_state}"
                )

                # Emit or update the step card for this round
                # NOTE: Do NOT set phase — let it fall into the default
                # "Execution Steps" group so todowrite cards appear inline
                # alongside other action steps in chronological order.
                if round_num in round_step_map:
                    todo_step_id = round_step_map[round_num]
                    yield _sse_event(
                        {
                            "type": "step.start",
                            "step": step,
                            "id": todo_step_id,
                            "title": _todo_step_title,
                            "detail": "todowrite",
                            "todo_meta": todo_meta,
                        }
                    )
                else:
                    todo_step_id, todo_step_event = build_step(
                        _todo_step_title,
                        "todowrite",
                    )
                    round_step_map[round_num] = todo_step_id
                    yield _sse_event(
                        {
                            "type": "step.start",
                            "step": step,
                            "id": todo_step_id,
                            "title": _todo_step_title,
                            "detail": "todowrite",
                            "todo_meta": todo_meta,
                        }
                    )

                yield _sse_event({"type": "plan.update", "tasks": todos_payload})
                yield step_meta(
                    round_step_map[round_num],
                    None,
                    action,
                    None,
                    _todo_step_title,
                    todo_meta=todo_meta,
                )
                history_steps.append(
                    {
                        "id": round_step_map[round_num],
                        "title": _todo_step_title,
                        "detail": "todowrite",
                        "thought": None,
                        "action_intention": None,
                        "action_reason": None,
                        "action": action,
                        "action_input": None,
                        "outputs": [],
                        "status": "done",
                        "todo_meta": todo_meta,
                    }
                )
                yield step_done(round_step_map[round_num])
                continue

            # Collect buffered thoughts for history persistence
            # (already streamed to frontend via thinking_chunk handler)
            buffered_thoughts = pending_thoughts.pop(round_num, [])
            thought_text = None
            if buffered_thoughts:
                full_thought = "".join(buffered_thoughts)
                full_thought = re.split(r"\n\s*Action\s*:", full_thought, maxsplit=1)[
                    0
                ].strip()
                if full_thought.startswith("Thought:"):
                    full_thought = full_thought[len("Thought:") :].strip()
                if full_thought:
                    thought_text = full_thought
            action_intention = normalize_display_text(
                action_output.get("action_intention")
                or pending_action_intentions.pop(round_num, None)
                or action_output.get("phase")
            )
            action_reason = normalize_display_text(
                action_output.get("action_reason")
                or pending_action_reasons.pop(round_num, None)
            )
            display_thought = action_intention or summarize_thought(
                thought_text or thoughts, action
            )

            # Use the actual action name as the step title (Manus-style UI)
            action_title = action or f"ReAct Round {round_num}"
            if round_num in round_step_map:
                # Step already exists (from thinking) - update title with same id
                react_step_id = round_step_map[round_num]
                updated_event = _sse_event(
                    {
                        "type": "step.start",
                        "step": step,
                        "id": react_step_id,
                        "title": action_title,
                        "detail": "Thought/Action/Observation",
                    }
                )
                yield updated_event
            else:
                react_step_id, react_step_event = build_step(
                    action_title,
                    "Thought/Action/Observation",
                )
                round_step_map[round_num] = react_step_id
                yield react_step_event

            # --- History: create step record ---
            action_input_str = None
            if action_input is not None:
                action_input_str = (
                    action_input
                    if isinstance(action_input, str)
                    else json.dumps(action_input, ensure_ascii=False)
                )
            current_history_step = {
                "id": react_step_id,
                "title": action_title,
                "detail": "Thought/Action/Observation",
                "thought": display_thought,
                "action_intention": action_intention,
                "action_reason": action_reason,
                "action": action,
                "action_input": action_input_str,
                "outputs": [],
                "status": "running",
                "error_type": action_output.get("error_type"),
                "error_message": action_output.get("error_message"),
            }

            # Stream action code to frontend for right panel
            # (code_interpreter)
            code_payload = None
            if action == "code_interpreter" and isinstance(action_input_data, dict):
                code_payload = action_input_data.get("code")
            if isinstance(code_payload, str) and code_payload.strip():
                yield step_chunk(react_step_id, "code", code_payload)
                if current_history_step is not None:
                    current_history_step["outputs"].append(
                        {"output_type": "code", "content": code_payload}
                    )

            observation_text = action_output.get("observations") or action_output.get(
                "content"
            )
            ask_data_result_payload = (
                _extract_ask_data_payload(observation_text)
                if action and action.lower() == "ask_data_query"
                else None
            )
            ask_data_call_id = (
                str(ask_data_result_payload.get("call_id"))
                if ask_data_result_payload and ask_data_result_payload.get("call_id")
                else None
            )
            ask_data_reused = bool(
                ask_data_result_payload and ask_data_result_payload.get("reused")
            )

            # Emit thinking metadata
            if thoughts or action or action_input:
                step_action_input = (
                    None if action == "code_interpreter" else action_input
                )
                yield step_meta(
                    react_step_id,
                    display_thought,
                    action,
                    step_action_input,
                    action_title,
                    action_intention=action_intention,
                    action_reason=action_reason,
                    ask_data_call_id=ask_data_call_id,
                    ask_data_reused=ask_data_reused,
                )

            # Emit observation (action execution result)
            if observation_text:
                raw_chunks = emit_tool_chunks(react_step_id, observation_text)
                if raw_chunks:
                    for chunk in raw_chunks:
                        yield chunk
                else:
                    for chunk in chunk_text(str(observation_text), max_len=600):
                        yield step_chunk(react_step_id, "text", chunk)
                # --- History: collect outputs from observation ---
                if current_history_step is not None:
                    parsed_obs = None
                    if isinstance(observation_text, str):
                        try:
                            parsed_obs = json.loads(observation_text)
                        except Exception:
                            pass
                    if isinstance(parsed_obs, dict) and isinstance(
                        parsed_obs.get("chunks"), list
                    ):
                        for item in parsed_obs["chunks"]:
                            if isinstance(item, dict):
                                current_history_step["outputs"].append(
                                    {
                                        "output_type": item.get("output_type", "text"),
                                        "content": item.get("content"),
                                    }
                                )
                    elif isinstance(observation_text, str) and observation_text:
                        current_history_step["outputs"].append(
                            {
                                "output_type": "text",
                                "content": observation_text,
                            }
                        )

            # Mark step as done and track as last completed
            status = "done" if action_output.get("is_exe_success", True) else "failed"
            error_type = action_output.get("error_type")
            error_message = action_output.get("error_message")
            yield step_done(react_step_id, status, error_type, error_message)
            if (
                status == "done"
                and action
                and action.lower() != "todowrite"
                and should_advance_todo(
                    action, thought_text or thoughts, observation_text
                )
            ):
                updated_todos = advance_todo_list()
                if updated_todos:
                    yield _sse_event({"type": "plan.update", "tasks": updated_todos})
            # --- History: finalize step ---
            if current_history_step is not None:
                current_history_step["status"] = status
                if error_type:
                    current_history_step["error_type"] = error_type
                if error_message:
                    current_history_step["error_message"] = error_message
                if ask_data_call_id:
                    current_history_step["ask_data_call_id"] = ask_data_call_id
                if ask_data_reused:
                    current_history_step["ask_data_reused"] = True
                history_steps.append(current_history_step)
                current_history_step = None

    if cancel_event.is_set():
        if current_history_step is not None:
            current_history_step["status"] = "cancelled"
            history_steps.append(current_history_step)
            current_history_step = None
        for item in history_steps:
            if item.get("status") == "running":
                item["status"] = "cancelled"
        for item in _todo_list:
            if item.get("status") in {"pending", "in_progress"}:
                item["status"] = "cancelled"
        if not agent_task.done():
            agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("Cancelled react agent task ended with error", exc_info=True)

        final_content = "任务已取消。"
        outcome = _react_outcome_payload(
            status=REACT_STATUS_CANCELLED,
            termination_reason=REACT_REASON_CANCELLED,
            history_steps=history_steps,
            max_steps=react_max_steps,
        )
        try:
            update_react_session_after_turn(
                session_state,
                react_state,
                history_steps,
                final_content,
                skills_dir=skills_dir,
                status=outcome["status"],
                termination_reason=outcome["termination_reason"],
                error_message=outcome.get("error_message"),
                run_summary=outcome,
            )
            save_react_session_state(session_state)
        except Exception:
            logger.debug("Failed to save cancelled ReAct session state", exc_info=True)
        history_payload = json.dumps(
            {
                "version": 1,
                "type": "react-agent",
                **outcome,
                "final_content": final_content,
                "steps": history_steps,
                "task_plan": list(_todo_list),
                "generated_images": react_state.get("generated_images", []),
                "session_state": session_state,
            },
            ensure_ascii=False,
        )
        storage_conv.add_view_message(history_payload)
        storage_conv.end_current_round()
        storage_conv.save_to_storage()
        REACT_AGENT_RUNS.pop(run_id, None)
        yield _sse_event({"type": "run.cancelled", "run_id": run_id, **outcome})
        yield _sse_event({"type": "final", "content": final_content, **outcome})
        yield _sse_event({"type": "done"})
        return

    try:
        reply = await agent_task
    except Exception as e:
        err_msg = f"React agent failed: {e}"
        outcome = _react_outcome_payload(
            status=REACT_STATUS_FAILED,
            termination_reason=REACT_REASON_LLM_ERROR
            if _looks_like_llm_error_text(str(e))
            else REACT_REASON_EXCEPTION,
            history_steps=history_steps,
            max_steps=react_max_steps,
            error_message=str(e),
        )
        try:
            update_react_session_after_turn(
                session_state,
                react_state,
                history_steps,
                err_msg,
                skills_dir=skills_dir,
                status=outcome["status"],
                termination_reason=outcome["termination_reason"],
                error_message=outcome.get("error_message"),
                run_summary=outcome,
            )
            save_react_session_state(session_state)
        except Exception:
            logger.debug("Failed to save failed ReAct session state", exc_info=True)
        error_payload = json.dumps(
            {
                "version": 1,
                "type": "react-agent",
                **outcome,
                "final_content": err_msg,
                "steps": history_steps,
                "task_plan": list(_todo_list),
                "generated_images": react_state.get("generated_images", []),
                "session_state": session_state,
            },
            ensure_ascii=False,
        )
        storage_conv.add_view_message(error_payload)
        storage_conv.end_current_round()
        storage_conv.save_to_storage()
        REACT_AGENT_RUNS.pop(run_id, None)
        yield _sse_event({"type": "run.failed", "run_id": run_id, **outcome})
        yield _sse_event({"type": "final", "content": err_msg, **outcome})
        yield _sse_event({"type": "done"})
        return

    if reply.action_report and reply.action_report.terminate:
        raw_content = reply.action_report.content or ""
        # The terminate ActionOutput.content is the full raw LLM text, e.g.:
        # "Thought: ...\nAction: terminate\nAction Input: {"result": "..."}"
        # We need to extract the "result" value from Action Input.
        final_content = raw_content
        try:
            steps = parser.parse(raw_content)
            if steps:
                action_input = steps[0].action_input
                if action_input:
                    # action_input could be a string like '{"result": "..."}'
                    if isinstance(action_input, str):
                        parsed_input = json.loads(action_input)
                    else:
                        parsed_input = action_input
                    if isinstance(parsed_input, dict) and "result" in parsed_input:
                        final_content = parsed_input["result"]
        except Exception:
            pass
    elif reply.action_report:
        # Loop ended without terminate (max retries or timeout).
        # reply.content is raw LLM output containing ReAct prefixes.
        # Try to extract a clean summary from the last step's thought.
        raw = reply.content or reply.action_report.content or ""
        final_content = raw
        try:
            steps = parser.parse(raw)
            if steps:
                last_step = steps[-1]
                # Prefer observation (execution result) > thought
                if last_step.observations:
                    final_content = last_step.observations
                elif last_step.thoughts:
                    final_content = last_step.thoughts
        except Exception:
            pass
        # Fallback: strip remaining ReAct prefixes via regex
        final_content = re.sub(
            r"^(Thought|Action|Action Input|Observation|Phase):\s*",
            "",
            final_content,
            flags=re.MULTILINE,
        ).strip()
        if not final_content:
            final_content = "任务执行已达到最大步数限制，请查看上方各步骤的执行结果。"
    else:
        final_content = reply.content or ""

    outcome = _compute_react_run_outcome(
        reply=reply,
        history_steps=history_steps,
        todo_list=list(_todo_list),
        max_steps=react_max_steps,
    )
    final_content = _final_content_for_outcome(final_content, outcome)

    try:
        update_react_session_after_turn(
            session_state,
            react_state,
            history_steps,
            final_content,
            skills_dir=skills_dir,
            status=outcome["status"],
            termination_reason=outcome["termination_reason"],
            error_message=outcome.get("error_message"),
            run_summary=outcome,
        )
        save_react_session_state(session_state)
    except Exception:
        logger.debug("Failed to save completed ReAct session state", exc_info=True)

    if react_state.get("ask_data_pending_query_id"):
        REACT_ASK_DATA_PENDING[conv_id] = {
            "query_id": str(react_state["ask_data_pending_query_id"]),
            "user_id": dialogue.user_name or "anonymous",
        }
    else:
        REACT_ASK_DATA_PENDING.pop(conv_id, None)

    # Persist AI reply with structured history payload
    history_payload = json.dumps(
        {
            "version": 1,
            "type": "react-agent",
            **outcome,
            "final_content": final_content,
            "steps": history_steps,
            "task_plan": list(_todo_list),
            "generated_images": react_state.get("generated_images", []),
            "session_state": session_state,
        },
        ensure_ascii=False,
    )
    storage_conv.add_view_message(history_payload)
    storage_conv.end_current_round()
    storage_conv.save_to_storage()
    REACT_AGENT_RUNS.pop(run_id, None)

    yield _sse_event(
        {"type": f"run.{outcome['status']}", "run_id": run_id, **outcome}
    )
    yield _sse_event({"type": "final", "content": final_content, **outcome})
    yield _sse_event({"type": "done"})


# ---------------------------------------------------------------------------
# Share link APIs
# ---------------------------------------------------------------------------


class ShareCreateRequest(_BaseModel):
    """Request body for creating a share link."""

    conv_uid: str


class ShareCreateResponse(_BaseModel):
    """Response body for share link creation."""

    token: str
    conv_uid: str
    share_url: str


class ShareConvResponse(_BaseModel):
    """Public payload returned when viewing a shared conversation."""

    conv_uid: str
    token: str
    messages: list  # list[{role, context, order}]


def _get_share_dao():
    """Lazily instantiate the ShareLinkDao (avoids import-time side-effects)."""
    from dbgpt_app.share.models import ShareLinkDao

    return ShareLinkDao()


def _get_conversation_service():
    """Return the ConversationServe Service component."""
    from dbgpt_serve.conversation.config import SERVE_SERVICE_COMPONENT_NAME
    from dbgpt_serve.conversation.service.service import Service

    return CFG.SYSTEM_APP.get_component(SERVE_SERVICE_COMPONENT_NAME, Service)


@router.post("/v1/chat/share", response_model=Result)
async def create_share_link(
    body: ShareCreateRequest = Body(),
    user_token: UserRequest = Depends(get_user_from_headers),
):
    """Create (or return existing) share link for a conversation.

    The returned ``share_url`` is a relative path that the client should
    prepend with the current host to form an absolute URL.
    """
    dao = _get_share_dao()
    created_by = user_token.user_id if user_token else None
    entity = dao.create_share(conv_uid=body.conv_uid, created_by=created_by)
    if entity is None:
        return Result.failed(msg="Failed to create share link")
    return Result.succ(
        ShareCreateResponse(
            token=entity.token,
            conv_uid=entity.conv_uid,
            share_url=f"/share/{entity.token}",
        )
    )


@router.get("/v1/chat/share/{token}", response_model=Result)
async def get_share_conversation(token: str):
    """Public endpoint — no authentication required.

    Returns the full conversation history for the given share token so that the
    replay page can reconstruct and animate the session.
    """
    dao = _get_share_dao()
    link = dao.get_by_token(token)
    if link is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Share link not found")

    service = _get_conversation_service()
    from dbgpt_serve.conversation.api.schemas import ServeRequest

    history = service.get_history_messages(ServeRequest(conv_uid=link.conv_uid))

    messages = [
        {"role": m.role, "context": m.context, "order": m.order}
        for m in (history or [])
    ]
    return Result.succ(
        ShareConvResponse(
            conv_uid=link.conv_uid,
            token=token,
            messages=messages,
        )
    )


@router.delete("/v1/chat/share/{token}", response_model=Result)
async def delete_share_link(
    token: str,
    user_token: UserRequest = Depends(get_user_from_headers),
):
    """Revoke a share link.  Only the owner (or any authenticated user) may delete."""
    dao = _get_share_dao()
    deleted = dao.delete_by_token(token)
    if not deleted:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Share link not found")
    return Result.succ({"deleted": True, "token": token})


@router.get("/v1/agent/files/download")
async def download_agent_file(
    file_path: str = Query(..., description="Absolute path to the file to download"),
):
    """Download a file created by agent tools (shell_interpreter, code_interpreter).

    Only files under allowed directories (/tmp, PILOT_PATH/tmp/) can be downloaded.
    This prevents arbitrary file access on the server.
    """
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    from dbgpt.configs.model_config import PILOT_PATH, ROOT_PATH

    # If path is not absolute, resolve relative to ROOT_PATH (sandbox working dir)
    if not os.path.isabs(file_path):
        file_path = os.path.join(ROOT_PATH, file_path)

    # Resolve to absolute path and prevent path traversal
    try:
        resolved = os.path.realpath(file_path)
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Allowed base directories for agent-created files
    allowed_dirs = [
        os.path.realpath("/tmp"),
        os.path.realpath(os.path.join(PILOT_PATH, "tmp")),
        os.path.realpath(ROOT_PATH),
    ]

    if not any(resolved.startswith(d + os.sep) or resolved == d for d in allowed_dirs):
        raise HTTPException(
            status_code=403,
            detail="Access denied: file is not in an allowed directory",
        )

    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")

    filename = os.path.basename(resolved)
    return FileResponse(
        path=resolved,
        filename=filename,
        media_type="application/octet-stream",
    )


@router.get("/v1/agent/skills/download")
async def download_skill_package(
    skill_name: str = Query(..., description="Skill folder name"),
    user_token: UserRequest = Depends(get_user_from_headers),
):
    """Download a skill folder as a .zip archive."""
    from fastapi import HTTPException

    if not skill_name:
        raise HTTPException(status_code=400, detail="skill_name is required")

    skills_dir = Path(DEFAULT_SKILLS_DIR).expanduser().resolve()
    skill_path = (skills_dir / skill_name).resolve()

    # Security: ensure path is under skills_dir
    try:
        skill_path.relative_to(skills_dir)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not skill_path.is_dir():
        raise HTTPException(status_code=404, detail="Skill not found")

    # Build zip in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(skill_path):
            for fname in files:
                abs_file = os.path.join(root, fname)
                arc_name = os.path.relpath(abs_file, skill_path)
                zf.write(abs_file, arcname=os.path.join(skill_name, arc_name))
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{skill_name}.zip"',
        },
    )


@router.post("/v1/chat/react-agent")
async def chat_react_agent(
    dialogue: ConversationVo = Body(),
    user_token: UserRequest = Depends(get_user_from_headers),
):
    logger.info(
        "chat_react_agent:%s,%s,%s",
        dialogue.chat_mode,
        dialogue.select_param,
        dialogue.model_name,
    )
    dialogue.user_name = user_token.user_id if user_token else dialogue.user_name
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Transfer-Encoding": "chunked",
    }
    try:
        return StreamingResponse(
            _react_agent_stream(dialogue, user_token),
            headers=headers,
            media_type="text/event-stream",
        )
    except Exception as e:
        logger.exception("React Agent Exception!%s", dialogue, exc_info=e)

        async def error_text(err_msg):
            yield f"data:{err_msg}\n\n"

        return StreamingResponse(
            error_text(str(e)),
            headers=headers,
            media_type="text/plain",
        )


# ── Human-in-the-Loop Question API ──────────────────────────────────────────


class _ReactAgentCancelBody(_BaseModel):
    conv_uid: Optional[str] = None
    run_id: Optional[str] = None


@router.post("/v1/chat/react-agent/cancel", response_model=Result)
async def chat_react_agent_cancel(
    body: _ReactAgentCancelBody = Body(),
    user_token: UserRequest = Depends(get_user_from_headers),
):
    """Cancel an active ReAct run and release pending HITL questions.

    The endpoint is intentionally idempotent: the frontend may call it after the
    stream has already closed or after a previous cancel request.
    """
    cancelled_run = False
    if body.run_id:
        run_state = REACT_AGENT_RUNS.get(body.run_id)
        if run_state:
            cancel_event = run_state.get("cancel_event")
            if cancel_event is not None:
                cancel_event.set()
                cancelled_run = True

    if body.conv_uid:
        for active_run_id, run_state in list(REACT_AGENT_RUNS.items()):
            if body.run_id and active_run_id == body.run_id:
                continue
            if run_state.get("conv_uid") != body.conv_uid:
                continue
            cancel_event = run_state.get("cancel_event")
            if cancel_event is not None:
                cancel_event.set()
                cancelled_run = True

    rejected_questions = 0
    if body.conv_uid:
        from dbgpt_app.openapi.api_v1.tools.question_manager import question_manager

        rejected_questions = question_manager.reject_by_conv(body.conv_uid)

    return Result.succ(
        {
            "success": True,
            "run_id": body.run_id,
            "conv_uid": body.conv_uid,
            "cancelled_run": cancelled_run,
            "rejected_questions": rejected_questions,
        }
    )


class _QuestionReplyBody(_BaseModel):
    answers: List[List[str]]


@router.post("/v1/chat/question/{request_id}/reply", response_model=Result)
async def question_reply(
    request_id: str,
    body: _QuestionReplyBody,
    user_token: UserRequest = Depends(get_user_from_headers),
):
    """User submits answers to a pending question, unblocking the agent tool."""
    from dbgpt_app.openapi.api_v1.tools.question_manager import question_manager

    try:
        question_manager.reply(request_id, body.answers)
        return Result.succ({"success": True, "request_id": request_id})
    except KeyError as e:
        return Result.failed(msg=str(e))


@router.post("/v1/chat/question/{request_id}/reject", response_model=Result)
async def question_reject(
    request_id: str,
    user_token: UserRequest = Depends(get_user_from_headers),
):
    """User dismisses a pending question, unblocking the agent tool with rejection."""
    from dbgpt_app.openapi.api_v1.tools.question_manager import question_manager

    try:
        question_manager.reject(request_id)
        return Result.succ({"success": True, "request_id": request_id})
    except KeyError as e:
        return Result.failed(msg=str(e))
