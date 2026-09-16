from pathlib import Path

import pytest

from dbgpt.agent.skill.loader import SkillLoader
from dbgpt_app.openapi.api_v1 import skill_state


def _write_skill_md(path: Path, name: str = "demo-skill") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {name}\n"
        "description: Demo skill\n"
        "version: 1.0.0\n"
        "skill_type: custom\n"
        "---\n"
        "Use this skill for tests.\n",
        encoding="utf-8",
    )


def test_skill_state_persists_enabled_flag(monkeypatch, tmp_path):
    state_file = tmp_path / "meta" / "skill_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(skill_state, "_state_path", lambda: state_file)

    skills_root = tmp_path / "skills"
    _write_skill_md(skills_root / "demo" / "SKILL.md", "demo")

    assert skill_state.skill_enabled("demo/SKILL.md", skills_dir=str(skills_root))

    entry = skill_state.set_skill_enabled(
        "demo/SKILL.md",
        False,
        skill_name="demo",
        skills_dir=str(skills_root),
    )

    assert entry["file_path"] == "demo/SKILL.md"
    assert entry["enabled"] is False
    assert not skill_state.skill_enabled("demo/SKILL.md", skills_dir=str(skills_root))
    assert skill_state.load_skill_state()["skills"]["demo/SKILL.md"]["enabled"] is False

    skill_state.remove_skill_state("demo/SKILL.md", skills_dir=str(skills_root))
    assert skill_state.skill_enabled("demo/SKILL.md", skills_dir=str(skills_root))


def test_normalize_skill_path_rejects_empty_and_outside_paths(tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir()

    with pytest.raises(ValueError, match="file_path is required"):
        skill_state.normalize_skill_path("", skills_dir=str(skills_root))

    with pytest.raises(ValueError, match="Invalid skill file path"):
        skill_state.normalize_skill_path("../outside/SKILL.md", skills_dir=str(skills_root))


def test_skill_loader_skips_resource_json_without_skill_metadata(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill_md(skills_root / "real-skill" / "SKILL.md", "real-skill")
    asset_path = skills_root / "real-skill" / "assets" / "sample-data.json"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_text('{"rows": [{"value": 1}]}', encoding="utf-8")

    skills = SkillLoader().load_skills_from_directory(str(skills_root), recursive=True)
    names = [skill.metadata.name for skill in skills if skill and skill.metadata]

    assert names == ["real-skill"]
    assert "Unknown" not in names
