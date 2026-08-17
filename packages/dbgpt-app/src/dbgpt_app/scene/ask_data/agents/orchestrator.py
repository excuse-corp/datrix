"""Bounded asynchronous orchestration for validated main-agent plans."""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field

from ..planning.validator import PlanValidator
from ..query.query_spec import QuerySpec, SceneResult
from ..query_service import SceneQueryService
from ..result import ResultBundleBuilder
from ..schemas.plan import MainAgentPlan, PlanTask


class QueryRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    results: list[SceneResult] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)
    snapshot_ids: list[str] = Field(default_factory=list)
    combine: dict[str, Any] | None = None
    bundle: dict[str, Any] | None = None
    clarification: dict[str, Any] | None = None
    duration_ms: int = 0
    ontology_snapshot_id: str | None = None


class AskDataOrchestrator:
    def __init__(
        self,
        plan_validator: PlanValidator,
        query_service: SceneQueryService,
        *,
        max_parallel_tasks: int = 5,
        max_parallel_per_datasource: int = 3,
        max_concurrent_queries: int = 10,
        query_queue_timeout_seconds: float = 30,
        task_timeout_seconds: float = 30,
        total_timeout_seconds: float = 120,
        result_builder: ResultBundleBuilder | None = None,
        ontology_snapshot_provider: Callable[[], Any | None] | None = None,
    ):
        self.plan_validator = plan_validator
        self.query_service = query_service
        self.max_parallel_tasks = min(max_parallel_tasks, 10)
        self.max_parallel_per_datasource = max(1, min(max_parallel_per_datasource, 10))
        self.max_concurrent_queries = max(1, min(max_concurrent_queries, 100))
        self.query_queue_timeout_seconds = max(0, query_queue_timeout_seconds)
        self._query_semaphore = asyncio.Semaphore(self.max_concurrent_queries)
        self.task_timeout_seconds = task_timeout_seconds
        self.total_timeout_seconds = total_timeout_seconds
        self.result_builder = result_builder or ResultBundleBuilder()
        self.ontology_snapshot_provider = ontology_snapshot_provider

    async def execute(
        self,
        *,
        plan: MainAgentPlan,
        engine_resolver: Callable[[str], Any | Awaitable[Any]],
        max_agents: int | None = None,
        sql_by_task: dict[str, str] | None = None,
    ) -> QueryRunResult:
        acquired = False
        try:
            await asyncio.wait_for(
                self._query_semaphore.acquire(),
                timeout=self.query_queue_timeout_seconds,
            )
            acquired = True
        except asyncio.TimeoutError:
            return QueryRunResult(
                status="rejected",
                errors=[
                    {
                        "code": "QUERY_QUEUE_TIMEOUT",
                        "message": "Query concurrency limit is temporarily full",
                    }
                ],
            )
        try:
            return await self._execute(
                plan=plan,
                engine_resolver=engine_resolver,
                max_agents=max_agents,
                sql_by_task=sql_by_task,
            )
        finally:
            if acquired:
                self._query_semaphore.release()

    async def _execute(
        self,
        *,
        plan: MainAgentPlan,
        engine_resolver: Callable[[str], Any | Awaitable[Any]],
        max_agents: int | None = None,
        sql_by_task: dict[str, str] | None = None,
    ) -> QueryRunResult:
        started = monotonic()
        if plan.action == "clarify":
            return QueryRunResult(
                status="clarification_required",
                clarification=plan.clarification.model_dump(mode="json")
                if plan.clarification
                else None,
            )
        if plan.action == "reject":
            return QueryRunResult(
                status="rejected",
                errors=[
                    {
                        "code": plan.reason_code or "REJECTED",
                        "message": plan.message or "Rejected",
                    }
                ],
            )
        agent_limit = min(
            max_agents
            or getattr(self.plan_validator, "max_tasks", self.max_parallel_tasks),
            10,
        )
        if len(plan.tasks) > agent_limit:
            return QueryRunResult(
                status="rejected",
                errors=[
                    {
                        "code": "MAX_AGENTS_EXCEEDED",
                        "message": f"At most {agent_limit} tasks are allowed",
                    }
                ],
            )
        sql_by_task = sql_by_task or {}
        if sql_by_task:
            snapshots = self.plan_validator.validate(
                plan,
                skip_query_spec_for_task_ids=set(sql_by_task),
            )
        else:
            snapshots = self.plan_validator.validate(plan)
        task_semaphore = asyncio.Semaphore(self.max_parallel_tasks)
        datasource_semaphores: dict[str, asyncio.Semaphore] = {}
        results: list[SceneResult] = []
        errors: list[dict[str, str]] = []

        async def run_task(task: PlanTask) -> None:
            snapshot = snapshots[task.task_id]
            data_source = snapshot.runtime_config["data_source"]
            datasource_semaphore = datasource_semaphores.setdefault(
                data_source, asyncio.Semaphore(self.max_parallel_per_datasource)
            )
            try:
                async with task_semaphore, datasource_semaphore:
                    engine = engine_resolver(data_source)
                    if asyncio.iscoroutine(engine):
                        engine = await engine
                    sql = sql_by_task.get(task.task_id)
                    if sql:
                        result = await asyncio.wait_for(
                            asyncio.to_thread(
                                self.query_service.execute_sql,
                                task_id=task.task_id,
                                sql=sql,
                                snapshot=snapshot,
                                engine=engine,
                                rag={"question": task.question},
                            ),
                            timeout=self.task_timeout_seconds,
                        )
                        results.append(result)
                        return
                    spec = QuerySpec(
                        dimensions=task.dimensions,
                        metrics=task.metrics,
                        time_grain=task.time_grain,
                        time_range=task.time_range,
                        named_filters=task.named_filters,
                        filters=task.filters,
                        order_by=task.order_by,
                        limit=task.limit,
                    )
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            self.query_service.execute,
                            task_id=task.task_id,
                            spec=spec,
                            snapshot=snapshot,
                            engine=engine,
                            rag={"question": task.question},
                        ),
                        timeout=self.task_timeout_seconds,
                    )
                    results.append(result)
            except asyncio.TimeoutError:
                errors.append(
                    {
                        "task_id": task.task_id,
                        "code": "AGENT_TIMEOUT",
                        "message": "Task timed out",
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "task_id": task.task_id,
                        "code": type(exc).__name__,
                        "message": str(exc),
                    }
                )

        tasks = [asyncio.create_task(run_task(task)) for task in plan.tasks]
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks), timeout=self.total_timeout_seconds
            )
        except asyncio.TimeoutError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            errors.append(
                {
                    "task_id": "_run",
                    "code": "QUERY_TOTAL_TIMEOUT",
                    "message": "Query run timed out",
                }
            )
            await asyncio.gather(*tasks, return_exceptions=True)
        status = (
            "succeeded"
            if results and not errors
            else "partial_succeeded"
            if results
            else "failed"
        )
        ontology_snapshot = self._active_ontology_snapshot()
        bundle = self.result_builder.build(
            results=results,
            snapshots={snapshot.scene_id: snapshot for snapshot in snapshots.values()},
            mode=plan.combine.mode if plan.combine else "separate",
            keys=plan.combine.keys if plan.combine else None,
            derived_metrics=plan.combine.derived_metrics if plan.combine else None,
            status=status,
            ontology_snapshot=ontology_snapshot,
            question="\n".join(task.question for task in plan.tasks),
        )
        return QueryRunResult(
            status=status,
            results=results,
            errors=errors,
            snapshot_ids=[snapshot.snapshot_id for snapshot in snapshots.values()],
            combine=plan.combine.model_dump(mode="json") if plan.combine else None,
            bundle=bundle.model_dump(mode="json"),
            duration_ms=int((monotonic() - started) * 1000),
            ontology_snapshot_id=getattr(ontology_snapshot, "snapshot_id", None),
        )

    def _active_ontology_snapshot(self) -> Any | None:
        if self.ontology_snapshot_provider is None:
            return None
        try:
            return self.ontology_snapshot_provider()
        except Exception:
            return None


__all__ = ["AskDataOrchestrator", "QueryRunResult"]
