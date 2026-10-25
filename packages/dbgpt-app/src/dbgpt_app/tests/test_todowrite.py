import json

from dbgpt_app.openapi.api_v1.tools.todowrite import make_todowrite


def _call(tool_instance, todos):
    return json.loads(tool_instance(json.dumps({"todos": todos}, ensure_ascii=False)))


def test_first_todo_write_locks_plan_and_repeated_call_does_not_change_it():
    todos = []
    state = {}
    tool_instance = make_todowrite(todos, lambda *_: None, state)

    created = _call(
        tool_instance,
        [
            {"content": "检查代码", "status": "in_progress", "priority": "high"},
            {"content": "运行测试", "status": "pending", "priority": "medium"},
            {"content": "整理结果", "status": "pending", "priority": "low"},
        ],
    )

    assert created["__plan_update__"] is True
    assert len(todos[0]["id"]) > 0
    assert state["revision"] == 1
    assert state["sequence"] == 1


def test_todo_description_requires_user_deliverables_not_finalization_steps():
    todos = []
    tool_instance = make_todowrite(todos, lambda *_: None, {})

    assert "concrete user-requested deliverable" in tool_instance._tool.description
    assert "returning the final answer" in tool_instance._tool.description


def test_initial_plan_does_not_accept_completed_steps_without_execution():
    todos = []
    state = {}
    tool_instance = make_todowrite(todos, lambda *_: None, state)

    created = _call(
        tool_instance,
        [
            {"content": "第一步", "status": "completed", "priority": "high"},
            {"content": "第二步", "status": "completed", "priority": "medium"},
        ],
    )

    assert created["__plan_update__"] is True
    assert [item["status"] for item in todos] == ["in_progress", "pending"]

    repeated = _call(tool_instance, todos)

    assert repeated["__plan_update__"] is False
    assert repeated["__todos__"] == todos
    assert state["sequence"] == 1


def test_locked_plan_rejects_structural_changes_but_accepts_status_progress():
    todos = []
    tool_instance = make_todowrite(todos, lambda *_: None, {})
    _call(
        tool_instance,
        [
            {"content": "检查代码", "status": "in_progress", "priority": "high"},
            {"content": "运行测试", "status": "pending", "priority": "medium"},
            {"content": "整理结果", "status": "pending", "priority": "low"},
        ],
    )
    locked_plan = [dict(item) for item in todos]

    rejected = _call(
        tool_instance,
        [
            {"content": "检查代码", "status": "in_progress", "priority": "high"},
            {"content": "新增文档", "status": "pending", "priority": "medium"},
            {"content": "整理结果", "status": "pending", "priority": "low"},
        ],
    )

    assert rejected["__plan_update__"] is False
    assert todos == locked_plan

    progressed = _call(
        tool_instance,
        [
            {**locked_plan[0], "status": "completed"},
            {**locked_plan[1], "status": "pending"},
            {**locked_plan[2], "status": "pending"},
        ],
    )

    assert progressed["__plan_update__"] is True
    assert [item["status"] for item in todos] == ["completed", "in_progress", "pending"]

    attempted_skip = _call(
        tool_instance,
        [
            {**todos[0], "status": "completed"},
            {**todos[1], "status": "in_progress"},
            {**todos[2], "status": "in_progress"},
        ],
    )
    assert attempted_skip["__plan_update__"] is False

    advanced_current = _call(
        tool_instance,
        [
            {**todos[0], "status": "completed"},
            {**todos[1], "status": "completed"},
            {**todos[2], "status": "pending"},
        ],
    )
    assert advanced_current["__plan_update__"] is True
    assert [item["status"] for item in todos] == ["completed", "completed", "in_progress"]

    cancelled_current = _call(
        tool_instance,
        [
            {**todos[0], "status": "completed"},
            {**todos[1], "status": "completed"},
            {**todos[2], "status": "cancelled"},
        ],
    )
    assert cancelled_current["__plan_update__"] is True
    assert [item["status"] for item in todos] == ["completed", "completed", "cancelled"]


def test_plan_revision_requires_authorization_and_increments_revision():
    todos = [
        {
            "id": "task-1",
            "content": "检查代码",
            "status": "in_progress",
            "priority": "high",
        },
        {
            "id": "task-2",
            "content": "运行测试",
            "status": "pending",
            "priority": "medium",
        },
    ]
    state = {"plan_id": "plan-1", "revision": 1, "sequence": 3, "locked": True}
    tool_instance = make_todowrite(todos, lambda *_: None, state, allow_revision=True)

    revised = _call(
        tool_instance,
        [
            {**todos[0], "status": "in_progress"},
            {"content": "更新文档", "status": "pending", "priority": "medium"},
        ],
    )

    assert revised["__plan_update__"] is True
    assert state["revision"] == 2
    assert todos[0]["id"] == revised["__todos__"][0]["id"]
    assert todos[1]["content"] == "更新文档"
    assert todos[1]["status"] == "pending"

    second_revision = _call(
        tool_instance,
        [{"content": "完全不同的计划", "status": "in_progress", "priority": "high"}],
    )
    assert second_revision["__plan_update__"] is False
    assert state["revision"] == 2
