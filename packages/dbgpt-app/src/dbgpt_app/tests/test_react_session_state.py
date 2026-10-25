import json
from types import SimpleNamespace

from dbgpt_app.openapi.api_v1 import react_session_state as rss


def _state(session_id="conv-1"):
    return {
        "version": 1,
        "session_id": session_id,
        "active_skill": None,
        "referenced_skills": [],
        "attachments": [],
        "artifacts": [],
        "workflow_state": {},
        "pending_user_input": None,
        "checkpoint": {},
        "active_task": None,
        "last_task": None,
    }


def test_continuation_request_detection():
    assert rss.is_react_continuation_request("继续")
    assert rss.is_react_continuation_request("按刚才继续")
    assert rss.is_react_continuation_request("continue")
    assert not rss.is_react_continuation_request("不用继续")
    assert not rss.is_react_continuation_request("帮我继续分析新的数据")


def test_plan_revision_requires_explicit_change_request():
    assert rss.is_react_plan_revision_request("请调整一下当前计划")
    assert rss.is_react_plan_revision_request("把步骤从四步改成六步")
    assert rss.is_react_plan_revision_request("Please revise the task plan")
    assert rss.is_react_plan_revision_request("计划要增加一个检查步骤")
    assert not rss.is_react_plan_revision_request("继续分析，顺便说明一下原因")
    assert not rss.is_react_plan_revision_request("不要修改计划，按原计划继续")
    assert not rss.is_react_plan_revision_request("修改报告内容，按原计划继续")
    assert not rss.is_react_plan_revision_request("don't change the plan")


def test_restore_react_task_plan_keeps_cancelled_items_terminal_and_single_active():
    plan = rss.restore_react_task_plan(
        {
            "task_plan": [
                {"content": "已完成项", "status": "completed"},
                {"content": "取消项", "status": "cancelled"},
                {"content": "多余进行项", "status": "in_progress"},
            ]
        }
    )

    assert [item["status"] for item in plan] == [
        "completed",
        "cancelled",
        "in_progress",
    ]


def test_restore_react_task_plan_reopens_cancelled_items_for_continuation():
    plan = rss.restore_react_task_plan(
        {
            "task_id": "task-1",
            "task_plan": [
                {"content": "已完成项", "status": "completed"},
                {"content": "取消项", "status": "cancelled"},
            ],
        },
        resume_cancelled=True,
    )

    assert [item["status"] for item in plan] == ["completed", "in_progress"]


def test_start_react_task_checkpoint_seeds_task_before_plan_exists():
    state = _state()

    checkpoint = rss.start_react_task_checkpoint(
        state,
        task_id="task-1",
        user_input="对比两个人的项目情况，生产一个报告",
        round_index=3,
    )

    assert checkpoint["task_id"] == "task-1"
    assert checkpoint["round_index"] == 3
    assert checkpoint["status"] == "in_progress"
    assert checkpoint["original_request"] == "对比两个人的项目情况，生产一个报告"
    assert state["active_task"] == checkpoint


def test_continuation_prompt_contains_original_goal_and_latest_message():
    prompt = rss.build_react_task_continuation_prompt(
        {
            "original_request": "对比两个人的项目情况，生产一个报告",
            "task_plan": [
                {"content": "收集甲的数据", "status": "completed"},
                {"content": "比较两人的项目", "status": "in_progress"},
            ],
            "completed_steps": [
                {"title": "收集甲的数据", "summary": "甲有 3 个项目。"}
            ],
            "last_result": "已完成甲方信息整理。",
        },
        "继续",
    )

    assert "对比两个人的项目情况，生产一个报告" in prompt
    assert "[completed] 收集甲的数据" in prompt
    assert "[in_progress] 比较两人的项目" in prompt
    assert "甲有 3 个项目。" in prompt
    assert "用户本轮消息：继续" in prompt


def test_task_checkpoint_keeps_progress_and_moves_completed_task_to_history():
    state = _state()
    plan = [
        {"content": "收集甲的项目", "status": "completed", "priority": "high"},
        {"content": "比较两人项目", "status": "in_progress", "priority": "high"},
    ]
    rss.update_react_task_checkpoint(
        state,
        task_id="task-1",
        original_request="对比两个人的项目情况",
        task_plan=plan,
        history_steps=[
            {
                "id": "step-1",
                "title": "收集甲的项目",
                "action": "ask_data_query",
                "status": "done",
                "outputs": [{"content": "甲有 3 个项目"}],
            }
        ],
        status="incomplete",
        plan_id="plan-1",
        plan_revision=2,
        plan_sequence=5,
        final_content="已收集甲的项目，尚未完成比较。",
    )

    checkpoint = state["active_task"]
    assert checkpoint["task_id"] == "task-1"
    assert checkpoint["original_request"] == "对比两个人的项目情况"
    assert checkpoint["completed_steps"][0]["summary"] == "甲有 3 个项目"
    assert checkpoint["last_result"] == "已收集甲的项目，尚未完成比较。"
    assert checkpoint["plan_id"] == "plan-1"
    assert checkpoint["plan_revision"] == 2
    assert checkpoint["plan_sequence"] == 5

    completed_plan = [dict(item, status="completed") for item in plan]
    rss.update_react_task_checkpoint(
        state,
        task_id="task-1",
        original_request="继续",
        task_plan=completed_plan,
        history_steps=[],
        status="completed",
        final_content="对比已完成。",
    )

    assert state["active_task"] is None
    assert state["last_task"]["original_request"] == "对比两个人的项目情况"
    assert state["last_task"]["status"] == "completed"


def test_recover_task_checkpoint_from_legacy_history_uses_task_id():
    state = _state()
    messages = [
        SimpleNamespace(type="human", content="原始对比请求", round_index=3),
        SimpleNamespace(
            type="view",
            round_index=3,
            content=json.dumps(
                {
                    "type": "react-agent",
                    "status": "incomplete",
                    "turn_id": "turn-3",
                    "task_id": "task-stable-id",
                    "task_plan": [
                        {"content": "比较两人项目", "status": "pending"}
                    ],
                    "steps": [],
                    "final_content": "第一人信息已整理。",
                }
            ),
        ),
    ]

    recovered = rss.recover_react_task_checkpoint_from_messages(state, messages)

    assert recovered["task_id"] == "task-stable-id"
    assert recovered["original_request"] == "原始对比请求"
    assert state["active_task"] == recovered


def test_recover_latest_cancelled_round_overrides_stale_active_task():
    state = _state()
    state["active_task"] = {
        "task_id": "old-task",
        "round_index": 1,
        "status": "incomplete",
        "original_request": "丁宇龙有哪些项目？",
        "task_plan": [{"content": "查询丁宇龙", "status": "pending"}],
    }
    messages = [
        SimpleNamespace(type="human", content="对比两个人的项目情况", round_index=2),
        SimpleNamespace(
            type="view",
            round_index=2,
            content=json.dumps(
                {
                    "type": "react-agent",
                    "status": "cancelled",
                    "round_index": 2,
                    "task_id": "new-task",
                    "task_plan": [
                        {"content": "比较两人项目", "status": "cancelled"}
                    ],
                }
            ),
        ),
    ]

    recovered = rss.recover_react_task_checkpoint_from_messages(state, messages)

    assert recovered["task_id"] == "new-task"
    assert recovered["original_request"] == "对比两个人的项目情况"
    assert state["active_task"]["task_id"] == "new-task"


def test_recover_latest_completed_round_does_not_revive_stale_task():
    state = _state()
    state["active_task"] = {
        "task_id": "old-task",
        "round_index": 1,
        "status": "incomplete",
        "original_request": "丁宇龙有哪些项目？",
        "task_plan": [{"content": "查询丁宇龙", "status": "pending"}],
    }
    messages = [
        SimpleNamespace(type="human", content="对比两个人的项目情况", round_index=2),
        SimpleNamespace(
            type="view",
            round_index=2,
            content=json.dumps(
                {
                    "type": "react-agent",
                    "status": "completed",
                    "round_index": 2,
                    "task_id": "new-task",
                    "task_plan": [],
                }
            ),
        ),
    ]

    assert rss.recover_react_task_checkpoint_from_messages(state, messages) is None
    assert state["active_task"] is None


def test_render_react_session_context_includes_active_checkpoint():
    state = _state()
    state["active_task"] = {
        "task_id": "task-1",
        "status": "incomplete",
        "original_request": "对比两个人的项目情况",
        "task_plan": [{"content": "比较乙的项目", "status": "pending"}],
        "completed_steps": [
            {
                "title": "收集甲的项目",
                "summary": "已确认甲有 3 个项目。",
            }
        ],
        "last_result": "甲方信息已整理。",
    }

    context = rss.render_react_session_context(
        state,
        rss.ConversationContext(history_messages=[], recent_history_text=""),
        current_user_input="继续",
    )

    assert "对比两个人的项目情况" in context
    assert "[pending] 比较乙的项目" in context
    assert "已确认甲有 3 个项目。" in context


def test_infer_active_skill_from_recent_view_message(tmp_path):
    skill_dir = tmp_path / "data-report"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Data Report\n", encoding="utf-8")
    message = SimpleNamespace(
        type="view",
        content=json.dumps(
            {
                "final_content": "已创建 data-report skill，文件位于 skills/data-report/SKILL.md",
                "steps": [],
            },
            ensure_ascii=False,
        ),
    )

    inferred = rss.infer_active_skill_from_messages([message], skills_dir=str(tmp_path))

    assert inferred is not None
    assert inferred["name"] == "data-report"
    assert inferred["path"].endswith("data-report/SKILL.md")


def test_record_uploaded_attachment_deduplicates_file(tmp_path):
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
    state = _state()

    rss.record_uploaded_attachment(state, str(csv_path))
    rss.record_uploaded_attachment(state, str(csv_path))

    assert len(state["attachments"]) == 1
    assert state["attachments"][0]["kind"] == "data"
    assert state["attachments"][0]["exists"] is True


def test_update_react_session_after_turn_records_skill_and_artifacts(tmp_path):
    skill_dir = tmp_path / "data-report"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Data Report\n", encoding="utf-8")
    state = _state()
    react_state = {"turn_id": "turn-1", "generated_images": ["/images/chart.png"]}
    history_steps = [
        {
            "outputs": [
                {
                    "output_type": "text",
                    "content": "Wrote skills/data-report/SKILL.md and pilot/tmp/conv-1/report.html",
                }
            ]
        }
    ]

    updated = rss.update_react_session_after_turn(
        state,
        react_state,
        history_steps,
        "data-report skill 已完成",
        skills_dir=str(tmp_path),
        status="completed",
    )

    assert updated["active_skill"]["name"] == "data-report"
    artifact_targets = {
        item.get("path") or item.get("url") for item in updated["artifacts"]
    }
    assert "skills/data-report/SKILL.md" in artifact_targets
    assert "/images/chart.png" in artifact_targets
    assert updated["checkpoint"]["last_turn_id"] == "turn-1"
