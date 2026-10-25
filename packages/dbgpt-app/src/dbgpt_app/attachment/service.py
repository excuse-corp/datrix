"""Safe, persistent attachment parsing service."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import signal
import subprocess
import sys
import time
import threading
import uuid
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from dbgpt.configs.model_config import resolve_root_path

from .models import AttachmentParseResult, ParseOptions
from .parsers import (
    DocParser,
    DocxParser,
    ExcelParser,
    HtmlParser,
    MarkdownParser,
    PdfParser,
    PptxParser,
    TextParser,
)
from .registry import UnsupportedAttachmentFormat, detect_format

MAX_ATTACHMENT_FILES = 5
MAX_ATTACHMENT_FILE_SIZE_BYTES = 30 * 1024 * 1024
PARSER_CACHE_VERSION = 3
DEFAULT_PARSER_TIMEOUT_SECONDS = 60
TIME_LIMITED_FORMATS = {"pdf", "doc", "docx", "pptx", "xlsx", "xls"}
PARSER_WORKER_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024


class AttachmentPathError(ValueError):
    """Raised when an attachment path is outside the permitted directory."""


class AttachmentLimitError(ValueError):
    """Raised when an attachment violates a request-level limit."""


class AttachmentParsingCancelled(RuntimeError):
    """Raised when the caller cancels an active attachment parse."""


class AttachmentParseService:
    """Route attachments to parsers and persist normalized parse results."""

    _parser_classes = {
        "pdf": PdfParser,
        "docx": DocxParser,
        "xlsx": ExcelParser,
        "xls": ExcelParser,
        "pptx": PptxParser,
        "doc": DocParser,
        "txt": TextParser,
        "md": MarkdownParser,
        "markdown": MarkdownParser,
        "html": HtmlParser,
    }
    _worker_bootstrap = (
        "import math, resource, runpy; "
        "cpu = int(math.ceil(float(__import__('sys').argv[1]))); "
        "memory = int(__import__('sys').argv[2]); "
        "resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1)); "
        "resource.setrlimit(resource.RLIMIT_AS, (memory, memory)); "
        "resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64)); "
        "runpy.run_module('dbgpt_app.attachment.worker', run_name='__main__')"
    )

    def __init__(
        self,
        *,
        storage_dir: str | Path | None = None,
        max_file_size_bytes: int = MAX_ATTACHMENT_FILE_SIZE_BYTES,
        max_files: int = MAX_ATTACHMENT_FILES,
        parser_timeout_seconds: int = DEFAULT_PARSER_TIMEOUT_SECONDS,
        cache_namespace: str = "default",
    ) -> None:
        default_storage = resolve_root_path("pilot/meta_data/attachments")
        self.storage_dir = Path(storage_dir or default_storage).expanduser().resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.storage_dir.chmod(0o700)
        except OSError:
            pass
        self.max_file_size_bytes = max(1, int(max_file_size_bytes))
        self.max_files = max(1, int(max_files))
        self.parser_timeout_seconds = max(1, min(int(parser_timeout_seconds), 300))
        self._cancel_event = threading.Event()
        self._process_lock = threading.Lock()
        self._active_processes: set[subprocess.Popen] = set()
        self.cache_namespace = hashlib.sha256(
            str(cache_namespace or "default").encode("utf-8")
        ).hexdigest()[:32]

    def parse_file(
        self,
        file_path: str | Path,
        *,
        attachment_id: str | None = None,
        mime_type: str | None = None,
        options: ParseOptions | None = None,
        allowed_roots: Sequence[str | Path] | None = None,
        persist: bool = True,
    ) -> AttachmentParseResult:
        """Parse one file, returning a failed result for parser-level errors."""

        if self._cancel_event.is_set():
            raise AttachmentParsingCancelled("Attachment parsing was cancelled")
        path = self._validate_path(file_path, allowed_roots=allowed_roots)
        size_bytes = path.stat().st_size
        if size_bytes > self.max_file_size_bytes:
            raise AttachmentLimitError(
                f"Attachment exceeds {self.max_file_size_bytes} bytes"
            )

        content_sha256 = self._sha256(path)
        resolved_attachment_id = attachment_id or f"att_{content_sha256[:32]}"
        parse_options = (options or ParseOptions()).normalized()
        normalized_mime = mime_type or mimetypes.guess_type(path.name)[0]
        cache_path = self._cache_path(
            resolved_attachment_id,
            parse_options,
            filename=path.name,
            mime_type=normalized_mime,
        )
        if persist:
            cached = self._load_cache(cache_path, content_sha256)
            if cached is not None:
                return cached

        format_name = "unknown"
        try:
            format_name = detect_format(str(path), normalized_mime)
            parser_class = self._parser_classes.get(format_name)
            if parser_class is None:
                raise UnsupportedAttachmentFormat(
                    f"Unsupported attachment format: {format_name}"
                )
            started = time.monotonic()
            if format_name in TIME_LIMITED_FORMATS:
                result = self._parse_in_worker(
                    format_name,
                    path,
                    resolved_attachment_id,
                    normalized_mime,
                    parse_options,
                )
            else:
                result = parser_class().parse(
                    path,
                    resolved_attachment_id,
                    normalized_mime,
                    parse_options,
                )
            if self._cancel_event.is_set():
                raise AttachmentParsingCancelled("Attachment parsing was cancelled")
            result.metadata.setdefault("content_sha256", content_sha256)
            result.metadata.setdefault(
                "parse_duration_ms", round((time.monotonic() - started) * 1000, 2)
            )
            if not parse_options.extract_metadata:
                result.metadata = {
                    key: value
                    for key, value in result.metadata.items()
                    if key
                    in {
                        "source_name",
                        "size_bytes",
                        "content_sha256",
                        "parse_duration_ms",
                    }
                }
        except UnsupportedAttachmentFormat as exc:
            result = self._failed_result(
                path,
                resolved_attachment_id,
                format_name,
                normalized_mime,
                "unsupported_format",
                str(exc),
            )
        except subprocess.TimeoutExpired as exc:
            result = self._failed_result(
                path,
                resolved_attachment_id,
                format_name,
                normalized_mime,
                "parser_timeout",
                f"Document parsing exceeded {self.parser_timeout_seconds} seconds",
            )
        except AttachmentParsingCancelled:
            raise
        except Exception as exc:
            safe_error = str(exc).replace(str(path), "[attachment]")
            timed_out = isinstance(exc, TimeoutError)
            result = self._failed_result(
                path,
                resolved_attachment_id,
                format_name,
                normalized_mime,
                "parser_timeout" if timed_out else "parse_error",
                (
                    f"Document parsing exceeded {self.parser_timeout_seconds} seconds"
                    if timed_out
                    else f"Unable to parse attachment: {safe_error}"
                ),
            )

        result.warnings = [
            warning.replace(str(path), "[attachment]") for warning in result.warnings
        ]
        if result.error_message:
            result.error_message = result.error_message.replace(
                str(path), "[attachment]"
            )

        result.metadata.setdefault("content_sha256", content_sha256)
        if persist:
            self._save_cache(cache_path, content_sha256, parse_options, result)
        return result

    def parse_files(
        self,
        file_paths: Iterable[str | Path],
        *,
        mime_types: Optional[Sequence[str | None]] = None,
        attachment_ids: Optional[Sequence[str | None]] = None,
        options: ParseOptions | None = None,
        allowed_roots: Sequence[str | Path] | None = None,
        persist: bool = True,
    ) -> List[AttachmentParseResult]:
        """Parse a bounded batch while isolating parser failures per file."""

        paths = list(file_paths)
        if not paths:
            raise AttachmentLimitError("At least one attachment is required")
        if len(paths) > self.max_files:
            raise AttachmentLimitError(
                f"At most {self.max_files} attachments can be parsed at once"
            )
        supplied_mimes = list(mime_types or [])
        supplied_ids = list(attachment_ids or [])
        results: List[AttachmentParseResult] = []
        for index, path in enumerate(paths):
            if self._cancel_event.is_set():
                raise AttachmentParsingCancelled("Attachment parsing was cancelled")
            mime_type = supplied_mimes[index] if index < len(supplied_mimes) else None
            try:
                results.append(
                    self.parse_file(
                        path,
                        attachment_id=(
                            supplied_ids[index]
                            if index < len(supplied_ids) and supplied_ids[index]
                            else None
                        ),
                        mime_type=mime_type,
                        options=options,
                        allowed_roots=allowed_roots,
                        persist=persist,
                    )
                )
            except (AttachmentPathError, AttachmentLimitError) as exc:
                try:
                    raw_path = Path(str(path)).expanduser()
                except (TypeError, ValueError, OSError):
                    raw_path = Path("invalid-attachment")
                results.append(
                    self._failed_result(
                        raw_path,
                        f"att_invalid_{index + 1}",
                        raw_path.suffix.lower().lstrip(".") or "unknown",
                        mime_type or mimetypes.guess_type(raw_path.name)[0],
                        "attachment_rejected",
                        str(exc).replace(str(path), "[attachment]"),
                    )
                )
        return results

    def load_cached_result(self, attachment_id: str) -> AttachmentParseResult | None:
        """Load the newest persisted result for an attachment id."""

        prefix = f"{self.cache_namespace}.{self._safe_cache_key(attachment_id)}."
        candidates = sorted(self.storage_dir.glob(f"{prefix}*.json"), reverse=True)
        for cache_path in candidates:
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                result = payload.get("result")
                if isinstance(result, dict):
                    return AttachmentParseResult.from_dict(result)
            except (OSError, ValueError, TypeError):
                continue
        return None

    def _validate_path(
        self,
        file_path: str | Path,
        *,
        allowed_roots: Sequence[str | Path] | None,
    ) -> Path:
        try:
            raw_path = Path(str(file_path)).expanduser()
        except (TypeError, ValueError, OSError) as exc:
            raise AttachmentPathError("Attachment path is invalid") from exc
        if not raw_path.is_absolute():
            raw_path = Path.cwd() / raw_path
        try:
            path = raw_path.resolve(strict=True)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            raise AttachmentPathError(
                "Attachment file path cannot be resolved"
            ) from exc
        if not path.is_file():
            raise AttachmentPathError("Attachment path is not a regular file")

        if allowed_roots:
            roots = [Path(root).expanduser().resolve() for root in allowed_roots]
            if not any(self._is_relative_to(path, root) for root in roots):
                raise AttachmentPathError(
                    "Attachment path is outside the permitted upload directory"
                )
        return path

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _parse_in_worker(
        self,
        format_name: str,
        path: Path,
        attachment_id: str,
        mime_type: str | None,
        options: ParseOptions,
    ) -> AttachmentParseResult:
        payload = {
            "format": format_name,
            "path": str(path),
            "attachment_id": attachment_id,
            "mime_type": mime_type,
            "options": {
                "extract_tables": options.extract_tables,
                "extract_metadata": options.extract_metadata,
                "max_text_chars": options.max_text_chars,
                "max_blocks": options.max_blocks,
            },
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                self._worker_bootstrap,
                str(self.parser_timeout_seconds),
                str(PARSER_WORKER_MEMORY_LIMIT_BYTES),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        with self._process_lock:
            if self._cancel_event.is_set():
                self._terminate_process(process)
                raise AttachmentParsingCancelled("Attachment parsing was cancelled")
            self._active_processes.add(process)
        try:
            stdout, stderr = process.communicate(
                input=json.dumps(payload, ensure_ascii=False),
                timeout=self.parser_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            self._terminate_process(process)
            process.communicate()
            raise
        finally:
            with self._process_lock:
                self._active_processes.discard(process)
        if process.returncode != 0:
            if self._cancel_event.is_set():
                raise AttachmentParsingCancelled("Attachment parsing was cancelled")
            detail = stderr.strip()[-2000:]
            raise RuntimeError(
                f"Parser worker exited with status {process.returncode}"
                + (f": {detail}" if detail else "")
            )
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Parser worker returned invalid output") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Parser worker returned an invalid result")
        return AttachmentParseResult.from_dict(parsed)

    def cancel_active_parsers(self) -> None:
        """Terminate active isolated parser processes and reject new work."""

        self._cancel_event.set()
        with self._process_lock:
            processes = list(self._active_processes)
        for process in processes:
            self._terminate_process(process)

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, AttributeError, PermissionError):
            if process.poll() is None:
                process.kill()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _cache_path(
        self,
        attachment_id: str,
        options: ParseOptions,
        *,
        filename: str,
        mime_type: str | None,
    ) -> Path:
        option_key = hashlib.sha256(
            json.dumps(
                {
                    "extract_tables": options.extract_tables,
                    "extract_metadata": options.extract_metadata,
                    "max_text_chars": options.max_text_chars,
                    "max_blocks": options.max_blocks,
                    "filename": filename,
                    "mime_type": mime_type,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]
        return (
            self.storage_dir
            / f"{self.cache_namespace}.{self._safe_cache_key(attachment_id)}."
            f"{option_key}.json"
        )

    @staticmethod
    def _safe_cache_key(value: str) -> str:
        return "".join(
            char if char.isalnum() or char in "_-" else "_" for char in value
        )

    @staticmethod
    def _load_cache(path: Path, content_sha256: str) -> AttachmentParseResult | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("cache_version") != PARSER_CACHE_VERSION:
                return None
            if payload.get("content_sha256") != content_sha256:
                return None
            result = payload.get("result")
            if isinstance(result, dict):
                return AttachmentParseResult.from_dict(result)
        except (OSError, ValueError, TypeError):
            return None
        return None

    @staticmethod
    def _save_cache(
        path: Path,
        content_sha256: str,
        options: ParseOptions,
        result: AttachmentParseResult,
    ) -> None:
        payload = {
            "cache_version": PARSER_CACHE_VERSION,
            "content_sha256": content_sha256,
            "options": {
                "extract_tables": options.extract_tables,
                "extract_metadata": options.extract_metadata,
                "max_text_chars": options.max_text_chars,
                "max_blocks": options.max_blocks,
            },
            "result": result.to_dict(include_text=True),
        }
        temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            temp_path.chmod(0o600)
            os.replace(temp_path, path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _failed_result(
        path: Path,
        attachment_id: str,
        format_name: str,
        mime_type: str | None,
        error_code: str,
        error_message: str,
    ) -> AttachmentParseResult:
        safe_error_message = error_message.replace(str(path), "[attachment]")
        try:
            size_bytes = path.stat().st_size if path.is_file() else None
        except OSError:
            size_bytes = None
        return AttachmentParseResult(
            attachment_id=attachment_id,
            filename=path.name,
            format=format_name,
            mime_type=mime_type,
            status="failed",
            parser="",
            metadata={
                "source_name": path.name,
                "size_bytes": size_bytes,
            },
            warnings=[safe_error_message],
            error_code=error_code,
            error_message=safe_error_message,
        )
