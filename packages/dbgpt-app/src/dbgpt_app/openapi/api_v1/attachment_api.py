"""Attachment parsing API for uploaded documents."""

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from dbgpt._private.pydantic import BaseModel as _BaseModel
from dbgpt._private.pydantic import Field
from dbgpt_app.attachment import (
    AttachmentJobLimitError,
    AttachmentJobNotFound,
    AttachmentParseService,
    ParseOptions,
    get_attachment_job_manager,
)
from dbgpt_app.attachment.manifest import find_uploads
from dbgpt_app.attachment.service import AttachmentLimitError
from dbgpt_app.openapi.api_v1.python_upload_api import _user_upload_dir
from dbgpt_app.openapi.api_view_model import Result
from dbgpt_serve.utils.auth import UserRequest, get_user_from_headers

router = APIRouter()


class AttachmentParseOptionsRequest(_BaseModel):
    extract_tables: bool = True
    extract_metadata: bool = True
    max_text_chars: int = Field(default=2_000_000, ge=1, le=2_000_000)
    max_blocks: int = Field(default=50_000, ge=1, le=50_000)


class AttachmentParseRequest(_BaseModel):
    file_paths: Optional[List[str]] = Field(default=None, max_length=5)
    attachment_ids: Optional[List[str]] = Field(default=None, max_length=5)
    async_parse: bool = False
    options: Optional[AttachmentParseOptionsRequest] = None


def _options_from_request(
    request_options: AttachmentParseOptionsRequest | None,
) -> ParseOptions:
    if request_options is None:
        return ParseOptions()
    values = (
        request_options.model_dump()
        if hasattr(request_options, "model_dump")
        else request_options.dict()
    )
    return ParseOptions(**values).normalized()


@router.post("/v1/attachments/parse", response_model=Result[Any])
async def parse_attachments(
    request: AttachmentParseRequest,
    user_token: UserRequest = Depends(get_user_from_headers),
):
    user_id = getattr(user_token, "user_id", None) or "default"
    upload_dir = _user_upload_dir(str(user_id))
    if not request.file_paths and not request.attachment_ids:
        return Result.failed(
            code="ATTACHMENT_INPUT_REQUIRED",
            msg="file_paths or attachment_ids is required",
        )
    file_paths = list(request.file_paths or [])
    attachment_ids = list(request.attachment_ids or [])
    if any(
        not isinstance(file_path, str) or len(file_path) > 4096 or "\x00" in file_path
        for file_path in file_paths
    ):
        return Result.failed(
            code="ATTACHMENT_PATH_INVALID",
            msg="Attachment paths must be valid and no longer than 4096 characters",
        )
    if attachment_ids:
        if file_paths and len(file_paths) != len(attachment_ids):
            return Result.failed(
                code="ATTACHMENT_ID_MISMATCH",
                msg="attachment_ids must align with file_paths",
            )
        if len(attachment_ids) != len(set(attachment_ids)):
            return Result.failed(
                code="ATTACHMENT_ID_DUPLICATE",
                msg="attachment_ids must be unique",
            )
        records = find_uploads(upload_dir, attachment_ids)
        records_by_id = {item.get("attachment_id"): item for item in records}
        if len(records_by_id) != len(set(attachment_ids)):
            return Result.failed(
                code="ATTACHMENT_NOT_FOUND",
                msg="One or more attachment IDs are not available to this user",
            )
        if not file_paths:
            file_paths = [str(records_by_id[item]["storage_path"]) for item in attachment_ids]
        else:
            for index, attachment_id in enumerate(attachment_ids):
                try:
                    expected = Path(
                        str(records_by_id[attachment_id]["storage_path"])
                    ).resolve(strict=False)
                    supplied = Path(file_paths[index]).expanduser().resolve(strict=False)
                except (OSError, ValueError, RuntimeError):
                    return Result.failed(
                        code="ATTACHMENT_PATH_INVALID",
                        msg="Attachment paths must be valid and no longer than 4096 characters",
                    )
                if supplied != expected:
                    return Result.failed(
                        code="ATTACHMENT_ID_PATH_MISMATCH",
                        msg="attachment_ids must match their uploaded file paths",
                    )
    if len(file_paths) != len(attachment_ids) and attachment_ids:
        return Result.failed(
            code="ATTACHMENT_ID_MISMATCH",
            msg="attachment_ids must align with file_paths",
        )
    if request.async_parse:
        try:
            manager = get_attachment_job_manager()
            job = manager.submit(
                user_id=str(user_id),
                file_paths=file_paths,
                attachment_ids=attachment_ids,
                allowed_roots=[upload_dir],
                options=_options_from_request(request.options),
            )
            return Result.succ({"job_id": job.job_id, "status": job.status})
        except AttachmentJobLimitError as exc:
            return Result.failed(code="ATTACHMENT_JOB_LIMIT", msg=str(exc))
        except AttachmentLimitError as exc:
            return Result.failed(code="ATTACHMENT_LIMIT_EXCEEDED", msg=str(exc))
    try:
        service = AttachmentParseService(cache_namespace=str(user_id))
        results = await run_in_threadpool(
            service.parse_files,
            file_paths,
            attachment_ids=attachment_ids,
            options=_options_from_request(request.options),
            allowed_roots=[upload_dir],
        )
    except AttachmentLimitError as exc:
        return Result.failed(code="ATTACHMENT_LIMIT_EXCEEDED", msg=str(exc))
    except Exception as exc:
        return Result.failed(code="ATTACHMENT_PARSE_REQUEST_FAILED", msg=str(exc))
    return Result.succ([result.to_dict() for result in results])


@router.get("/v1/attachments/parse/{job_id}")
async def get_parse_job(
    job_id: str,
    user_token: UserRequest = Depends(get_user_from_headers),
):
    user_id = getattr(user_token, "user_id", None) or "default"
    try:
        job = get_attachment_job_manager().get(job_id, user_id=str(user_id))
    except AttachmentJobNotFound:
        return Result.failed(code="ATTACHMENT_JOB_NOT_FOUND", msg="Parse job not found")
    return Result.succ(job.to_dict())
