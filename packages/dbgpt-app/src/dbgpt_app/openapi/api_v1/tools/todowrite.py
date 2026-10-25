"""Todo tool with a locked task structure and mutable progress statuses."""

import json
import uuid
from typing import Any, Callable, Dict, List, Optional

from dbgpt.agent.resource.tool.base import tool

_VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
_VALID_PRIORITIES = {"high", "medium", "low"}


def make_todowrite(
    todo_list: List[Dict[str, Any]],
    stream_callback: Callable,
    plan_state: Optional[Dict[str, Any]] = None,
    allow_revision: bool = False,
):
    """Return a tool that creates a plan once and then only advances its status."""

    state = plan_state if plan_state is not None else {}
    if todo_list:
        state["plan_id"] = state.get("plan_id") or uuid.uuid4().hex
        state["revision"] = max(1, int(state.get("revision") or 0))
    else:
        state.setdefault("plan_id", None)
        state.setdefault("revision", 0)
    state.setdefault("sequence", 0)
    state.setdefault("locked", bool(todo_list))
    revision_authorized = [allow_revision]
    for item in todo_list:
        if not item.get("id"):
            item["id"] = f"task-{uuid.uuid4().hex}"

    @tool(
        description=(
            "Create and manage a structured task list for the current session. "
            "Use for complex tasks (3+ steps). The first accepted list locks the "
            "task contents and order. Later calls must pass the full unchanged "
            "list and may only advance statuses. Never add, remove, reorder, or "
            "rewrite tasks unless the user explicitly asks to change the plan. "
            "Each item must map to a concrete user-requested deliverable or "
            "necessary work. Do not create standalone items for internal tool "
            "calls, reasoning, returning the final answer, or terminating. "
            "Each todo has: id (copy from the current plan when available), "
            "content, status (pending | in_progress | completed | cancelled), "
            "and priority (high | medium | low). Only ONE task may be "
            "in_progress at a time. Do not call again when the plan is unchanged."
            '\nParameter: {"todos": [{"id": "...", "content": "...", '
            '"status": "...", "priority": "..."}]}'
        )
    )
    def todowrite(todos: str) -> str:
        """Create the initial plan or update statuses without changing structure."""
        parsed: List[Dict[str, str]] = []
        try:
            raw = json.loads(todos) if isinstance(todos, str) else todos
            items = raw if isinstance(raw, list) else raw.get("todos", raw)
            if not isinstance(items, list):
                raise ValueError("todos must be a list")
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("each todo must be an object")
                content = str(item.get("content", "")).strip()
                if not content:
                    raise ValueError("todo content cannot be empty")
                status = str(item.get("status", "pending"))
                priority = str(item.get("priority", "medium"))
                if status not in _VALID_STATUSES:
                    raise ValueError(f"invalid todo status: {status}")
                if priority not in _VALID_PRIORITIES:
                    raise ValueError(f"invalid todo priority: {priority}")
                parsed.append(
                    {
                        "id": str(item.get("id", "")).strip(),
                        "content": content,
                        "status": status,
                        "priority": priority,
                    }
                )
            if len(parsed) > 30:
                raise ValueError("a plan can contain at most 30 tasks")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return _result(f"Error: invalid todos: {exc}", todo_list, state, False)

        if not state["locked"]:
            if not parsed:
                return _result("Error: the initial task plan cannot be empty", todo_list, state, False)
            for item in parsed:
                item["id"] = f"task-{uuid.uuid4().hex}"
                if item["status"] in {"completed", "cancelled"}:
                    item["status"] = "pending"
            _set_single_active(parsed)
            todo_list[:] = parsed
            state["plan_id"] = uuid.uuid4().hex
            state["revision"] = 1
            state["locked"] = True
            revision_authorized[0] = False
            return _result("Task plan created and locked", todo_list, state, True)

        same_structure = _same_structure(parsed, todo_list)
        if not same_structure:
            if not revision_authorized[0]:
                return _result(
                    "Rejected: the task plan is locked. Keep its contents and order "
                    "unchanged; only update statuses. The user must explicitly "
                    "request a plan change before the structure can be revised.",
                    todo_list,
                    state,
                    False,
                )
            revised = _revise_plan(parsed, todo_list)
            if revised == todo_list:
                return _result("Task plan unchanged", todo_list, state, False)
            todo_list[:] = revised
            state["revision"] = int(state.get("revision") or 0) + 1
            revision_authorized[0] = False
            return _result("Task plan revised as requested", todo_list, state, True)

        updated = _update_statuses(parsed, todo_list)
        if updated == todo_list:
            return _result("Task plan unchanged", todo_list, state, False)
        todo_list[:] = updated
        return _result("Task progress updated", todo_list, state, True)

    return todowrite


def _same_structure(parsed: List[Dict[str, str]], current: List[Dict[str, str]]) -> bool:
    if len(parsed) != len(current):
        return False
    return all(
        new_item["content"] == old_item.get("content")
        and new_item["priority"] == old_item.get("priority", "medium")
        and (not new_item["id"] or new_item["id"] == old_item.get("id"))
        for new_item, old_item in zip(parsed, current)
    )


def _set_single_active(items: List[Dict[str, str]]) -> None:
    active_seen = False
    for item in items:
        if item["status"] == "in_progress":
            if active_seen:
                item["status"] = "pending"
            else:
                active_seen = True
    if not active_seen:
        next_item = next((item for item in items if item["status"] == "pending"), None)
        if next_item:
            next_item["status"] = "in_progress"


def _update_statuses(
    parsed: List[Dict[str, str]], current: List[Dict[str, str]]
) -> List[Dict[str, str]]:
    updated = [dict(item) for item in current]
    for index, new_item in enumerate(parsed):
        old_status = updated[index].get("status", "pending")
        requested_status = new_item["status"]
        if old_status in {"completed", "cancelled"}:
            updated[index]["status"] = old_status
        elif requested_status == "cancelled":
            updated[index]["status"] = "cancelled"
        elif old_status == "in_progress":
            updated[index]["status"] = (
                "completed" if requested_status == "completed" else "in_progress"
            )
        else:
            updated[index]["status"] = "pending"
    active_index = next(
        (
            index
            for index, item in enumerate(updated)
            if item.get("status") not in {"completed", "cancelled"}
        ),
        None,
    )
    for index, item in enumerate(updated):
        if item.get("status") in {"completed", "cancelled"}:
            continue
        item["status"] = "in_progress" if index == active_index else "pending"
    return updated


def _revise_plan(
    parsed: List[Dict[str, str]], current: List[Dict[str, str]]
) -> List[Dict[str, str]]:
    unused = list(current)
    revised: List[Dict[str, str]] = []
    for item in parsed:
        match_index = next(
            (
                index
                for index, old_item in enumerate(unused)
                if old_item.get("content") == item["content"]
                and old_item.get("priority", "medium") == item["priority"]
            ),
            None,
        )
        if match_index is None:
            revised.append(
                {
                    **item,
                    "id": f"task-{uuid.uuid4().hex}",
                    "status": "pending",
                }
            )
        else:
            old_item = unused.pop(match_index)
            revised.append(
                {
                    **item,
                    "id": old_item.get("id") or f"task-{uuid.uuid4().hex}",
                    "status": old_item.get("status", "pending"),
                }
            )
    _set_single_active(revised)
    return revised


def _result(
    message: str,
    todos: List[Dict[str, str]],
    plan_state: Dict[str, Any],
    changed: bool,
) -> str:
    if changed:
        plan_state["sequence"] = int(plan_state.get("sequence") or 0) + 1
    total = len(todos)
    done = sum(1 for item in todos if item.get("status") == "completed")
    return json.dumps(
        {
            "chunks": [
                {
                    "output_type": "text",
                    "content": f"{message} ({done}/{total} completed)",
                }
            ],
            "__todos__": todos,
            "__plan__": {
                "plan_id": plan_state.get("plan_id"),
                "revision": plan_state.get("revision", 0),
                "sequence": plan_state.get("sequence", 0),
            },
            "__plan_update__": changed,
        },
        ensure_ascii=False,
    )
