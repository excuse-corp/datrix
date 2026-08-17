"""Production service assembly for the AskData SQL metadata backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from dbgpt.storage.metadata import DatabaseManager

from .agents.orchestrator import AskDataOrchestrator
from .models.audit import SqlAuditRepository
from .models.dao import create_ask_data_tables
from .models.idempotency import SqlIdempotencyRepository
from .models.repositories import SqlSceneRepository
from .models.run_repository import SqlRunRepository
from .planning.validator import PlanValidator
from .query_service import SceneQueryService
from .rag.manager import SceneKnowledgeManager
from .run_service import RunExecutionService
from .scene.publish_service import ScenePublishService
from .schemas.schema import ViewSchema
from .snapshot.registry import SceneAgentRegistry
from .snapshot.service import SqlSnapshotService


@dataclass(frozen=True)
class AskDataServiceBundle:
    scene_repository: SqlSceneRepository
    snapshot_service: SqlSnapshotService
    run_repository: SqlRunRepository
    registry: SceneAgentRegistry
    publish_service: ScenePublishService
    orchestrator: AskDataOrchestrator
    run_service: RunExecutionService
    audit_repository: SqlAuditRepository
    idempotency_repository: SqlIdempotencyRepository


def create_sql_ask_data_services(
    db_manager: DatabaseManager,
    schema_resolver: Callable[[str, int], ViewSchema],
    *,
    knowledge_manager: SceneKnowledgeManager | None = None,
    max_parallel_agents: int = 5,
    max_parallel_per_datasource: int = 3,
    max_concurrent_queries: int = 10,
    query_queue_timeout_seconds: float = 30,
    agent_timeout_seconds: float = 30,
    query_total_timeout_seconds: float = 120,
) -> AskDataServiceBundle:
    create_ask_data_tables(db_manager)
    scene_repository = SqlSceneRepository(db_manager)
    snapshot_service = SqlSnapshotService(db_manager)
    run_repository = SqlRunRepository(db_manager)
    audit_repository = SqlAuditRepository(db_manager)
    idempotency_repository = SqlIdempotencyRepository(db_manager)
    knowledge_manager = knowledge_manager or SceneKnowledgeManager()
    publish_service = ScenePublishService(
        scene_repository,
        snapshot_service,
        schema_resolver,
        knowledge_manager=knowledge_manager,
    )
    registry = SceneAgentRegistry(snapshot_service)
    orchestrator = AskDataOrchestrator(
        PlanValidator(registry),
        SceneQueryService(knowledge_manager=knowledge_manager),
        max_parallel_tasks=max_parallel_agents,
        max_parallel_per_datasource=max_parallel_per_datasource,
        max_concurrent_queries=max_concurrent_queries,
        query_queue_timeout_seconds=query_queue_timeout_seconds,
        task_timeout_seconds=agent_timeout_seconds,
        total_timeout_seconds=query_total_timeout_seconds,
    )
    run_service = RunExecutionService(orchestrator, run_repository)
    return AskDataServiceBundle(
        scene_repository=scene_repository,
        snapshot_service=snapshot_service,
        run_repository=run_repository,
        registry=registry,
        publish_service=publish_service,
        orchestrator=orchestrator,
        run_service=run_service,
        audit_repository=audit_repository,
        idempotency_repository=idempotency_repository,
    )


__all__ = ["AskDataServiceBundle", "create_sql_ask_data_services"]
