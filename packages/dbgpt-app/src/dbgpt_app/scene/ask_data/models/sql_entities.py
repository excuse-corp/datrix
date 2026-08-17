"""SQLAlchemy metadata entities for AskData lifecycle records."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

from dbgpt.storage.metadata import Model


class AskDataSceneEntity(Model):
    __tablename__ = "ask_data_scene"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scene_id = Column(String(128), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=False, default="")
    status = Column(String(32), nullable=False, default="draft", index=True)
    latest_revision = Column(Integer, nullable=False, default=0)
    active_revision = Column(Integer, nullable=True)
    current_snapshot_id = Column(String(128), nullable=True)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    soft_deleted_at = Column(DateTime, nullable=True)


class AskDataSceneRevisionEntity(Model):
    __tablename__ = "ask_data_scene_revision"
    __table_args__ = (
        UniqueConstraint("scene_id", "revision", name="uq_ask_data_scene_revision"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    scene_id = Column(String(128), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="draft", index=True)
    data_source_name = Column(String(255), nullable=False)
    view_name = Column(String(255), nullable=False)
    semantic_md = Column(Text, nullable=False)
    parsed_config_json = Column(JSON, nullable=True)
    view_schema_json = Column(JSON, nullable=True)
    knowledge_space_name = Column(String(255), nullable=True)
    semantic_hash = Column(String(128), nullable=True)
    schema_hash = Column(String(128), nullable=True)
    knowledge_hash = Column(String(128), nullable=True)
    last_validated_at = Column(DateTime, nullable=True)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)


class AskDataSnapshotEntity(Model):
    __tablename__ = "ask_data_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "scene_id", "revision_id", name="uq_ask_data_snapshot_revision"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_id = Column(String(128), nullable=False, unique=True, index=True)
    scene_id = Column(String(128), nullable=False, index=True)
    revision_id = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False, index=True)
    content_hash = Column(String(128), nullable=False)
    semantic_hash = Column(String(128), nullable=False)
    schema_hash = Column(String(128), nullable=False)
    knowledge_hash = Column(String(128), nullable=False)
    routing_projection_json = Column(JSON, nullable=False)
    runtime_config_json = Column(JSON, nullable=False)
    schema_version = Column(String(32), nullable=False, default="1")
    query_spec_version = Column(String(32), nullable=False, default="1")
    compiler_version = Column(String(32), nullable=False, default="1")
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AskDataQueryRunEntity(Model):
    __tablename__ = "ask_data_query_run"
    __table_args__ = (
        Index(
            "uq_ask_data_query_run_idempotency",
            "user_id",
            "idempotency_key",
            unique=True,
            mssql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    query_id = Column(String(128), nullable=False, unique=True, index=True)
    request_id = Column(String(128), nullable=False, index=True)
    conversation_id = Column(String(128), nullable=True, index=True)
    user_id = Column(String(128), nullable=False, index=True)
    entry_type = Column(String(32), nullable=False, default="main_agent", index=True)
    question = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, index=True)
    plan_json = Column(JSON, nullable=True)
    result_json = Column(JSON, nullable=True)
    combine_mode = Column(String(32), nullable=True)
    ontology_snapshot_id = Column(String(128), nullable=True, index=True)
    snapshot_ids_json = Column(JSON, nullable=False, default=list)
    errors_json = Column(JSON, nullable=False, default=list)
    warnings_json = Column(JSON, nullable=False, default=list)
    clarification_json = Column(JSON, nullable=True)
    clarification_round = Column(Integer, nullable=False, default=0)
    clarification_expires_at = Column(DateTime, nullable=True)
    idempotency_key = Column(String(255), nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AskDataAgentRunEntity(Model):
    __tablename__ = "ask_data_agent_run"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_run_id = Column(String(128), nullable=False, unique=True, index=True)
    query_id = Column(String(128), nullable=False, index=True)
    task_id = Column(String(128), nullable=False, index=True)
    scene_id = Column(String(128), nullable=False, index=True)
    revision_id = Column(String(128), nullable=False)
    snapshot_id = Column(String(128), nullable=False)
    knowledge_space_name = Column(String(255), nullable=True)
    query_spec_hash = Column(String(128), nullable=True)
    compiler_version = Column(String(32), nullable=True)
    sql_hash = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False, index=True)
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AskDataAuditEventEntity(Model):
    __tablename__ = "ask_data_audit_event"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(128), nullable=False, unique=True, index=True)
    action = Column(String(128), nullable=False, index=True)
    resource_type = Column(String(64), nullable=False, index=True)
    resource_id = Column(String(128), nullable=False, index=True)
    user_id = Column(String(128), nullable=False, index=True)
    request_id = Column(String(128), nullable=False, index=True)
    outcome = Column(String(32), nullable=False, index=True)
    error_code = Column(String(128), nullable=True)
    details_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AskDataIdempotencyEntity(Model):
    __tablename__ = "ask_data_idempotency"
    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_ask_data_idempotency_scope_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope = Column(String(255), nullable=False, index=True)
    key = Column(String(255), nullable=False)
    fingerprint = Column(String(128), nullable=False)
    response_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, index=True)


__all__ = [
    "AskDataSceneEntity",
    "AskDataSceneRevisionEntity",
    "AskDataSnapshotEntity",
    "AskDataQueryRunEntity",
    "AskDataAgentRunEntity",
    "AskDataAuditEventEntity",
    "AskDataIdempotencyEntity",
]
