"""Persistent UI state for file-based skills."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from dbgpt.configs.model_config import SKILLS_DIR, resolve_root_path

STATE_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path() -> Path:
    raw = resolve_root_path("pilot/meta_data/skill_state.json")
    path = Path(raw or "pilot/meta_data/skill_state.json").expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _default_state() -> Dict[str, Any]:
    return {"version": STATE_VERSION, "skills": {}, "updated_at": _now_iso()}


def load_skill_state() -> Dict[str, Any]:
    path = _state_path()
    if not path.is_file():
        return _default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _default_state()
    if not isinstance(data, dict):
        return _default_state()
    if not isinstance(data.get("skills"), dict):
        data["skills"] = {}
    data["version"] = STATE_VERSION
    return data


def save_skill_state(state: Dict[str, Any]) -> None:
    if not isinstance(state.get("skills"), dict):
        state["skills"] = {}
    state["version"] = STATE_VERSION
    state["updated_at"] = _now_iso()
    path = _state_path()
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def normalize_skill_path(file_path: str, *, skills_dir: str = SKILLS_DIR) -> str:
    """Return a stable path relative to the skills root."""
    root = Path(skills_dir or SKILLS_DIR).expanduser().resolve()
    raw_value = str(file_path or "").strip()
    if not raw_value:
        raise ValueError("file_path is required")
    raw = Path(raw_value).expanduser()
    if raw.is_absolute():
        resolved = raw.resolve()
    else:
        resolved = (root / raw).resolve()
    try:
        return str(resolved.relative_to(root))
    except Exception as exc:
        raise ValueError("Invalid skill file path") from exc


def skill_enabled(file_path: str, *, skills_dir: str = SKILLS_DIR) -> bool:
    rel_path = normalize_skill_path(file_path, skills_dir=skills_dir)
    state = load_skill_state()
    entry = state.get("skills", {}).get(rel_path, {})
    return bool(entry.get("enabled", True))


def set_skill_enabled(
    file_path: str,
    enabled: bool,
    *,
    skill_name: Optional[str] = None,
    skills_dir: str = SKILLS_DIR,
) -> Dict[str, Any]:
    rel_path = normalize_skill_path(file_path, skills_dir=skills_dir)
    state = load_skill_state()
    skills = state.setdefault("skills", {})
    current = skills.get(rel_path, {}) if isinstance(skills.get(rel_path), dict) else {}
    current.update(
        {
            "enabled": bool(enabled),
            "name": skill_name or current.get("name") or Path(rel_path).parts[0],
            "updated_at": _now_iso(),
        }
    )
    skills[rel_path] = current
    save_skill_state(state)
    return {"file_path": rel_path, **current}


def remove_skill_state(file_path: str, *, skills_dir: str = SKILLS_DIR) -> None:
    rel_path = normalize_skill_path(file_path, skills_dir=skills_dir)
    state = load_skill_state()
    skills = state.setdefault("skills", {})
    skills.pop(rel_path, None)
    save_skill_state(state)
