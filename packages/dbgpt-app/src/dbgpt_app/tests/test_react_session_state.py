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
    }


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
