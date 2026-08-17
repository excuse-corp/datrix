from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from dbgpt_app.scene.ask_data.agents.orchestrator import QueryRunResult
from dbgpt_app.scene.ask_data.models import InMemoryRunRepository, QueryRun, RunStatus
from dbgpt_app.scene.ask_data.query.query_spec import ResultColumn, SceneResult
from dbgpt_app.scene.ask_data.run_service import RunExecutionService
from dbgpt_app.scene.ask_data.schemas.plan import CombinePlan, MainAgentPlan, PlanTask
from dbgpt_app.scene.ask_data.schemas.snapshot import (
    Snapshot,
    SnapshotSourceHashes,
    SnapshotStatus,
)


class FakePlanValidator:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def validate(self, plan):
        return {plan.tasks[0].task_id: self.snapshot}


class FakeOrchestrator:
    def __init__(self, snapshot, result):
        self.plan_validator = FakePlanValidator(snapshot)
        self.result = result
        self.calls = 0

    async def execute(self, *, plan, engine_resolver):
        self.calls += 1
        return self.result


class NoneOnMissingRunRepository(InMemoryRunRepository):
    def get_query(self, query_id):
        try:
            return super().get_query(query_id)
        except Exception:
            return None


def _plan() -> MainAgentPlan:
    return MainAgentPlan(
        action="execute",
        combine=CombinePlan(mode="separate"),
        tasks=[
            PlanTask(
                task_id="task_1",
                scene_id="contracts",
                question="total amount",
                metrics=["amount"],
            )
        ],
    )


def _snapshot() -> Snapshot:
    return Snapshot(
        snapshot_id="snap_contracts",
        scene_id="contracts",
        revision_id="2",
        status=SnapshotStatus.ACTIVE,
        content_hash="sha256:content",
        source_hashes=SnapshotSourceHashes(
            semantic_hash="sha256:semantic",
            schema_hash="sha256:schema",
            knowledge_hash="sha256:knowledge",
        ),
        routing_projection={},
        runtime_config={
            "data_source": "ecology",
            "rag": {"knowledge_space": "askdata_contracts_r2"},
        },
    )


def test_run_service_persists_query_and_agent_runs():
    scene_result = SceneResult(
        task_id="task_1",
        scene_id="contracts",
        scene_revision=2,
        snapshot_id="snap_contracts",
        status="succeeded",
        columns=[ResultColumn(key="amount", type="number")],
        query_spec_hash="sha256:query",
        compiler_version="1",
        sql_hash="sha256:sql",
    )
    repository = InMemoryRunRepository()
    orchestrator = FakeOrchestrator(
        _snapshot(), QueryRunResult(status="succeeded", results=[scene_result])
    )
    service = RunExecutionService(orchestrator, repository)

    query, result = __import__("asyncio").run(
        service.execute(
            question="total amount",
            plan=_plan(),
            user_id="user-1",
            request_id="req-1",
            engine_resolver=lambda _: object(),
            idempotency_key="same-request",
        )
    )

    assert result.status == "succeeded"
    assert query.status == RunStatus.SUCCEEDED
    agents = repository.agents_for_query(query.query_id)
    assert len(agents) == 1
    assert agents[0].status == RunStatus.SUCCEEDED
    assert agents[0].sql_hash == "sha256:sql"


def test_run_service_returns_idempotent_result_without_reexecution():
    repository = InMemoryRunRepository()
    orchestrator = FakeOrchestrator(
        _snapshot(), QueryRunResult(status="rejected", errors=[{"code": "NO"}])
    )
    service = RunExecutionService(orchestrator, repository)
    kwargs = {
        "question": "not allowed",
        "plan": MainAgentPlan(action="reject", reason_code="NO"),
        "user_id": "user-1",
        "request_id": "req-1",
        "engine_resolver": lambda _: object(),
        "idempotency_key": "same-request",
    }

    import asyncio

    first, _ = asyncio.run(service.execute(**kwargs))
    second, result = asyncio.run(service.execute(**kwargs))

    assert first.query_id == second.query_id
    assert result.status == "rejected"
    assert orchestrator.calls == 1


def test_run_service_treats_none_query_lookup_as_missing():
    import asyncio

    repository = NoneOnMissingRunRepository()
    orchestrator = FakeOrchestrator(
        _snapshot(), QueryRunResult(status="rejected", errors=[{"code": "NO"}])
    )
    service = RunExecutionService(orchestrator, repository)

    query, result = asyncio.run(
        service.execute(
            question="not allowed",
            plan=MainAgentPlan(action="reject", reason_code="NO"),
            user_id="user-1",
            request_id="req-none-missing",
            engine_resolver=lambda _: object(),
        )
    )

    assert query.status == RunStatus.REJECTED
    assert result.status == "rejected"
    assert repository.get_query(query.query_id) is not None


def test_run_service_persists_bundle_warnings_on_query_run():
    repository = InMemoryRunRepository()
    orchestrator = FakeOrchestrator(
        _snapshot(),
        QueryRunResult(
            status="succeeded",
            bundle={"warnings": [{"code": "RAG_DEGRADED"}]},
        ),
    )
    service = RunExecutionService(orchestrator, repository)

    query, _ = __import__("asyncio").run(
        service.execute(
            question="total amount",
            plan=_plan(),
            user_id="user-1",
            request_id="req-warning",
            engine_resolver=lambda _: object(),
        )
    )

    assert query.warnings == ["RAG_DEGRADED"]


def test_run_service_persists_runtime_ontology_snapshot_id():
    repository = InMemoryRunRepository()
    orchestrator = FakeOrchestrator(
        _snapshot(),
        QueryRunResult(status="succeeded", ontology_snapshot_id="onto_contracts"),
    )
    service = RunExecutionService(orchestrator, repository)

    query, result = __import__("asyncio").run(
        service.execute(
            question="total amount",
            plan=_plan(),
            user_id="user-1",
            request_id="req-ontology",
            engine_resolver=lambda _: object(),
        )
    )

    assert result.ontology_snapshot_id == "onto_contracts"
    assert query.ontology_snapshot_id == "onto_contracts"


def test_run_service_replays_success_result_and_rejects_key_reuse_conflict():
    import asyncio

    scene_result = SceneResult(
        task_id="task_1",
        scene_id="contracts",
        scene_revision=2,
        snapshot_id="snap_contracts",
        status="succeeded",
        columns=[ResultColumn(key="amount", type="number")],
        rows=[{"amount": 12}],
        row_count=1,
        query_spec_hash="sha256:query",
        compiler_version="1",
        sql_hash="sha256:sql",
    )
    repository = InMemoryRunRepository()
    orchestrator = FakeOrchestrator(
        _snapshot(), QueryRunResult(status="succeeded", results=[scene_result])
    )
    service = RunExecutionService(orchestrator, repository)
    kwargs = {
        "question": "total amount",
        "plan": _plan(),
        "user_id": "user-1",
        "request_id": "req-replay",
        "engine_resolver": lambda _: object(),
        "idempotency_key": "success-replay",
    }

    first, first_result = asyncio.run(service.execute(**kwargs))
    second, second_result = asyncio.run(service.execute(**kwargs))

    assert first.query_id == second.query_id
    assert (
        second_result.model_dump(mode="json")
        == first_result.model_dump(mode="json")
    )
    assert orchestrator.calls == 1
    with pytest.raises(ValueError, match="IDEMPOTENCY_KEY_CONFLICT"):
        asyncio.run(
            service.execute(
                **{**kwargs, "question": "different question"},
            )
        )


def test_query_run_expiry_accepts_naive_metadata_timestamp():
    run = QueryRun(
        query_id="query-expiry",
        request_id="request-expiry",
        user_id="user-1",
        question="question",
        status=RunStatus.CLARIFICATION_REQUIRED,
        clarification_expires_at=datetime.utcnow() - timedelta(seconds=1),
    )

    assert run.clarification_expired is True
