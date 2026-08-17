"""Thread-safe MVP repository for QueryRun and AgentRun records."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock

from dbgpt.storage.metadata import DatabaseManager

from .dao import AskDataAgentRunDao, AskDataQueryRunDao
from .runs import AgentRun, QueryRun, RunStatus
from .sql_entities import AskDataAgentRunEntity, AskDataQueryRunEntity


class RunRepositoryError(RuntimeError):
    pass


class InMemoryRunRepository:
    def __init__(self) -> None:
        self._queries: dict[str, QueryRun] = {}
        self._agents: dict[str, AgentRun] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._lock = RLock()

    def create_query(self, run: QueryRun) -> QueryRun:
        with self._lock:
            if run.query_id in self._queries:
                raise RunRepositoryError("QUERY_ID_CONFLICT")
            if run.idempotency_key:
                key = (run.user_id, run.idempotency_key)
                existing = self._idempotency.get(key)
                if existing and existing != run.query_id:
                    raise RunRepositoryError("IDEMPOTENCY_KEY_CONFLICT")
                self._idempotency[key] = run.query_id
            self._queries[run.query_id] = run
            return run

    def get_query(self, query_id: str) -> QueryRun:
        with self._lock:
            try:
                return self._queries[query_id]
            except KeyError as exc:
                raise RunRepositoryError("QUERY_NOT_FOUND") from exc

    def find_by_idempotency(self, user_id: str, key: str) -> QueryRun | None:
        with self._lock:
            query_id = self._idempotency.get((user_id, key))
            return self._queries.get(query_id) if query_id else None

    def save_query(self, run: QueryRun) -> QueryRun:
        with self._lock:
            if run.query_id not in self._queries:
                raise RunRepositoryError("QUERY_NOT_FOUND")
            self._queries[run.query_id] = run
            return run

    def create_agent(self, run: AgentRun) -> AgentRun:
        with self._lock:
            if run.agent_run_id in self._agents:
                raise RunRepositoryError("AGENT_RUN_ID_CONFLICT")
            self._agents[run.agent_run_id] = run
            return run

    def get_agent(self, agent_run_id: str) -> AgentRun:
        with self._lock:
            try:
                return self._agents[agent_run_id]
            except KeyError as exc:
                raise RunRepositoryError("AGENT_RUN_NOT_FOUND") from exc

    def save_agent(self, run: AgentRun) -> AgentRun:
        with self._lock:
            if run.agent_run_id not in self._agents:
                raise RunRepositoryError("AGENT_RUN_NOT_FOUND")
            self._agents[run.agent_run_id] = run
            return run

    def agents_for_query(self, query_id: str) -> list[AgentRun]:
        with self._lock:
            return [run for run in self._agents.values() if run.query_id == query_id]

    def list_queries(self) -> list[QueryRun]:
        with self._lock:
            return list(self._queries.values())

    def reset_conversation(
        self, conversation_id: str, user_id: str | None = None
    ) -> int:
        with self._lock:
            count = 0
            for query_id, run in list(self._queries.items()):
                if (
                    run.conversation_id == conversation_id
                    and (user_id is None or run.user_id == user_id)
                    and run.status == RunStatus.CLARIFICATION_REQUIRED
                ):
                    self._queries[query_id] = run.model_copy(
                        update={
                            "status": RunStatus.FAILED,
                            "errors": [
                                {
                                    "code": "CONVERSATION_RESET",
                                    "message": "Conversation was reset",
                                }
                            ],
                            "clarification_expires_at": datetime.now(timezone.utc),
                            "finished_at": datetime.now(timezone.utc),
                        }
                    )
                    count += 1
            return count


class SqlRunRepository:
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
        self.query_dao = AskDataQueryRunDao(db_manager)
        self.agent_dao = AskDataAgentRunDao(db_manager)

    def create_query(self, run: QueryRun) -> QueryRun:
        existing = self.find_by_idempotency(run.user_id, run.idempotency_key)
        if existing and existing.query_id != run.query_id:
            raise RunRepositoryError("IDEMPOTENCY_KEY_CONFLICT")
        if self.get_query(run.query_id) is not None:
            raise RunRepositoryError("QUERY_ID_CONFLICT")
        return self.query_dao.save_query(run)

    def get_query(self, query_id: str) -> QueryRun | None:
        return self.query_dao.get_query(query_id)

    def find_by_idempotency(self, user_id: str, key: str | None) -> QueryRun | None:
        if not key:
            return None
        with self.db_manager.session(commit=False) as session:
            entity = (
                session.query(AskDataQueryRunEntity)
                .filter_by(user_id=user_id, idempotency_key=key)
                .first()
            )
            return self.query_dao.get_query(entity.query_id) if entity else None

    def save_query(self, run: QueryRun) -> QueryRun:
        if self.get_query(run.query_id) is None:
            raise RunRepositoryError("QUERY_NOT_FOUND")
        return self.query_dao.save_query(run)

    def create_agent(self, run: AgentRun) -> AgentRun:
        if self.get_agent(run.agent_run_id) is not None:
            raise RunRepositoryError("AGENT_RUN_ID_CONFLICT")
        return self.agent_dao.save_agent(run)

    def get_agent(self, agent_run_id: str) -> AgentRun | None:
        return self.agent_dao.get_agent(agent_run_id)

    def save_agent(self, run: AgentRun) -> AgentRun:
        if self.get_agent(run.agent_run_id) is None:
            raise RunRepositoryError("AGENT_RUN_NOT_FOUND")
        return self.agent_dao.save_agent(run)

    def agents_for_query(self, query_id: str) -> list[AgentRun]:
        with self.db_manager.session(commit=False) as session:
            entities = (
                session.query(AskDataAgentRunEntity).filter_by(query_id=query_id).all()
            )
        return [
            agent
            for entity in entities
            if (agent := self.agent_dao.get_agent(entity.agent_run_id)) is not None
        ]

    def list_queries(self) -> list[QueryRun]:
        with self.db_manager.session(commit=False) as session:
            query_ids = [
                item.query_id for item in session.query(AskDataQueryRunEntity).all()
            ]
        return [
            query
            for query_id in query_ids
            if (query := self.query_dao.get_query(query_id)) is not None
        ]

    def reset_conversation(
        self, conversation_id: str, user_id: str | None = None
    ) -> int:
        count = 0
        for run in self.list_queries():
            if (
                run.conversation_id == conversation_id
                and (user_id is None or run.user_id == user_id)
                and run.status == RunStatus.CLARIFICATION_REQUIRED
            ):
                self.save_query(
                    run.model_copy(
                        update={
                            "status": RunStatus.FAILED,
                            "errors": [
                                {
                                    "code": "CONVERSATION_RESET",
                                    "message": "Conversation was reset",
                                }
                            ],
                            "clarification_expires_at": datetime.now(timezone.utc),
                            "finished_at": datetime.now(timezone.utc),
                        }
                    )
                )
                count += 1
        return count


__all__ = ["InMemoryRunRepository", "RunRepositoryError", "SqlRunRepository"]
