"""Execution service that persists QueryRun and AgentRun audit records."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, Awaitable, Callable
from uuid import uuid4

from .agents.orchestrator import AskDataOrchestrator, QueryRunResult
from .models import AgentRun, InMemoryRunRepository, QueryRun, RunStatus
from .schemas.plan import MainAgentPlan


class RunExecutionService:
    def __init__(
        self,
        orchestrator: AskDataOrchestrator,
        repository: InMemoryRunRepository | None = None,
    ):
        self.orchestrator = orchestrator
        self.repository = repository or InMemoryRunRepository()

    def reset_conversation(
        self, conversation_id: str, user_id: str | None = None
    ) -> int:
        return self.repository.reset_conversation(conversation_id, user_id=user_id)

    async def execute(
        self,
        *,
        question: str,
        plan: MainAgentPlan,
        user_id: str,
        request_id: str,
        engine_resolver: Callable[[str], Any | Awaitable[Any]],
        conversation_id: str | None = None,
        idempotency_key: str | None = None,
        query_id: str | None = None,
        max_agents: int | None = None,
        entry_type: str = "main_agent",
        sql_by_task: dict[str, str] | None = None,
    ) -> tuple[QueryRun, QueryRunResult]:
        if idempotency_key:
            existing = self.repository.find_by_idempotency(user_id, idempotency_key)
            if existing:
                if (
                    existing.question != question
                    or existing.plan_json != plan.model_dump(mode="json")
                    or existing.entry_type != entry_type
                ):
                    raise ValueError("IDEMPOTENCY_KEY_CONFLICT")
                return existing, self._replay_result(existing)
        query_id = query_id or f"qry_{uuid4().hex}"
        started_at = datetime.now(timezone.utc)
        run_model = QueryRun(
                query_id=query_id,
                request_id=request_id,
                conversation_id=conversation_id,
                user_id=user_id,
                entry_type=entry_type,
                question=question,
                plan_json=plan.model_dump(mode="json"),
                combine_mode=plan.combine.mode if plan.combine else None,
                ontology_snapshot_id=plan.ontology_snapshot_id,
                idempotency_key=idempotency_key,
                status=RunStatus.RUNNING,
                started_at=started_at,
                clarification_round=(
                    self.repository.get_query(query_id).clarification_round
                    if query_id and self._query_exists(query_id)
                    else 0
                ),
            )
        if query_id and self._query_exists(query_id):
            existing = self.repository.get_query(query_id)
            if existing.status != RunStatus.CLARIFICATION_REQUIRED:
                raise ValueError("QUERY_NOT_CLARIFICATION_REQUIRED")
            run = self.repository.save_query(
                existing.model_copy(update=run_model.model_dump())
            )
        else:
            try:
                run = self.repository.create_query(run_model)
            except Exception as exc:
                if idempotency_key:
                    existing = self.repository.find_by_idempotency(
                        user_id, idempotency_key
                    )
                    if existing is not None:
                        if (
                            existing.question != question
                            or existing.plan_json != plan.model_dump(mode="json")
                            or existing.entry_type != entry_type
                        ):
                            raise ValueError("IDEMPOTENCY_KEY_CONFLICT")
                        return existing, self._replay_result(existing)
                raise exc
        started = monotonic()
        snapshots = {}
        try:
            if plan.action == "execute":
                if sql_by_task:
                    snapshots = self.orchestrator.plan_validator.validate(
                        plan,
                        skip_query_spec_for_task_ids=set(sql_by_task),
                    )
                else:
                    snapshots = self.orchestrator.plan_validator.validate(plan)
                for task in plan.tasks:
                    snapshot = snapshots[task.task_id]
                    self.repository.create_agent(
                        AgentRun(
                            agent_run_id=f"agr_{uuid4().hex}",
                            query_id=query_id,
                            task_id=task.task_id,
                            scene_id=task.scene_id,
                            revision_id=snapshot.revision_id,
                            snapshot_id=snapshot.snapshot_id,
                            knowledge_space_name=snapshot.runtime_config["rag"].get(
                                "knowledge_space"
                            ),
                            status=RunStatus.RUNNING,
                            started_at=datetime.now(timezone.utc),
                        )
                    )
            execute_kwargs = {"plan": plan, "engine_resolver": engine_resolver}
            if max_agents is not None:
                execute_kwargs["max_agents"] = max_agents
            if sql_by_task is not None:
                execute_kwargs["sql_by_task"] = sql_by_task
            result = await self.orchestrator.execute(**execute_kwargs)
        except Exception as exc:
            result = QueryRunResult(
                status="failed",
                errors=[{"code": type(exc).__name__, "message": str(exc)}],
                snapshot_ids=[snapshot.snapshot_id for snapshot in snapshots.values()],
            )
        finished = datetime.now(timezone.utc)
        duration_ms = int((monotonic() - started) * 1000)
        status = RunStatus(result.status)
        warnings = [warning for item in result.results for warning in item.warnings]
        for warning in (result.bundle or {}).get("warnings", []):
            if isinstance(warning, dict):
                value = warning.get("code") or warning.get("message")
            else:
                value = str(warning)
            if value and value not in warnings:
                warnings.append(value)
        updated = run.model_copy(
            update={
                "status": status,
                "snapshot_ids": result.snapshot_ids,
                "errors": result.errors,
                "warnings": warnings,
                "result_json": result.model_dump(mode="json"),
                "ontology_snapshot_id": (
                    result.ontology_snapshot_id or plan.ontology_snapshot_id
                ),
                "finished_at": finished,
                "duration_ms": duration_ms,
                "clarification": result.clarification,
                "clarification_expires_at": (
                    finished + timedelta(minutes=10)
                    if result.status == "clarification_required"
                    else None
                ),
                "clarification_round": (
                    run.clarification_round + 1
                    if result.status == "clarification_required"
                    else run.clarification_round
                ),
            }
        )
        self.repository.save_query(updated)
        self._finish_agents(query_id, result, finished, duration_ms)
        return updated, result

    @staticmethod
    def _replay_result(run: QueryRun) -> QueryRunResult:
        if run.result_json:
            return QueryRunResult.model_validate(run.result_json)
        return QueryRunResult(
            status=run.status.value,
            errors=run.errors,
            snapshot_ids=run.snapshot_ids,
            combine={"mode": run.combine_mode} if run.combine_mode else None,
            duration_ms=run.duration_ms or 0,
            clarification=run.clarification,
        )

    def _query_exists(self, query_id: str) -> bool:
        try:
            return self.repository.get_query(query_id) is not None
        except Exception:
            return False

    def _finish_agents(
        self,
        query_id: str,
        result: QueryRunResult,
        finished: datetime,
        duration_ms: int,
    ) -> None:
        errors_by_task = {item.get("task_id"): item for item in result.errors}
        results_by_task = {item.task_id: item for item in result.results}
        for agent in self.repository.agents_for_query(query_id):
            scene_result = results_by_task.get(agent.task_id)
            error = errors_by_task.get(agent.task_id)
            if scene_result:
                updated = agent.model_copy(
                    update={
                        "status": RunStatus.SUCCEEDED,
                        "query_spec_hash": scene_result.query_spec_hash,
                        "compiler_version": scene_result.compiler_version,
                        "sql_hash": scene_result.sql_hash,
                        "finished_at": finished,
                        "duration_ms": scene_result.duration_ms,
                    }
                )
            else:
                updated = agent.model_copy(
                    update={
                        "status": RunStatus.FAILED,
                        "error_code": error.get("code") if error else "QUERY_FAILED",
                        "error_message": error.get("message")
                        if error
                        else "Query task did not produce a result",
                        "finished_at": finished,
                        "duration_ms": duration_ms,
                    }
                )
            self.repository.save_agent(updated)


__all__ = ["RunExecutionService"]
