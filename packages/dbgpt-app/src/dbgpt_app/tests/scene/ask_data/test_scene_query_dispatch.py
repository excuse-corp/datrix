import asyncio

import pytest

from dbgpt_app.openapi.api_v1.tools import ask_data as ask_data_tools
from dbgpt_app.scene.ask_data.api import scenes
from dbgpt_app.scene.ask_data.models import InMemorySceneRepository
from dbgpt_app.scene.ask_data.schemas.plan import CombinePlan, MainAgentPlan, PlanTask
from dbgpt_app.scene.ask_data.snapshot import InMemorySnapshotService
from dbgpt_app.scene.ask_data.query.capabilities import query_metrics
from dbgpt_app.scene.ask_data.testsupport import build_test_snapshot


class ConcurrentSceneQueryAgent:
    def __init__(self):
        self.active = 0
        self.max_active = 0

    async def build_query_spec(self, snapshot, question):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            from dbgpt_app.scene.ask_data.query import QuerySpec

            return QuerySpec(metrics=[query_metrics(snapshot)[0]["key"]])
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_selected_scene_agents_build_specs_concurrently(monkeypatch):
    snapshot_service = InMemorySnapshotService()
    for scene_id in ("contracts", "projects"):
        snapshot = build_test_snapshot(scene_id, "r1")
        snapshot_service.save_ready(snapshot)
        snapshot_service.activate(snapshot.snapshot_id)
    monkeypatch.setattr(scenes, "_snapshot_service", snapshot_service)
    monkeypatch.setattr(scenes, "_service", InMemorySceneRepository())

    plan = MainAgentPlan(
        action="execute",
        combine=CombinePlan(mode="separate"),
        tasks=[
            PlanTask(
                task_id="contracts",
                scene_id="contracts",
                question="合同金额",
                metrics=["contract_amount"],
            ),
            PlanTask(
                task_id="projects",
                scene_id="projects",
                question="项目金额",
                metrics=["contract_amount"],
            ),
        ],
    )
    agent = ConcurrentSceneQueryAgent()

    resolved = await ask_data_tools._apply_scene_query_agent(
        plan,
        agent,
        max_parallel_scene_agents=2,
    )

    assert agent.max_active == 2
    assert [task.task_id for task in resolved.tasks] == ["contracts", "projects"]
