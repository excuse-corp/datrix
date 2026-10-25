"""Format detection and parser registry for uploaded documents."""

import mimetypes
import stat
import zipfile
from pathlib import Path
from typing import Optional

SUPPORTED_FORMATS = {
    "pdf",
    "docx",
    "pptx",
    "doc",
    "xlsx",
    "xls",
    "txt",
    "md",
    "markdown",
    "html",
    "htm",
}

MIME_TO_FORMAT = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/msword": "doc",
    "application/vnd.ms-excel": "xls",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/html": "html",
}

MAX_ZIP_ENTRIES = 5_000
MAX_ZIP_UNCOMPRESSED_BYTES = 200 * 1024 * 1024


class UnsupportedAttachmentFormat(ValueError):
    """Raised when an uploaded file is not a supported document format."""


def _zip_format(path: Path) -> Optional[str]:
    if not zipfile.is_zipfile(path):
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_ENTRIES:
                raise UnsupportedAttachmentFormat(
                    f"ZIP entry limit exceeded: {MAX_ZIP_ENTRIES}"
                )
            total_uncompressed = 0
            names = set()
            for member in members:
                name = member.filename.replace("\\", "/")
                if not name or name.startswith("/") or ".." in Path(name).parts:
                    raise UnsupportedAttachmentFormat(
                        "ZIP archive contains an unsafe member path"
                    )
                if stat.S_ISLNK((member.external_attr >> 16) & 0xFFFF):
                    raise UnsupportedAttachmentFormat(
                        "ZIP archive contains a symbolic link"
                    )
                total_uncompressed += max(0, member.file_size)
                if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                    raise UnsupportedAttachmentFormat(
                        "ZIP uncompressed size limit exceeded"
                    )
                names.add(name)
    except UnsupportedAttachmentFormat:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise UnsupportedAttachmentFormat("Invalid ZIP archive") from exc
    if "word/document.xml" in names:
        return "docx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return None


def detect_format(file_path: str, mime_type: Optional[str] = None) -> str:
    """Detect a supported format using magic bytes, MIME, and extension."""

    path = Path(file_path)
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
    except OSError as exc:
        raise UnsupportedAttachmentFormat(f"Unable to read attachment: {exc}") from exc

    if header.startswith(b"%PDF-"):
        detected = "pdf"
    elif header.startswith(b"\xd0\xcf\x11\xe0"):
        extension = path.suffix.lower().lstrip(".")
        normalized_mime = (
            mime_type or mimetypes.guess_type(str(path))[0] or ""
        ).lower()
        if extension == "xls" or normalized_mime == "application/vnd.ms-excel":
            detected = "xls"
        else:
            detected = "doc"
    else:
        detected = _zip_format(path)

    if detected:
        extension = path.suffix.lower().lstrip(".")
        normalized_extension = {
            "markdown": "md",
            "htm": "html",
        }.get(extension, extension)
        if (
            normalized_extension in SUPPORTED_FORMATS
            and normalized_extension != detected
        ):
            raise UnsupportedAttachmentFormat(
                f"File content does not match the .{extension} extension"
            )
        return detected

    try:
        if zipfile.is_zipfile(path):
            raise UnsupportedAttachmentFormat("ZIP archive is not DOCX or PPTX")
    except OSError:
        pass

    if header.startswith(
        (
            b"\x89PNG\r\n\x1a\n",
            b"\xff\xd8\xff",
            b"GIF87a",
            b"GIF89a",
            b"BM",
            b"\x7fELF",
        )
    ):
        raise UnsupportedAttachmentFormat("Binary files are not supported attachments")

    normalized_mime = (mime_type or mimetypes.guess_type(str(path))[0] or "").lower()
    if normalized_mime in MIME_TO_FORMAT:
        return MIME_TO_FORMAT[normalized_mime]

    extension = path.suffix.lower().lstrip(".")
    if extension in SUPPORTED_FORMATS:
        return (
            "md"
            if extension == "markdown"
            else "html"
            if extension == "htm"
            else extension
        )

    raise UnsupportedAttachmentFormat(
        f"Unsupported attachment format: {path.suffix or normalized_mime or 'unknown'}"
    )
