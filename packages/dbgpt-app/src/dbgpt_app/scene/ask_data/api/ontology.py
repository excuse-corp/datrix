"""HTTP management surface for the global business Ontology."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from dbgpt_app.openapi.api_view_model import Result

from ..ontology.fragments import scene_snapshot, source_snapshot_refs
from ..ontology.markdown import with_managed_frontmatter
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


class OntologyDraftFromScenesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, ge=1)
    apply: bool = False
    mode: Literal["llm", "rules"] | None = None


class OntologyPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)


class DefaultModelRequest(BaseModel):
    model_name: str = Field(min_length=1)


def configure_ontology_service(service: OntologyLifecycleService) -> None:
    global _service
    _service = service


def get_ontology_service() -> OntologyLifecycleService:
    return _service


def _default_model_path():
    from pathlib import Path

    return Path("pilot/meta_data/default_model.json")


@router.get("/default-model")
def get_default_model():
    import json

    path = _default_model_path()
    if not path.exists():
        return Result.succ({"model_name": None})
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("model_name")
    except Exception:
        value = None
    return Result.succ({"model_name": value})


@router.put("/default-model")
def set_default_model(
    payload: DefaultModelRequest,
    principal: AskDataPrincipal = Depends(get_principal),
):
    import json

    _require_admin(principal)
    path = _default_model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model_name": payload.model_name}), encoding="utf-8")
    return Result.succ({"model_name": payload.model_name})


def _revision_payload(revision) -> dict[str, Any]:
    markdown = with_managed_frontmatter(revision.ontology_md, revision.revision)
    return {
        "ontology_id": revision.ontology_id,
        "revision": revision.revision,
        "status": revision.status.value,
        "markdown": markdown,
        "content_hash": revision.content_hash,
        "source_scene_snapshots": revision.source_scene_snapshots,
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
    snapshot_service = scenes.get_publish_service().snapshot_service
    authorizer = getattr(scenes, "_authorizer", None)
    for snapshot in snapshot_service.active_snapshots():
        if authorizer is not None and not principal.can_access_scene(snapshot.scene_id):
            continue
        snapshots.append(snapshot)
    return snapshots


def _authorized_scene_sources(principal: AskDataPrincipal) -> list[dict[str, Any]]:
    from . import scenes

    repository = scenes.get_scene_service().repository
    sources: list[dict[str, Any]] = []
    for snapshot in _authorized_scene_snapshots(principal):
        semantic_md = _semantic_md_from_snapshot(snapshot)
        try:
            revision = repository.get_revision(
                snapshot.scene_id, int(snapshot.revision_id)
            )
            if revision.semantic_md:
                semantic_md = revision.semantic_md
        except Exception:
            pass
        sources.append({"snapshot": snapshot, "semantic_md": semantic_md})
    return sources


def _semantic_md_from_snapshot(snapshot: Any) -> str | None:
    runtime_config = getattr(snapshot, "runtime_config", {}) or {}
    if not isinstance(runtime_config, dict):
        return None
    documents = runtime_config.get("documents", {})
    if not isinstance(documents, dict):
        return None
    semantic_md = documents.get("semantic_md")
    return semantic_md if isinstance(semantic_md, str) and semantic_md.strip() else None


def _source_scene_snapshots(principal: AskDataPrincipal) -> list[dict[str, str]]:
    return source_snapshot_refs(_authorized_scene_sources(principal))


def _source_scene_display_refs(principal: AskDataPrincipal) -> list[dict[str, str]]:
    sources = _authorized_scene_sources(principal)
    refs = source_snapshot_refs(sources)
    names: dict[str, str] = {}
    for source in sources:
        snapshot = scene_snapshot(source)
        if snapshot is None:
            continue
        projection = getattr(snapshot, "routing_projection", {}) or {}
        name = projection.get("name") if isinstance(projection, dict) else None
        if name:
            names[str(snapshot.scene_id)] = str(name)
    for item in refs:
        scene_name = names.get(item["scene_id"])
        if scene_name:
            item["scene_name"] = scene_name
    return refs


def _source_manifest_payload(revision, principal: AskDataPrincipal) -> dict[str, Any]:
    return {
        "ontology_id": revision.ontology_id,
        "revision": revision.revision,
        "source_type": "active_scenes",
        "sources": revision.source_scene_snapshots,
        "active_sources": _source_scene_display_refs(principal),
    }


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
            "source_scenes": _source_scene_display_refs(principal),
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


@router.post("/compile")
def compile_ontology(
    payload: OntologyPreviewRequest,
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    return compile_preview(payload, service, principal)


@router.post("/revisions/{revision}/validate")
def validate_revision(
    revision: int,
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        result = service.validate(revision, _authorized_scene_sources(principal))
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
            revision, _authorized_scene_sources(principal)
        )
        return {"status": "succeeded", "data": _snapshot_payload(snapshot)}
    except OntologyServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/publish")
def publish_snapshot(
    payload: OntologyPublishRequest,
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        snapshot = service.build_snapshot(
            payload.revision, _authorized_scene_sources(principal)
        )
        active = service.activate(snapshot.snapshot_id)
        return {"status": "succeeded", "data": _snapshot_payload(active)}
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
                scene_snapshots=_authorized_scene_sources(principal),
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
        "data": {
            "items": [_snapshot_payload(item) for item in service.list_snapshots()]
        },
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
            "data": service.generate_from_active_scenes(
                scene_snapshots=_authorized_scene_sources(principal),
                user_id=principal.user_id,
                expected_revision=revision,
            ),
        }
    except OntologyServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/generate-from-scenes")
async def generate_from_scenes(
    payload: OntologyDraftFromScenesRequest,
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    return await draft_from_active_scenes(payload, service, principal)


@router.post("/draft/from-active-scenes")
async def draft_from_active_scenes(
    payload: OntologyDraftFromScenesRequest,
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    _require_admin(principal)
    try:
        result = await service.generate_from_active_scenes_async(
            scene_snapshots=_authorized_scene_sources(principal),
            user_id=principal.user_id,
            expected_revision=payload.expected_revision,
            apply=payload.apply,
            mode=payload.mode,
        )
        return {
            "status": "succeeded",
            "data": {
                "pending_count": result["pending_count"],
                "changes": result["changes"],
                "generation": result.get("generation"),
                "revision": _revision_payload(result["revision"]),
            },
        }
    except OntologyServiceError as exc:
        raise _http_error(exc) from exc


@router.get("/draft/source-manifest")
def draft_source_manifest(
    service: OntologyLifecycleService = Depends(get_ontology_service),
    principal: AskDataPrincipal = Depends(get_principal),
):
    revision = service.draft(user_id=principal.user_id)
    return {
        "status": "succeeded",
        "data": _source_manifest_payload(revision, principal),
    }


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
        "data": {"items": _source_scene_display_refs(principal)},
    }


@router.get("/snapshot/latest")
def latest_snapshot(
    service: OntologyLifecycleService = Depends(get_ontology_service),
):
    snapshot = service.active_snapshot()
    return {
        "status": "succeeded",
        "data": _snapshot_payload(snapshot) if snapshot else None,
    }


__all__ = ["configure_ontology_service", "get_ontology_service", "router"]
