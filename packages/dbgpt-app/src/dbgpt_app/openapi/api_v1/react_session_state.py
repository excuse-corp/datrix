"""Persistent state helpers for the ReAct agent stream.

The DB-GPT message tables remain the source of truth for UI/history records, but
the ReAct loop needs a compact, durable state object that can be injected into
the next turn without replaying every internal tool log.  This module stores that
state as JSON files under ``pilot/meta_data/react_agent_state``.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dbgpt.configs.model_config import ROOT_PATH, SKILLS_DIR, resolve_root_path
from dbgpt_app.openapi.api_v1.upload_limits import MAX_CHAT_UPLOAD_FILES
from dbgpt_app.scene.conversation_context import ConversationContext

STATE_VERSION = 1
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_ATTACHMENTS = MAX_CHAT_UPLOAD_FILES
MAX_ARTIFACTS = 60
MAX_RECENT_MESSAGES = 8


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value or "default")


def _state_dir() -> Path:
    raw = resolve_root_path("pilot/meta_data/react_agent_state")
    path = Path(raw or "pilot/meta_data/react_agent_state").expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(conv_id: str) -> Path:
    return _state_dir() / f"{_safe_id(conv_id)}.json"


def _default_state(conv_id: str) -> Dict[str, Any]:
    now = _now_iso()
    return {
        "version": STATE_VERSION,
        "session_id": conv_id,
        "active_skill": None,
        "referenced_skills": [],
        "attachments": [],
        "artifacts": [],
        "workflow_state": {},
        "pending_user_input": None,
        "checkpoint": {},
        "active_task": None,
        "last_task": None,
        "created_at": now,
        "updated_at": now,
    }


def _normalize_state(conv_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _default_state(conv_id)
    if isinstance(state, dict):
        normalized.update(state)
    normalized["version"] = STATE_VERSION
    normalized["session_id"] = conv_id
    for key in ("referenced_skills", "attachments", "artifacts"):
        if not isinstance(normalized.get(key), list):
            normalized[key] = []
    if not isinstance(normalized.get("workflow_state"), dict):
        normalized["workflow_state"] = {}
    if not isinstance(normalized.get("checkpoint"), dict):
        normalized["checkpoint"] = {}
    for key in ("active_task", "last_task"):
        if not isinstance(normalized.get(key), dict):
            normalized[key] = None
    return normalized


def load_react_session_state(conv_id: str) -> Dict[str, Any]:
    path = _state_path(conv_id)
    if not path.is_file():
        return _default_state(conv_id)
    try:
        return _normalize_state(conv_id, json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return _default_state(conv_id)


def save_react_session_state(state: Dict[str, Any]) -> None:
    conv_id = str(state.get("session_id") or "default")
    state = _normalize_state(conv_id, state)
    state["updated_at"] = _now_iso()
    path = _state_path(conv_id)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _root_path() -> Path:
    return Path(ROOT_PATH or os.getcwd()).expanduser().resolve()


def _resolve_local_path(file_path: str) -> Path:
    raw = str(file_path or "").strip()
    if not raw:
        return Path("")
    if "://" in raw:
        return Path(raw)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = _root_path() / path
    return path.resolve()


def _display_path(file_path: str) -> str:
    raw = str(file_path or "").strip()
    if not raw:
        return raw
    if "://" in raw:
        return raw
    path = _resolve_local_path(raw)
    try:
        return str(path.relative_to(_root_path()))
    except Exception:
        return str(path)


def _kind_for_path(path: Path, mime_type: Optional[str]) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTENSIONS or (mime_type or "").startswith("image/"):
        return "image"
    if ext in {".csv", ".xls", ".xlsx", ".json", ".parquet"}:
        return "data"
    if ext in {".html", ".htm"}:
        return "html"
    return "file"


def _image_summary(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        from PIL import Image
    except Exception:
        return None

    try:
        with Image.open(path) as image:
            original = image.copy()
            width, height = original.size
            rgb = original.convert("RGB")
            sample = rgb.copy()
            sample.thumbnail((256, 256))
            quantized = sample.quantize(colors=8)
            colors = quantized.getcolors(maxcolors=256) or []
            total = sum(count for count, _idx in colors) or 1
            palette = quantized.getpalette() or []
            dominant = []
            for count, palette_idx in sorted(colors, reverse=True)[:8]:
                base = palette_idx * 3
                if base + 2 >= len(palette):
                    continue
                r, g, b = palette[base : base + 3]
                dominant.append(
                    {
                        "hex": f"#{r:02X}{g:02X}{b:02X}",
                        "rgb": [r, g, b],
                        "ratio": round(count / total, 4),
                    }
                )
            return {
                "width": width,
                "height": height,
                "mode": original.mode,
                "dominant_colors": dominant,
            }
    except Exception:
        return None


def build_attachment_metadata(file_path: str) -> Dict[str, Any]:
    resolved = _resolve_local_path(file_path)
    mime_type, _encoding = mimetypes.guess_type(str(resolved))
    exists = resolved.is_file() if "://" not in str(file_path) else False
    stat = resolved.stat() if exists else None
    metadata: Dict[str, Any] = {
        "path": _display_path(file_path),
        "absolute_path": str(resolved) if exists else str(file_path),
        "mime_type": mime_type,
        "kind": _kind_for_path(resolved, mime_type),
        "exists": exists,
        "size_bytes": stat.st_size if stat else None,
    }
    if metadata["kind"] == "image" and exists:
        summary = _image_summary(resolved)
        if summary:
            metadata["image"] = summary
    return metadata


def record_uploaded_attachment(
    state: Dict[str, Any],
    file_path: Optional[str],
    upload_metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if not file_path:
        return
    attachment = build_attachment_metadata(file_path)
    if upload_metadata:
        attachment.update(
            {
                key: value
                for key, value in upload_metadata.items()
                if key in {
                    "attachment_id",
                    "filename",
                    "size_bytes",
                    "mime_type",
                    "sha256",
                    "storage_path",
                    "status",
                    "format",
                }
                and value is not None
            }
        )
    attachment["first_seen_at"] = _now_iso()
    attachments = [a for a in state.get("attachments", []) if isinstance(a, dict)]
    key = attachment.get("absolute_path") or attachment.get("path")
    filtered = [
        item
        for item in attachments
        if (item.get("absolute_path") or item.get("path")) != key
    ]
    filtered.append(attachment)
    state["attachments"] = filtered[-MAX_ATTACHMENTS:]


def update_attachment_parse_metadata(
    state: Dict[str, Any], file_path: str, parse_result: Any
) -> None:
    """Store a compact parse summary without copying parsed document text."""

    resolved = str(_resolve_local_path(file_path))
    attachments = [a for a in state.get("attachments", []) if isinstance(a, dict)]
    target = next(
        (
            item
            for item in attachments
            if item.get("absolute_path") == resolved
            or item.get("path") == _display_path(file_path)
        ),
        None,
    )
    if target is None:
        record_uploaded_attachment(state, file_path)
        attachments = [a for a in state.get("attachments", []) if isinstance(a, dict)]
        target = next(
            (
                item
                for item in attachments
                if item.get("absolute_path") == resolved
            ),
            None,
        )
    if target is None:
        return
    target.update(
        {
            "attachment_id": getattr(parse_result, "attachment_id", None),
            "format": getattr(parse_result, "format", None),
            "parse_status": getattr(parse_result, "status", "failed"),
            "parser": getattr(parse_result, "parser", None),
            "block_count": len(getattr(parse_result, "blocks", []) or []),
            "text_chars": len(getattr(parse_result, "plain_text", "") or ""),
            "warnings": list(getattr(parse_result, "warnings", []) or [])[:10],
            "parse_error_code": getattr(parse_result, "error_code", None),
        }
    )


def _skill_candidates(skills_dir: str = SKILLS_DIR) -> Dict[str, str]:
    root = Path(skills_dir or SKILLS_DIR).expanduser().resolve()
    candidates: Dict[str, str] = {}
    if not root.exists():
        return candidates
    for item in root.iterdir():
        if item.is_dir() and (item / "SKILL.md").is_file():
            candidates[item.name.lower()] = _display_path(str(item / "SKILL.md"))
        elif item.is_file() and item.suffix == ".skill":
            candidates.setdefault(item.stem.lower(), _display_path(str(item)))
    return candidates


def _flatten_text(value: Any, *, depth: int = 0) -> str:
    if depth > 5 or value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ""
        try:
            parsed = json.loads(stripped)
        except Exception:
            return stripped
        return stripped + "\n" + _flatten_text(parsed, depth=depth + 1)
    if isinstance(value, dict):
        parts = []
        for key in (
            "data",
            "final_content",
            "content",
            "title",
            "detail",
            "action",
            "action_input",
            "outputs",
            "steps",
            "generated_images",
        ):
            if key in value:
                parts.append(_flatten_text(value.get(key), depth=depth + 1))
        return "\n".join(part for part in parts if part)
    if isinstance(value, list):
        return "\n".join(_flatten_text(item, depth=depth + 1) for item in value)
    return str(value)


def _messages_to_recent_text(messages: Iterable[Any]) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    for message in messages or []:
        role = str(getattr(message, "type", "message") or "message")
        content = getattr(message, "content", "")
        text = _flatten_text(content)
        if text:
            rows.append((role, text))
    return rows


def infer_active_skill_from_text(
    text: str, *, skills_dir: str = SKILLS_DIR
) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    known = _skill_candidates(skills_dir)
    scores: Dict[str, int] = {}

    patterns = [
        r"skills/([A-Za-z0-9_.-]+)/SKILL\.md",
        r"skills/([A-Za-z0-9_.-]+)\.skill",
        r"Skill\s*[:：]\s*([A-Za-z0-9_.-]+)",
        r"技能\s*[:：]\s*([A-Za-z0-9_.-]+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            name = match.group(1).strip().lower()
            if name in known:
                scores[name] = scores.get(name, 0) + 5

    lowered = text.lower()
    if "skill" in lowered or "技能" in text or "SKILL.md" in text:
        for name in known:
            if re.search(
                rf"(?<![A-Za-z0-9_.-]){re.escape(name)}(?![A-Za-z0-9_.-])",
                lowered,
            ):
                scores[name] = scores.get(name, 0) + 1

    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    name = ranked[0][0]
    return {"name": name, "path": known[name], "source": "history_inference"}


def infer_active_skill_from_messages(
    messages: Iterable[Any], *, skills_dir: str = SKILLS_DIR
) -> Optional[Dict[str, Any]]:
    rows = _messages_to_recent_text(messages)
    for _role, text in reversed(rows[-MAX_RECENT_MESSAGES:]):
        inferred = infer_active_skill_from_text(text, skills_dir=skills_dir)
        if inferred:
            return inferred
    combined = "\n".join(text for _role, text in rows[-MAX_RECENT_MESSAGES:])
    return infer_active_skill_from_text(combined, skills_dir=skills_dir)


def record_active_skill(
    state: Dict[str, Any],
    name: Optional[str],
    path: Optional[str] = None,
    *,
    source: str = "runtime",
) -> None:
    if not name:
        return
    skill = {
        "name": str(name),
        "path": path or _skill_candidates().get(str(name).lower()),
        "source": source,
        "updated_at": _now_iso(),
    }
    state["active_skill"] = skill
    referenced = [
        item for item in state.get("referenced_skills", []) if isinstance(item, dict)
    ]
    referenced = [item for item in referenced if item.get("name") != skill["name"]]
    referenced.append(skill)
    state["referenced_skills"] = referenced[-10:]


def _extract_artifacts_from_text(text: str) -> List[Dict[str, Any]]:
    artifacts: List[Dict[str, Any]] = []
    seen = set()
    path_patterns = [
        r"(?:/root/dataman/)?skills/[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+",
        r"(?:/root/dataman/)?skills/[A-Za-z0-9_.-]+\.skill",
        r"(?:/root/dataman/)?pilot/tmp/[A-Za-z0-9_./-]+",
        r"(?:/root/dataman/)?[^\s'\")]+\.html",
    ]
    for pattern in path_patterns:
        for match in re.finditer(pattern, text):
            raw_path = match.group(0).rstrip(".,;:，。；：)")
            display = _display_path(raw_path)
            if display in seen:
                continue
            seen.add(display)
            artifact_type = "file"
            if "/SKILL.md" in display or display.endswith(".skill"):
                artifact_type = "skill"
            elif display.endswith((".html", ".htm")):
                artifact_type = "html"
            artifacts.append(
                {
                    "type": artifact_type,
                    "path": display,
                    "source": "turn_output",
                    "updated_at": _now_iso(),
                }
            )
    return artifacts


def _append_artifacts(state: Dict[str, Any], artifacts: List[Dict[str, Any]]) -> None:
    current = [a for a in state.get("artifacts", []) if isinstance(a, dict)]
    by_key = {
        a.get("path") or a.get("url") or json.dumps(a, sort_keys=True): a
        for a in current
    }
    for artifact in artifacts:
        key = (
            artifact.get("path")
            or artifact.get("url")
            or json.dumps(artifact, sort_keys=True)
        )
        by_key[key] = artifact
    state["artifacts"] = list(by_key.values())[-MAX_ARTIFACTS:]


def is_skill_reference_request(text: str) -> bool:
    lowered = (text or "").lower()
    return any(
        token in lowered
        for token in ("这个skill", "这个 skill", "这个技能", "this skill", "that skill")
    )


def is_skill_maintenance_request(text: str) -> bool:
    lowered = (text or "").lower()
    return ("skill" in lowered or "技能" in lowered) and any(
        token in lowered
        for token in (
            "优化",
            "修改",
            "更新",
            "完善",
            "改进",
            "修复",
            "调整",
            "edit",
            "update",
            "optimize",
            "improve",
            "modify",
            "fix",
        )
    )


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n...[truncated]"


def is_react_continuation_request(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？,.!?；;：:、]+", "", (text or "").lower())
    if not normalized or normalized.startswith(("不继续", "不用继续", "不要继续", "先不", "stop")):
        return False
    return normalized.startswith(
        (
            "继续",
            "接着",
            "请继续",
            "请接着",
            "按刚才继续",
            "按上次继续",
            "继续刚才",
            "继续之前",
            "继续上次",
            "完成刚才的任务",
            "把刚才的任务完成",
            "continue",
            "resume",
        )
    )


def is_react_plan_revision_request(text: str) -> bool:
    text = (text or "").lower()
    compact = re.sub(r"\s+", "", text)
    if re.search(r"(?:不要|不需要|无需|不用|别|不).{0,8}(?:修改|调整|更改|变更|重做|重排|重新安排|重写).{0,6}(?:计划|方案|步骤|任务列表)", compact):
        return False
    if re.search(r"\b(?:don't|do not|does not|no need to)\s+(?:change|revise|modify|reorder|replan|rewrite|update)", text):
        return False
    chinese_patterns = (
        r"(?:修改|调整|更改|变更|重做|重写|重排|重新安排|重新制定|重新规划|增删|改动|更换)(?:一下|下|当前|原有|整个|这份|这个|执行|任务|步骤|工作)*(?:的)?(?:执行)?(?:计划|方案|步骤|任务列表)",
        r"(?:计划|方案|步骤|任务列表)(?:需要|要|请|为|改为|改成)?(?:修改|调整|更改|变更|重排|增删|改为|改成|改一下)",
        r"(?:计划|方案|步骤|任务列表).{0,6}(?:修改|调整|更改|变更|重做|重写|重排|增删|增加|删除|新增|移除|改成|改为|改一下|换一下)",
        r"(?:把|将).{0,16}(?:计划|方案|步骤|任务列表).{0,8}(?:改为|改成|调整|修改|重排)",
    )
    if any(re.search(pattern, compact) for pattern in chinese_patterns):
        return True
    english_patterns = (
        r"\b(?:change|revise|modify|reorder|rework|replan|rewrite|update)\s+(?:the\s+)?(?:task\s+)?(?:plan|steps|task list)\b",
        r"\b(?:plan|steps|task list)\s+(?:should be\s+)?(?:changed|revised|modified|reordered|updated)\b",
    )
    return any(re.search(pattern, text) for pattern in english_patterns)


def _normalize_task_plan(task_plan: Any) -> List[Dict[str, Any]]:
    if not isinstance(task_plan, list):
        return []
    normalized = []
    for item in task_plan[:30]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        task = {
            "content": _truncate(content, 500),
            "status": str(item.get("status") or "pending"),
            "priority": str(item.get("priority") or "medium"),
        }
        if item.get("id"):
            task["id"] = _truncate(str(item["id"]), 100)
        normalized.append(task)
    return normalized


def restore_react_task_plan(
    active_task: Any, *, resume_cancelled: bool = False
) -> List[Dict[str, Any]]:
    if not isinstance(active_task, dict):
        return []
    task_plan = _normalize_task_plan(active_task.get("task_plan"))
    in_progress = False
    task_id = str(active_task.get("task_id") or "legacy")
    for index, item in enumerate(task_plan):
        item.setdefault("id", f"task-{task_id}-{index + 1}")
        if item["status"] == "failed" or (
            resume_cancelled and item["status"] == "cancelled"
        ):
            item["status"] = "pending"
        if item["status"] == "in_progress":
            if in_progress:
                item["status"] = "pending"
            else:
                in_progress = True
    if not in_progress:
        next_item = next(
            (item for item in task_plan if item["status"] == "pending"), None
        )
        if next_item:
            next_item["status"] = "in_progress"
    return task_plan


def start_react_task_checkpoint(
    state: Dict[str, Any],
    *,
    task_id: str,
    user_input: str,
    round_index: int,
    resume_task: bool = False,
) -> Optional[Dict[str, Any]]:
    existing = state.get("active_task") if resume_task else None
    task_plan = restore_react_task_plan(existing, resume_cancelled=resume_task)
    return update_react_task_checkpoint(
        state,
        task_id=task_id,
        original_request=user_input,
        task_plan=task_plan,
        history_steps=[],
        status="in_progress",
        plan_id=existing.get("plan_id") if isinstance(existing, dict) else None,
        plan_revision=existing.get("plan_revision", 0)
        if isinstance(existing, dict)
        else 0,
        plan_sequence=existing.get("plan_sequence", 0)
        if isinstance(existing, dict)
        else 0,
        round_index=round_index,
        force=True,
    )


def build_react_task_continuation_prompt(
    active_task: Any, latest_user_input: str
) -> str:
    if not isinstance(active_task, dict):
        return latest_user_input
    lines = [
        "继续完成下面的未完成任务。当前消息不是一个新的独立问题。",
        f"原始任务：{_truncate(str(active_task.get('original_request') or ''), 2000)}",
    ]
    task_plan = _normalize_task_plan(active_task.get("task_plan"))
    if task_plan:
        lines.append("当前计划（已完成项只复用结果，不要重复执行）：")
        lines.extend(
            f"- [{item['status']}] {item['content']}" for item in task_plan
        )
    completed_steps = [
        item
        for item in active_task.get("completed_steps", [])
        if isinstance(item, dict)
    ]
    if completed_steps:
        lines.append("已完成工作及证据：")
        for item in completed_steps[-8:]:
            summary = _truncate(str(item.get("summary") or ""), 700)
            detail = f"：{summary}" if summary else ""
            lines.append(
                f"- {item.get('title') or item.get('action') or '已完成步骤'}{detail}"
            )
    if active_task.get("last_result"):
        lines.append(
            "上次阶段性结果："
            + _truncate(str(active_task["last_result"]), 1500)
        )
    if latest_user_input:
        lines.append(f"用户本轮消息：{_truncate(latest_user_input, 1000)}")
    lines.append(
        "执行要求：从第一个未完成计划项继续，完成原始任务及其报告要求；"
        "不要只重复上次结果，也不要把未完成任务当作已完成。"
    )
    return "\n".join(line for line in lines if line.strip())


def _task_step_summaries(
    history_steps: Iterable[Any], turn_id: str
) -> List[Dict[str, str]]:
    summaries = []
    for index, step in enumerate(history_steps or []):
        if not isinstance(step, dict) or step.get("status") not in {"done", "completed"}:
            continue
        action = str(step.get("action") or "")
        if action.lower() in {"todowrite", "terminate"}:
            continue
        output_parts = []
        for output in step.get("outputs") or []:
            if not isinstance(output, dict):
                continue
            content = output.get("content")
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False, default=str)
            if content:
                output_parts.append(str(content))
        summary = "\n".join(output_parts).strip()
        summaries.append(
            {
                "id": f"{turn_id}:{step.get('id') or index}",
                "title": _truncate(str(step.get("title") or action or "执行步骤"), 160),
                "action": action,
                "summary": _truncate(summary, 900),
            }
        )
    return summaries


def update_react_task_checkpoint(
    state: Dict[str, Any],
    *,
    task_id: str,
    original_request: str,
    task_plan: Any,
    history_steps: Iterable[Any],
    status: str,
    plan_id: Optional[str] = None,
    plan_revision: int = 0,
    plan_sequence: int = 0,
    final_content: Optional[str] = None,
    error_message: Optional[str] = None,
    round_index: Optional[int] = None,
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    normalized_plan = _normalize_task_plan(task_plan)
    existing = state.get("active_task")
    same_task = isinstance(existing, dict) and existing.get("task_id") == task_id
    if not normalized_plan and not same_task and not force and not original_request:
        return None
    if not normalized_plan and same_task:
        normalized_plan = _normalize_task_plan(existing.get("task_plan"))

    completed_steps = list(existing.get("completed_steps", [])) if same_task else []
    existing_ids = {
        item.get("id") for item in completed_steps if isinstance(item, dict)
    }
    for item in _task_step_summaries(history_steps, task_id):
        if item["id"] not in existing_ids:
            completed_steps.append(item)
            existing_ids.add(item["id"])

    preserved_request = (
        str(existing.get("original_request") or "").strip()
        if same_task
        else ""
    )
    checkpoint = {
        "task_id": task_id,
        "round_index": round_index
        if round_index is not None
        else (existing or {}).get("round_index"),
        "original_request": _truncate(
            preserved_request or str(original_request or ""),
            2000,
        ),
        "status": status,
        "task_plan": normalized_plan,
        "plan_id": plan_id or (existing if same_task else {}).get("plan_id"),
        "plan_revision": plan_revision
        or (existing if same_task else {}).get("plan_revision", 0),
        "plan_sequence": plan_sequence
        or (existing if same_task else {}).get("plan_sequence", 0),
        "completed_steps": completed_steps[-12:],
        "last_result": _truncate(
            str(final_content or (existing or {}).get("last_result") or ""), 2000
        ),
        "error_message": _truncate(str(error_message or ""), 1000)
        if error_message
        else None,
        "updated_at": _now_iso(),
    }
    if (
        isinstance(existing, dict)
        and existing.get("task_id")
        and existing.get("task_id") != task_id
        and existing.get("status") != "completed"
    ):
        state["last_task"] = existing
    if status == "completed":
        state["last_task"] = checkpoint
        state["active_task"] = None
    else:
        state["active_task"] = checkpoint
    state["updated_at"] = checkpoint["updated_at"]
    return checkpoint


def recover_react_task_checkpoint_from_messages(
    state: Dict[str, Any], messages: Iterable[Any]
) -> Optional[Dict[str, Any]]:
    message_list = list(messages or [])
    active_task = state.get("active_task")
    user_messages = [
        message
        for message in message_list
        if getattr(message, "type", None) == "human"
    ]
    latest_human = user_messages[-1] if user_messages else None
    latest_human_round = getattr(latest_human, "round_index", 0)
    latest_human_text = str(getattr(latest_human, "content", "") or "")
    latest_payload = None
    latest_round = 0
    for message in reversed(message_list):
        if getattr(message, "type", None) != "view":
            continue
        content = getattr(message, "content", "")
        try:
            payload = json.loads(content) if isinstance(content, str) else content
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("type") == "react-agent":
            latest_payload = payload
            latest_round = int(
                payload.get("round_index")
                or getattr(message, "round_index", 0)
                or 0
            )
            break

    active_round = (
        active_task.get("round_index", 0) if isinstance(active_task, dict) else 0
    )
    if (
        latest_human is not None
        and latest_human_round > latest_round
        and not is_react_continuation_request(latest_human_text)
        and latest_human_round > active_round
    ):
        return update_react_task_checkpoint(
            state,
            task_id=f"orphan-{latest_human_round}",
            original_request=latest_human_text,
            task_plan=[],
            history_steps=[],
            status="incomplete",
            round_index=latest_human_round,
            force=True,
        )

    if isinstance(active_task, dict):
        if latest_payload and latest_payload.get("status") == "completed":
            if latest_round >= active_round:
                state["active_task"] = None
                return None
            return active_task
        latest_task_id = (latest_payload or {}).get("task_id")
        latest_is_newer = latest_round > active_round
        same_round_task_mismatch = (
            latest_round == active_round
            and latest_task_id
            and latest_task_id != active_task.get("task_id")
        )
        if latest_payload and (latest_is_newer or same_round_task_mismatch):
            active_task = None
        else:
            return active_task

    if not latest_payload or latest_payload.get("status") == "completed":
        return None

    user_by_round = {
        getattr(message, "round_index", 0): str(getattr(message, "content", "") or "")
        for message in message_list
        if getattr(message, "type", None) == "human"
    }
    task_plan = _normalize_task_plan(latest_payload.get("task_plan"))
    status = str(latest_payload.get("status") or "incomplete")
    has_resumable_item = any(
        item.get("status") in {"pending", "in_progress", "failed"}
        for item in task_plan
    )
    if not has_resumable_item and status not in {
        "cancelled",
        "incomplete",
        "failed",
    }:
        return None
    task_id = str(
        latest_payload.get("task_id")
        or latest_payload.get("turn_id")
        or f"legacy-{latest_round}"
    )
    return update_react_task_checkpoint(
        state,
        task_id=task_id,
        original_request=user_by_round.get(latest_round, ""),
        task_plan=task_plan,
        history_steps=latest_payload.get("steps") or [],
        status=status,
        plan_id=latest_payload.get("plan_id"),
        plan_revision=int(latest_payload.get("plan_revision") or 0),
        plan_sequence=int(latest_payload.get("plan_sequence") or 0),
        final_content=latest_payload.get("final_content"),
        error_message=latest_payload.get("error_message"),
        round_index=latest_round,
        force=True,
    )


def render_react_session_context(
    state: Dict[str, Any],
    conversation_context: ConversationContext,
    *,
    current_user_input: str = "",
) -> str:
    lines: List[str] = ["## Session Context"]
    active_skill = state.get("active_skill") if isinstance(state, dict) else None
    if isinstance(active_skill, dict) and active_skill.get("name"):
        lines.append("### Active Skill")
        lines.append(f"- Name: {active_skill.get('name')}")
        if active_skill.get("path"):
            lines.append(f"- Path: {active_skill.get('path')}")
        lines.append(f"- Source: {active_skill.get('source') or 'unknown'}")

    attachments = [a for a in state.get("attachments", []) if isinstance(a, dict)]
    if attachments:
        lines.append("### Uploaded Attachments")
        for item in attachments[-5:]:
            lines.append(
                f"- {item.get('kind')}: {item.get('path')}"
                f" ({item.get('mime_type') or 'unknown'}, exists={item.get('exists')})"
            )
            if item.get("parse_status"):
                lines.append(
                    f"  Parse: {item.get('parse_status')}, format={item.get('format') or 'unknown'}, "
                    f"blocks={item.get('block_count', 0)}, text_chars={item.get('text_chars', 0)}"
                )
            warnings = item.get("warnings") or []
            if warnings:
                lines.append(f"  Parse warnings: {_truncate('; '.join(map(str, warnings)), 500)}")
            image = item.get("image")
            if isinstance(image, dict):
                colors = image.get("dominant_colors") or []
                color_text = ", ".join(
                    f"{c.get('hex')} {c.get('ratio')}"
                    for c in colors[:6]
                    if isinstance(c, dict)
                )
                lines.append(
                    f"  Image: {image.get('width')}x{image.get('height')}, "
                    f"mode={image.get('mode')}, dominant colors=[{color_text}]"
                )

    artifacts = [a for a in state.get("artifacts", []) if isinstance(a, dict)]
    if artifacts:
        lines.append("### Known Artifacts")
        for item in artifacts[-8:]:
            target = item.get("path") or item.get("url") or item.get("name")
            if target:
                lines.append(f"- {item.get('type') or 'file'}: {target}")

    active_task = state.get("active_task") if isinstance(state, dict) else None
    if (
        (
            is_react_continuation_request(current_user_input)
            or is_react_plan_revision_request(current_user_input)
        )
        and isinstance(active_task, dict)
        and active_task.get("status") != "completed"
    ):
        lines.append("### Active Task Checkpoint")
        lines.append(f"- Status: {active_task.get('status') or 'incomplete'}")
        original_request = active_task.get("original_request")
        if original_request:
            lines.append(f"- Original goal: {_truncate(str(original_request), 2000)}")
        task_plan = _normalize_task_plan(active_task.get("task_plan"))
        if task_plan:
            lines.append("- Plan:")
            for item in task_plan:
                lines.append(
                    f"  - [{item['status']}] {item['content']}"
                )
        completed_steps = [
            item
            for item in active_task.get("completed_steps", [])
            if isinstance(item, dict)
        ]
        if completed_steps:
            lines.append("- Completed work and evidence:")
            for item in completed_steps[-8:]:
                summary = _truncate(str(item.get("summary") or ""), 900)
                detail = f": {summary}" if summary else ""
                lines.append(
                    f"  - {item.get('title') or item.get('action') or 'Completed step'}{detail}"
                )
        if active_task.get("last_result"):
            lines.append(
                f"- Last result: {_truncate(str(active_task['last_result']), 2000)}"
            )
        if active_task.get("error_message"):
            lines.append(
                f"- Last blocker: {_truncate(str(active_task['error_message']), 1000)}"
            )
        lines.append(
            "- Continuation: Resume this task when the latest user message asks to continue "
            "or clearly refers to it. Reuse completed results, finish the open plan items, "
            "and do not treat a partial response as task completion. For an unrelated new "
            "request, answer that request without executing this plan."
        )

    if conversation_context.recent_history_text:
        lines.append("### Recent Conversation")
        lines.extend(conversation_context.recent_history_text.splitlines())

    lines.append("### Continuity Rules")
    lines.append(
        "- Treat Active Skill and Known Artifacts as authoritative context for this session."
    )
    lines.append(
        "- If the user says this skill/这个 skill/这个技能 and Active Skill "
        "exists, resolve it to that exact skill."
    )
    lines.append(
        "- For skill maintenance requests, inspect the target skill file before "
        "answering; do not claim the original skill was not provided."
    )
    lines.append(
        "- If an uploaded image is a visual reference, carry its dominant colors "
        "and style constraints into generated CSS, charts, templates, and final "
        "validation."
    )

    if active_skill and is_skill_maintenance_request(current_user_input):
        lines.append("### Mandatory Skill Maintenance Gate")
        lines.append(
            "- The current request is a skill maintenance task. The first "
            "substantive action must read the target skill, preferably with "
            "load_skill or shell_interpreter."
        )
        lines.append(
            "- After reading it, modify the actual skill files when the user asks "
            "to optimize/update/fix the skill. A generic replacement suggestion "
            "is not sufficient."
        )

    return "\n".join(lines)


def update_react_session_after_turn(
    state: Dict[str, Any],
    react_state: Dict[str, Any],
    history_steps: List[Dict[str, Any]],
    final_content: str,
    *,
    skills_dir: str = SKILLS_DIR,
    status: str,
    termination_reason: Optional[str] = None,
    error_message: Optional[str] = None,
    run_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    matched = react_state.get("matched") if isinstance(react_state, dict) else None
    metadata = getattr(matched, "metadata", None)
    if metadata and getattr(metadata, "name", None):
        record_active_skill(
            state,
            getattr(metadata, "name", None),
            getattr(metadata, "file_path", None),
            source="matched_skill",
        )

    turn_text = "\n".join(
        [
            final_content or "",
            _flatten_text(history_steps),
            _flatten_text(react_state.get("generated_images", [])),
        ]
    )
    inferred = infer_active_skill_from_text(turn_text, skills_dir=skills_dir)
    if inferred:
        record_active_skill(
            state,
            inferred.get("name"),
            inferred.get("path"),
            source=inferred.get("source") or "turn_output",
        )

    artifacts = _extract_artifacts_from_text(turn_text)
    for url in react_state.get("generated_images", []) or []:
        artifacts.append(
            {
                "type": "image",
                "url": url,
                "source": "generated_image",
                "updated_at": _now_iso(),
            }
        )
    _append_artifacts(state, artifacts)

    state["workflow_state"] = {
        **(state.get("workflow_state") or {}),
        "last_turn_id": react_state.get("turn_id"),
        "last_status": status,
        "last_termination_reason": termination_reason,
    }
    state["checkpoint"] = {
        "last_status": status,
        "last_turn_id": react_state.get("turn_id"),
        "last_final_content": _truncate(final_content or "", 2000),
        "termination_reason": termination_reason,
        "error_message": _truncate(error_message or "", 1000) if error_message else None,
        "run_summary": run_summary or {},
        "updated_at": _now_iso(),
    }
    state["updated_at"] = _now_iso()
    return state
