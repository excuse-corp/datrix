from __future__ import annotations

import asyncio

import pytest

from dbgpt_app.scene.ask_data.agents.orchestrator import AskDataOrchestrator
from dbgpt_app.scene.ask_data.ontology.schemas import OntologySnapshot
from dbgpt_app.scene.ask_data.planning.validator import (
    PlanValidationError,
    PlanValidator,
)
from dbgpt_app.scene.ask_data.query.query_spec import ResultColumn, SceneResult
from dbgpt_app.scene.ask_data.schemas.plan import (
    Clarification,
    CombinePlan,
    MainAgentPlan,
    PlanTask,
)
from dbgpt_app.scene.ask_data.snapshot import InMemorySnapshotService
from dbgpt_app.scene.ask_data.snapshot.registry import SceneAgentRegistry
from dbgpt_app.scene.ask_data.testsupport import build_test_snapshot


def _registry() -> SceneAgentRegistry:
    service = InMemorySnapshotService()
    service.save_ready(build_test_snapshot("contracts", "1"))
    service.activate("snap_contracts_1")
    return SceneAgentRegistry(service)


def _task(task_id: str = "task-1") -> PlanTask:
    return PlanTask(
        task_id=task_id,
        scene_id="contracts",
        question="contract amount",
        metrics=["contract_amount"],
        limit=2,
    )


def _ontology_snapshot() -> OntologySnapshot:
    return OntologySnapshot(
        snapshot_id="onto_contracts",
        ontology_id="global_business",
        revision=3,
        status="active",
        content_hash="sha256:ontology",
        graph_json={
            "nodes": [
                {
                    "id": "contract",
                    "type": "entity",
                    "name": "合同",
                    "aliases": [],
                    "description": "合同业务对象",
                    "data": {},
                    "provenance": [],
                },
                {
                    "id": "contract_amount",
                    "type": "metric",
                    "name": "合同金额",
                    "aliases": [],
                    "description": "",
                    "data": {"source_scene": "contracts"},
                    "provenance": [],
                },
            ],
            "edges": [
                {
                    "id": "belongs_to_contract_amount_contract",
                    "source": "contract_amount",
                    "target": "contract",
                    "type": "belongs_to",
                    "name": "属于实体",
                    "description": "",
                    "data": {},
                    "provenance": [],
                }
            ],
        },
        prompt_projection_json={},
    )


class _FakeQueryService:
    def __init__(self, failures: set[str] | None = None):
        self.failures = failures or set()
        self.active = 0
        self.max_active = 0

    def execute(self, *, task_id, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if task_id in self.failures:
                raise RuntimeError("query failed")
            return SceneResult(
                task_id=task_id,
                scene_id="contracts",
                scene_revision="1",
                snapshot_id="snap_contracts_1",
                status="succeeded",
                columns=[ResultColumn(key="contract_amount", type="metric", unit="元")],
                query_spec_hash="sha256:query",
                compiler_version="1.0",
                sql_hash="sha256:sql",
            )
        finally:
            self.active -= 1


class _SlowQueryService(_FakeQueryService):
    def __init__(self):
        super().__init__()

    def execute(self, *, task_id, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            import time

            time.sleep(0.03)
            return SceneResult(
                task_id=task_id,
                scene_id="contracts",
                scene_revision="1",
                snapshot_id="snap_contracts_1",
                status="succeeded",
                columns=[ResultColumn(key="contract_amount", type="metric", unit="元")],
                query_spec_hash="sha256:query",
                compiler_version="1.0",
                sql_hash="sha256:sql",
            )
        finally:
            self.active -= 1


def test_plan_validator_rejects_unknown_scene_and_too_many_tasks():
    validator = PlanValidator(_registry(), max_tasks=1)
    plan = MainAgentPlan(
        action="execute",
        tasks=[_task("task-1"), _task("task-2")],
        combine=CombinePlan(),
    )

    with pytest.raises(PlanValidationError) as raised:
        validator.validate(plan)

    assert {issue.code for issue in raised.value.issues} >= {"TOO_MANY_TASKS"}


def test_non_execute_plans_return_without_query_dispatch():
    registry = _registry()
    validator = PlanValidator(registry)
    service = _FakeQueryService()
    orchestrator = AskDataOrchestrator(validator, service)

    async def run():
        clarify = await orchestrator.execute(
            plan=MainAgentPlan(
                action="clarify",
                clarification=Clarification(
                    question="Which period?", field="time_range", options=["year"]
                ),
            ),
            engine_resolver=lambda _: None,
        )
        reject = await orchestrator.execute(
            plan=MainAgentPlan(
                action="reject", reason_code="NO_SCENE", message="Not supported"
            ),
            engine_resolver=lambda _: None,
        )
        return clarify, reject

    clarify, reject = asyncio.run(run())
    assert clarify.status == "clarification_required"
    assert reject.status == "rejected"
    assert service.max_active == 0


def test_orchestrator_reports_partial_success_and_limits_parallelism():
    registry = _registry()
    validator = PlanValidator(registry, max_tasks=3)
    service = _FakeQueryService({"task-2"})
    orchestrator = AskDataOrchestrator(
        validator,
        service,
        max_parallel_tasks=1,
        max_parallel_per_datasource=1,
    )
    plan = MainAgentPlan(
        action="execute",
        tasks=[_task("task-1"), _task("task-2"), _task("task-3")],
        combine=CombinePlan(),
    )

    result = asyncio.run(
        orchestrator.execute(plan=plan, engine_resolver=lambda _: object())
    )

    assert result.status == "partial_succeeded"
    assert len(result.results) == 2
    assert len(result.errors) == 1
    assert service.max_active == 1


def test_orchestrator_reports_all_failed():
    registry = _registry()
    validator = PlanValidator(registry)
    service = _FakeQueryService({"task-1"})
    orchestrator = AskDataOrchestrator(validator, service)
    plan = MainAgentPlan(action="execute", tasks=[_task()], combine=CombinePlan())

    result = asyncio.run(
        orchestrator.execute(plan=plan, engine_resolver=lambda _: object())
    )

    assert result.status == "failed"
    assert not result.results


def test_orchestrator_limits_concurrent_requests():
    registry = _registry()
    validator = PlanValidator(registry, max_tasks=1)
    service = _SlowQueryService()
    orchestrator = AskDataOrchestrator(
        validator,
        service,
        max_concurrent_queries=2,
        query_queue_timeout_seconds=2,
    )
    plan = MainAgentPlan(action="execute", tasks=[_task()], combine=CombinePlan())

    async def run_many():
        return await asyncio.gather(
            *[
                orchestrator.execute(
                    plan=plan,
                    engine_resolver=lambda _: object(),
                )
                for _ in range(5)
            ]
        )

    results = asyncio.run(run_many())

    assert all(item.status == "succeeded" for item in results)
    assert service.max_active == 2


def test_orchestrator_uses_active_ontology_only_after_scene_queries():
    registry = _registry()
    validator = PlanValidator(registry)
    service = _FakeQueryService()
    orchestrator = AskDataOrchestrator(
        validator,
        service,
        ontology_snapshot_provider=_ontology_snapshot,
    )
    plan = MainAgentPlan(action="execute", tasks=[_task()], combine=CombinePlan())

    result = asyncio.run(
        orchestrator.execute(plan=plan, engine_resolver=lambda _: object())
    )

    assert result.status == "succeeded"
    assert result.ontology_snapshot_id == "onto_contracts"
    assert result.bundle["ontology_context"]["ontology_snapshot_id"] == "onto_contracts"
    assert result.bundle["ontology_context"]["matched_scene_ids"] == ["contracts"]
    assert result.bundle["ontology_context"]["matched_column_keys"] == [
        "contract_amount"
    ]
    assert "Ontology v3" in result.bundle["answer"]
