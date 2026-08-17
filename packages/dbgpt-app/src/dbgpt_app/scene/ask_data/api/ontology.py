"""HTTP management surface for the global business Ontology."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..ontology.fragments import source_snapshot_refs
from ..ontology.service import (
    OntologyConflictError,
    OntologyLifecycleService,
    OntologyServiceError,
    create_default_ontology_service,
)
from ..security import AskDataPrincipal
from .scenes import _require_admin, get_principal

router = APIRouter(prefix="/api/v1/ask-data/ontology", tags=["AskData Ontology"])
_service: OntologyLifecycleService = create_default_ontology_service()


class OntologyDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markdown: str = Field(min_length=1)
    expected_revision: int | None = Field(default=None, ge=1)


class OntologyPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markdown: str = Field(min_length=1)


class OntologyLayoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layout: dict[str, Any] = Field(default_factory=dict)


class OntologySceneSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, ge=1)
    apply: bool = False


def configure_ontology_service(service: OntologyLifecycleService) -> None:
    global _service
    _service = service


def get_ontology_service() -> OntologyLifecycleService:
    return _service


def _revision_payload(revision) -> dict[str, Any]:
    return {
        "ontology_id": revision.ontology_id,
        "revision": revision.revision,
        "status": revision.status.value,
        "markdown": revision.ontology_md,
        "content_hash": revision.content_hash,
        "validation_issues": revision.validation_issues,
        "created_by": revision.created_by,
        "created_at": revision.created_at.isoformat(),
        "updated_at": revision.updated_at.isoformat(),
    }


def _snapshot_payload(snapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "ontology_id": snapshot.ontology_id,
        "revision": snapshot.revision,
        "status": snapshot.status,
        "content_hash": snapshot.content_hash,
        "source_scene_snapshots": snapshot.source_scene_snapshots,
        "created_at": snapshot.created_at.isoformat(),
        "activated_at": snapshot.activated_at.isoformat()
        if snapshot.activated_at
        else None,
    }


def _authorized_scene_snapshots(principal: AskDataPrincipal) -> list[Any]:
    """Bind a publish to exactly the authorized active Scene versions."""
    from . import scenes

    snapshots = []
    for snapshot in scenes._snapshot_service.active_snapshots():
        if not principal.can_access_scene(snapshot.scene_id):
            continue
        snapshots.append(snapshot)
    return snapshots


def _source_scene_snapshots(principal: AskDataPrincipal) -> list[dict[str, str]]:
    return source_snapshot_refs(_authorized_scene_snapshots(principal))


def _http_error(exc: OntologyServiceError) -> HTTPException:
    status = 409 if isinstance(exc, OntologyConflictError) else 400
    if exc.code.endswith("NOT_FOUND"):
        status = 404
    return HTTPException(
        status_code=status, detail={"code": exc.code, "message": str(exc)}
    )


@router.get("")
def get_ontology(
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    draft = service.draft(user_id=principal.user_id)
    active = service.active_snapshot()
    return {
        "status": "succeeded",
        "data": {
            "draft": _revision_payload(draft),
            "active_snapshot": _snapshot_payload(active) if active else None,
            "source_scenes": _source_scene_snapshots(principal),
        },
    }


@router.get("/draft")
def get_draft(
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    return {
        "status": "succeeded",
        "data": _revision_payload(service.draft(user_id=principal.user_id)),
    }


@router.put("/draft")
def save_draft(
    payload: OntologyDraftRequest,
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        revision = service.save_draft(
            markdown=payload.markdown,
            user_id=principal.user_id,
            expected_revision=payload.expected_revision,
        )
        return {"status": "succeeded", "data": _revision_payload(revision)}
    except OntologyServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/compile-preview")
def compile_preview(
    payload: OntologyPreviewRequest,
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        return {"status": "succeeded", "data": service.preview(payload.markdown)}
    except OntologyServiceError as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_ONTOLOGY_MARKDOWN", "message": str(exc)},
        ) from exc


@router.post("/revisions/{revision}/validate")
def validate_revision(
    revision: int,
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        result = service.validate(revision, _authorized_scene_snapshots(principal))
        return {
            "status": "succeeded",
            "data": {
                "valid": result["valid"],
                "issues": result["issues"],
                "revision": _revision_payload(result["revision"]),
            },
        }
    except OntologyServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/revisions/{revision}/build-snapshot")
def build_snapshot(
    revision: int,
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        snapshot = service.build_snapshot(
            revision, _authorized_scene_snapshots(principal)
        )
        return {"status": "succeeded", "data": _snapshot_payload(snapshot)}
    except OntologyServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/snapshots/{snapshot_id}/activate")
def activate_snapshot(
    snapshot_id: str,
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        return {
            "status": "succeeded",
            "data": _snapshot_payload(service.activate(snapshot_id)),
        }
    except OntologyServiceError as exc:
        raise _http_error(exc) from exc


@router.get("/graph")
def get_graph(
    revision: int | None = None,
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    try:
        return {
            "status": "succeeded",
            "data": service.graph(
                revision=revision,
                user_id=principal.user_id,
                scene_snapshots=_authorized_scene_snapshots(principal),
            ),
        }
    except OntologyServiceError as exc:
        raise _http_error(exc) from exc


@router.get("/revisions")
def list_revisions(
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    service.draft(user_id=principal.user_id)
    return {
        "status": "succeeded",
        "data": {
            "items": [_revision_payload(item) for item in service.list_revisions()]
        },
    }


@router.get("/snapshots")
def list_snapshots(
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    service.draft(user_id=principal.user_id)
    return {
        "status": "succeeded",
        "data": {"items": [_snapshot_payload(item) for item in service.list_snapshots()]},
    }


@router.get("/revisions/{revision}/diff")
def revision_scene_diff(
    revision: int,
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        return {
            "status": "succeeded",
            "data": service.scene_sync(
                scene_snapshots=_authorized_scene_snapshots(principal),
                user_id=principal.user_id,
                expected_revision=revision,
            ),
        }
    except OntologyServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/sync-scenes")
def sync_scenes(
    payload: OntologySceneSyncRequest,
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        result = service.scene_sync(
            scene_snapshots=_authorized_scene_snapshots(principal),
            user_id=principal.user_id,
            expected_revision=payload.expected_revision,
            apply=payload.apply,
        )
        return {
            "status": "succeeded",
            "data": {
                "pending_count": result["pending_count"],
                "changes": result["changes"],
                "revision": _revision_payload(result["revision"]),
            },
        }
    except OntologyServiceError as exc:
        raise _http_error(exc) from exc


@router.put("/revisions/{revision}/layout")
def save_layout(
    revision: int,
    payload: OntologyLayoutRequest,
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        return {
            "status": "succeeded",
            "data": {
                "layout": service.save_layout(
                    revision, payload.layout, principal.user_id
                )
            },
        }
    except OntologyServiceError as exc:
        raise _http_error(exc) from exc


@router.get("/source-scenes")
def source_scenes(
    principal: AskDataPrincipal = Depends(get_principal),
):
    return {
        "status": "succeeded",
        "data": {"items": _source_scene_snapshots(principal)},
    }


__all__ = ["configure_ontology_service", "get_ontology_service", "router"]
