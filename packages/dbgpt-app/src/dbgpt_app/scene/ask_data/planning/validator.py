"""Validate main-agent plans before they reach the query service."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..query.query_spec import OrderBy, QuerySpec
from ..query.validator import QuerySpecValidationError, QuerySpecValidator
from ..schemas.plan import MainAgentPlan
from ..snapshot.registry import RegistryLookupError, SceneAgentRegistry


class PlanValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    path: str
    message: str


class PlanValidationError(ValueError):
    def __init__(self, issues: list[PlanValidationIssue]):
        self.issues = issues
        super().__init__(issues[0].message if issues else "Plan validation failed")


class PlanValidator:
    hard_max_tasks = 10

    def __init__(self, registry: SceneAgentRegistry, *, max_tasks: int = 10):
        self.registry = registry
        self.max_tasks = min(max_tasks, self.hard_max_tasks)
        self.query_validator = QuerySpecValidator()

    def validate(
        self,
        plan: MainAgentPlan,
        *,
        skip_query_spec_for_task_ids: set[str] | None = None,
    ) -> dict[str, object]:
        if plan.action != "execute":
            return {}
        skip_query_spec_for_task_ids = skip_query_spec_for_task_ids or set()
        issues: list[PlanValidationIssue] = []
        if len(plan.tasks) > self.max_tasks:
            issues.append(
                PlanValidationIssue(
                    code="TOO_MANY_TASKS",
                    path="tasks",
                    message=f"At most {self.max_tasks} tasks are allowed",
                )
            )
        snapshots = {}
        task_ids: set[str] = set()
        for index, task in enumerate(plan.tasks):
            path = f"tasks[{index}]"
            if task.task_id in task_ids:
                issues.append(
                    PlanValidationIssue(
                        code="DUPLICATE_TASK_ID",
                        path=f"{path}.task_id",
                        message=f"Duplicate task ID: {task.task_id}",
                    )
                )
            task_ids.add(task.task_id)
            try:
                snapshot = self.registry.get(task.scene_id)
            except RegistryLookupError:
                issues.append(
                    PlanValidationIssue(
                        code="SCENE_NOT_ACTIVE",
                        path=f"{path}.scene_id",
                        message=f"Scene is not active: {task.scene_id}",
                    )
                )
                continue
            snapshots[task.task_id] = snapshot
            if task.task_id not in skip_query_spec_for_task_ids:
                spec = QuerySpec(
                    dimensions=task.dimensions,
                    metrics=task.metrics,
                    time_grain=task.time_grain,
                    time_range=task.time_range,
                    named_filters=task.named_filters,
                    filters=task.filters,
                    order_by=[OrderBy.model_validate(item) for item in task.order_by],
                    limit=task.limit,
                )
                try:
                    self.query_validator.validate(spec, snapshot)
                except QuerySpecValidationError as exc:
                    issues.extend(
                        PlanValidationIssue(
                            code=item.code,
                            path=f"{path}.{item.path}",
                            message=item.message,
                        )
                        for item in exc.issues
                    )
        for derived in plan.combine.derived_metrics if plan.combine else []:
            issues.append(
                PlanValidationIssue(
                    code="DERIVED_METRIC_NOT_ALLOWED",
                    path="combine.derived_metrics",
                    message=(
                        f"Derived metric is not declared in active Ontology: {derived}"
                    )
                )
            )
        if plan.combine and plan.combine.mode in {"compare", "derived"}:
            if len(plan.tasks) < 2:
                issues.append(
                    PlanValidationIssue(
                        code="COMBINE_REQUIRES_MULTIPLE_TASKS",
                        path="combine.mode",
                        message="Compare and derived modes require multiple tasks",
                    )
                )
        if issues:
            raise PlanValidationError(issues)
        return snapshots


__all__ = ["PlanValidationError", "PlanValidationIssue", "PlanValidator"]
