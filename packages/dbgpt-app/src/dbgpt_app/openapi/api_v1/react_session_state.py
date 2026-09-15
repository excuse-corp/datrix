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

STATE_VERSION = 1
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_ATTACHMENTS = 20
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


def record_uploaded_attachment(state: Dict[str, Any], file_path: Optional[str]) -> None:
    if not file_path:
        return
    attachment = build_attachment_metadata(file_path)
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


def _recent_conversation_lines(messages: Iterable[Any]) -> List[str]:
    rows = _messages_to_recent_text(messages)
    lines = []
    for role, text in rows[-MAX_RECENT_MESSAGES:]:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            continue
        label = (
            "User"
            if role == "human"
            else "Assistant"
            if role in {"ai", "view"}
            else role
        )
        lines.append(f"- {label}: {_truncate(normalized, 600)}")
    return lines


def render_react_session_context(
    state: Dict[str, Any],
    messages: Iterable[Any],
    *,
    current_user_input: str = "",
    max_chars: int = 16000,
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

    recent_lines = _recent_conversation_lines(messages)
    if recent_lines:
        lines.append("### Recent Conversation")
        lines.extend(recent_lines)

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

    return _truncate("\n".join(lines), max_chars)


def update_react_session_after_turn(
    state: Dict[str, Any],
    react_state: Dict[str, Any],
    history_steps: List[Dict[str, Any]],
    final_content: str,
    *,
    skills_dir: str = SKILLS_DIR,
    status: str = "completed",
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
    }
    state["checkpoint"] = {
        "last_status": status,
        "last_turn_id": react_state.get("turn_id"),
        "last_final_content": _truncate(final_content or "", 2000),
        "updated_at": _now_iso(),
    }
    state["updated_at"] = _now_iso()
    return state
