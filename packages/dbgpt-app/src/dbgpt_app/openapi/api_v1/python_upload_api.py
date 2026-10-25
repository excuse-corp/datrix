import logging
import hashlib
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, UploadFile

from dbgpt._private.config import Config
from dbgpt_app.openapi.api_view_model import Result
from dbgpt_app.openapi.api_v1.upload_limits import (
    MAX_CHAT_UPLOAD_FILES,
    MAX_CHAT_UPLOAD_FILE_SIZE_BYTES,
)
from dbgpt_app.attachment import AttachmentUpload
from dbgpt_app.attachment.manifest import record_upload
from dbgpt._private.pydantic import BaseModel
from dbgpt_serve.utils.auth import UserRequest, get_user_from_headers

router = APIRouter()
CFG = Config()
logger = logging.getLogger(__name__)


class FileUploadResponse(BaseModel):
    """Upload response with legacy paths and stable attachment metadata."""

    success: bool
    err_code: Optional[str] = None
    err_msg: Optional[str] = None
    data: Any = None
    host_name: Optional[str] = None
    attachment: Optional[Dict[str, Any]] = None
    attachments: List[Dict[str, Any]] = []
    file_path: Optional[str] = None
    file_paths: List[str] = []

    @classmethod
    def succ(
        cls,
        data: Any,
        *,
        attachments: List[Dict[str, Any]],
    ) -> "FileUploadResponse":
        paths = [str(item["storage_path"]) for item in attachments]
        return cls(
            success=True,
            data=data,
            attachment=attachments[0] if len(attachments) == 1 else None,
            attachments=attachments,
            file_path=paths[0] if paths else None,
            file_paths=paths,
        )

    @classmethod
    def failed(cls, msg: str) -> "FileUploadResponse":
        return cls(success=False, err_code="E000X", err_msg=msg)


def _sha256(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _attachment_metadata(file_path: str, file: UploadFile) -> Dict[str, Any]:
    path = Path(file_path)
    mime_type = file.content_type or mimetypes.guess_type(path.name)[0]
    suffix = path.suffix.lower().lstrip(".")
    format_name = {"markdown": "md", "htm": "html"}.get(suffix, suffix)
    if format_name not in {
        "pdf",
        "docx",
        "pptx",
        "doc",
        "txt",
        "md",
        "html",
    }:
        format_name = None
    return AttachmentUpload(
        attachment_id=f"att_{uuid.uuid4().hex}",
        filename=path.name,
        size_bytes=path.stat().st_size,
        mime_type=mime_type,
        sha256=_sha256(file_path),
        storage_path=os.path.abspath(file_path),
        format=format_name,
    ).to_dict()


def _record_attachment(upload_dir: str, file_path: str, file: UploadFile) -> Dict[str, Any]:
    metadata = _attachment_metadata(file_path, file)
    return record_upload(upload_dir, metadata)


def _resolve_upload_path(upload_dir: str, filename: str) -> str:
    upload_dir_path = Path(upload_dir).resolve()
    filename_path = Path(filename)
    if filename_path.is_absolute():
        raise ValueError("filename must be a relative path inside upload directory")
    if filename_path.parent == Path(".") and filename_path.name == ".attachments.json":
        raise ValueError("filename is reserved for attachment metadata")

    file_path = (upload_dir_path / filename_path).resolve()
    try:
        file_path.relative_to(upload_dir_path)
    except ValueError as exc:
        raise ValueError("filename must stay inside upload directory") from exc
    return str(file_path)


async def _save_uploaded_file(file: UploadFile, upload_dir: str) -> str:
    if not file or not file.filename:
        raise ValueError("No file provided or filename is empty")

    content = await file.read(MAX_CHAT_UPLOAD_FILE_SIZE_BYTES + 1)
    if not content:
        raise ValueError("Uploaded file is empty")
    if len(content) > MAX_CHAT_UPLOAD_FILE_SIZE_BYTES:
        raise ValueError("Each file must be 30 MB or smaller")

    file_path = _resolve_upload_path(upload_dir, file.filename)
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "wb") as buffer:
        buffer.write(content)
    return os.path.abspath(file_path)


def _user_upload_dir(user_id: str) -> str:
    base_dir = os.getcwd()
    if (
        CFG.SYSTEM_APP
        and hasattr(CFG.SYSTEM_APP, "work_dir")
        and CFG.SYSTEM_APP.work_dir
    ):
        base_dir = CFG.SYSTEM_APP.work_dir
    user_component = str(user_id or "default")
    if (
        user_component in {".", ".."}
        or Path(user_component).name != user_component
        or "/" in user_component
        or "\\" in user_component
    ):
        raise ValueError("Invalid user upload scope")
    base_path = Path(base_dir).expanduser().resolve()
    upload_base_candidate = base_path / "python_uploads"
    if upload_base_candidate.is_symlink():
        raise ValueError("Upload directory cannot be a symbolic link")
    upload_base_candidate.mkdir(parents=True, exist_ok=True)
    upload_base = upload_base_candidate.resolve()
    try:
        upload_base.relative_to(base_path)
    except ValueError as exc:
        raise ValueError("Upload directory cannot escape the application root") from exc
    user_dir_candidate = upload_base / user_component
    if user_dir_candidate.is_symlink():
        raise ValueError("User upload directory cannot be a symbolic link")
    upload_dir_path = user_dir_candidate.resolve()
    try:
        upload_dir_path.relative_to(upload_base)
    except ValueError as exc:
        raise ValueError("Invalid user upload scope") from exc
    upload_dir = str(upload_dir_path)
    os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


@router.post("/v1/python/file/upload", response_model=FileUploadResponse)
async def python_file_upload(
    file: UploadFile = File(...),
    user_token: UserRequest = Depends(get_user_from_headers),
):
    try:
        if not file or not file.filename:
            return FileUploadResponse.failed("No file provided or filename is empty")

        user_id = user_token.user_id or "default"
        logger.info(
            f"Uploading file: {file.filename}, content_type: {file.content_type}, "
            f"user: {user_id}"
        )

        abs_path = await _save_uploaded_file(file, _user_upload_dir(user_id))
        logger.info("File uploaded successfully to %s", abs_path)
        metadata = _record_attachment(_user_upload_dir(user_id), abs_path, file)
        return FileUploadResponse.succ(
            abs_path,
            attachments=[metadata],
        )
    except Exception as e:
        logger.exception(f"File upload failed: {e}")
        return FileUploadResponse.failed(f"Upload error: {str(e)}")


@router.post("/v1/python/files/upload", response_model=FileUploadResponse)
async def python_files_upload(
    files: List[UploadFile] = File(...),
    user_token: UserRequest = Depends(get_user_from_headers),
):
    try:
        if not files:
            return FileUploadResponse.failed("No files provided")
        if len(files) > MAX_CHAT_UPLOAD_FILES:
            return FileUploadResponse.failed("You can upload at most 5 files at a time")

        user_id = user_token.user_id or "default"
        upload_dir = _user_upload_dir(user_id)
        uploaded_paths = []
        try:
            for file in files:
                uploaded_paths.append(await _save_uploaded_file(file, upload_dir))
        except Exception:
            for uploaded_path in uploaded_paths:
                try:
                    os.remove(uploaded_path)
                except OSError:
                    pass
            raise

        logger.info("Uploaded %s files for user %s", len(uploaded_paths), user_id)
        attachments = [
            _record_attachment(upload_dir, path, file)
            for path, file in zip(uploaded_paths, files)
        ]
        return FileUploadResponse.succ(uploaded_paths, attachments=attachments)
    except Exception as e:
        logger.exception("File upload failed: %s", e)
        return FileUploadResponse.failed(f"Upload error: {str(e)}")
