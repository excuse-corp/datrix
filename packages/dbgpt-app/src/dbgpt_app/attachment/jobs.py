"""Transactional, durable background jobs for attachment parsing."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from dbgpt.configs.model_config import resolve_root_path

from .models import AttachmentParseResult, ParseOptions
from .service import AttachmentLimitError, AttachmentParseService, AttachmentPathError

DEFAULT_MAX_CONCURRENT_JOBS_PER_USER = 2
DEFAULT_MAX_RETRIES = 1
LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 20


@dataclass
class AttachmentParseJob:
    job_id: str
    user_id: str
    file_paths: List[str]
    attachment_ids: List[Optional[str]]
    allowed_roots: List[str]
    options: ParseOptions
    parse_key: str = ""
    max_retries: int = DEFAULT_MAX_RETRIES
    status: str = "queued"
    attempt: int = 0
    retry_at: float = 0
    items: List[Dict[str, Any]] = field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    reused: bool = False

    def to_dict(self, *, include_paths: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "job_id": self.job_id,
            "attachment_ids": self.attachment_ids,
            "status": self.status,
            "attempt": self.attempt,
            "max_retries": self.max_retries,
            "items": self.items,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }
        if include_paths:
            payload.update(
                {
                    "user_id": self.user_id,
                    "file_paths": self.file_paths,
                    "allowed_roots": self.allowed_roots,
                    "options": self.options.__dict__,
                    "parse_key": self.parse_key,
                    "retry_at": self.retry_at,
                }
            )
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AttachmentParseJob":
        return cls(
            job_id=str(payload.get("job_id") or ""),
            user_id=str(payload.get("user_id") or ""),
            file_paths=[str(value) for value in payload.get("file_paths") or []],
            attachment_ids=list(payload.get("attachment_ids") or []),
            allowed_roots=[str(value) for value in payload.get("allowed_roots") or []],
            options=ParseOptions(**(payload.get("options") or {})).normalized(),
            parse_key=str(payload.get("parse_key") or ""),
            max_retries=max(0, int(payload.get("max_retries", DEFAULT_MAX_RETRIES))),
            status=str(payload.get("status") or "failed"),
            attempt=max(0, int(payload.get("attempt", 0))),
            retry_at=float(payload.get("retry_at") or 0),
            items=list(payload.get("items") or []),
            error_code=payload.get("error_code"),
            error_message=payload.get("error_message"),
        )


class AttachmentJobLimitError(ValueError):
    """Raised when a user has reached the background job limit."""


class AttachmentJobNotFound(KeyError):
    """Raised when a job is not visible to the requesting user."""


class TransientAttachmentJobError(RuntimeError):
    """Signals a retryable worker or storage failure."""


class AttachmentParseJobManager:
    """Persist queue state in SQLite and coordinate workers across processes."""

    def __init__(
        self,
        *,
        storage_dir: str | Path | None = None,
        max_workers: int = 4,
        max_concurrent_jobs_per_user: int = DEFAULT_MAX_CONCURRENT_JOBS_PER_USER,
        max_retries: int = DEFAULT_MAX_RETRIES,
        parse_batch: Callable[..., List[AttachmentParseResult]] | None = None,
    ) -> None:
        default_dir = resolve_root_path("pilot/meta_data/attachment_jobs")
        self.storage_dir = Path(storage_dir or default_dir).expanduser().resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.storage_dir.chmod(0o700)
        except OSError:
            pass
        self.database_path = self.storage_dir / "jobs.sqlite3"
        self.max_workers = max(1, min(int(max_workers), 16))
        self.max_concurrent_jobs_per_user = max(1, int(max_concurrent_jobs_per_user))
        self.max_retries = max(0, min(int(max_retries), 3))
        self._parse_batch = parse_batch or self._default_parse_batch
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="attachment-parse",
        )
        self._lock = threading.RLock()
        self._futures: Dict[str, Future[Any]] = {}
        self._dispatcher_stop = threading.Event()
        self._initialize_database()
        self._recover_expired_leases()
        self._dispatch_queued()
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop,
            name="attachment-job-dispatcher",
            daemon=True,
        )
        self._dispatcher.start()

    @contextmanager
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS attachment_parse_jobs (
                    job_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    parse_key TEXT NOT NULL,
                    retry_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    lease_until REAL NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_attachment_jobs_user_status "
                "ON attachment_parse_jobs(user_id, status)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_attachment_jobs_dedupe "
                "ON attachment_parse_jobs(user_id, parse_key, updated_at)"
            )

    def submit(
        self,
        *,
        user_id: str,
        file_paths: Sequence[str | Path],
        attachment_ids: Sequence[str | None] | None = None,
        allowed_roots: Sequence[str | Path] | None = None,
        options: ParseOptions | None = None,
        max_retries: int | None = None,
    ) -> AttachmentParseJob:
        paths = [str(path) for path in file_paths]
        if not paths or len(paths) > 5:
            raise AttachmentLimitError(
                "At least one and at most five attachments are required"
            )
        ids = list(attachment_ids or [])
        if ids and len(ids) != len(paths):
            raise AttachmentLimitError("attachment_ids must align with file_paths")
        normalized_ids = [ids[index] if index < len(ids) else None for index in range(len(paths))]
        owner = str(user_id or "default")
        parse_options = (options or ParseOptions()).normalized()
        parse_key = self._parse_key(paths, normalized_ids, parse_options)
        now = time.time()
        job_id = f"parse_job_{uuid.uuid4().hex}"
        job = AttachmentParseJob(
            job_id=job_id,
            user_id=owner,
            file_paths=paths,
            attachment_ids=normalized_ids,
            allowed_roots=[str(root) for root in allowed_roots or []],
            options=parse_options,
            parse_key=parse_key,
            max_retries=(
                self.max_retries
                if max_retries is None
                else max(0, min(int(max_retries), 3))
            ),
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload FROM attachment_parse_jobs "
                "WHERE user_id = ? AND parse_key = ? "
                "AND status IN ('queued','parsing','completed','partial','needs_ocr','unsupported') "
                "ORDER BY updated_at DESC LIMIT 1",
                (owner, parse_key),
            ).fetchone()
            if existing:
                reused = AttachmentParseJob.from_dict(json.loads(existing["payload"]))
                reused.reused = True
                connection.commit()
                return reused
            active = connection.execute(
                "SELECT COUNT(*) FROM attachment_parse_jobs "
                "WHERE user_id = ? AND status IN ('queued','parsing')",
                (owner,),
            ).fetchone()[0]
            if active >= self.max_concurrent_jobs_per_user:
                connection.rollback()
                raise AttachmentJobLimitError(
                    f"At most {self.max_concurrent_jobs_per_user} parse jobs can run per user"
                )
            connection.execute(
                "INSERT INTO attachment_parse_jobs "
                "(job_id,user_id,status,parse_key,retry_at,updated_at,lease_until,payload) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    job.job_id,
                    owner,
                    "queued",
                    parse_key,
                    0,
                    now,
                    0,
                    json.dumps(job.to_dict(include_paths=True), ensure_ascii=False),
                ),
            )
            connection.commit()
        self._dispatch_queued()
        return job

    @staticmethod
    def _parse_key(
        paths: Sequence[str], attachment_ids: Sequence[str | None], options: ParseOptions
    ) -> str:
        file_signatures = []
        for value in paths:
            path = Path(value)
            try:
                stat = path.stat()
                signature = [str(path.resolve()), stat.st_size, stat.st_mtime_ns]
            except OSError:
                signature = [str(path), None, None]
            file_signatures.append(signature)
        data = {
            "files": file_signatures,
            "attachment_ids": attachment_ids,
            "options": options.__dict__,
        }
        return hashlib.sha256(
            json.dumps(data, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def get(self, job_id: str, *, user_id: str) -> AttachmentParseJob:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM attachment_parse_jobs WHERE job_id = ? AND user_id = ?",
                (str(job_id), str(user_id or "default")),
            ).fetchone()
        if row is None:
            raise AttachmentJobNotFound(job_id)
        self._dispatch_queued()
        return AttachmentParseJob.from_dict(json.loads(row["payload"]))

    def _dispatch_queued(self) -> None:
        now = time.time()
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT job_id FROM attachment_parse_jobs "
                    "WHERE status = 'queued' AND retry_at <= ? ORDER BY updated_at LIMIT ?",
                    (now, self.max_workers * 4),
                ).fetchall()
            for row in rows:
                job_id = row["job_id"]
                if job_id not in self._futures:
                    self._futures[job_id] = self._executor.submit(self._run, job_id)

    def _dispatch_loop(self) -> None:
        while not self._dispatcher_stop.wait(1):
            try:
                self._recover_expired_leases()
                self._dispatch_queued()
            except sqlite3.Error:
                continue

    def shutdown(self, *, wait: bool = True) -> None:
        self._dispatcher_stop.set()
        self._executor.shutdown(wait=wait, cancel_futures=False)
        if self._dispatcher.is_alive():
            self._dispatcher.join(timeout=2)

    def _claim(self, job_id: str) -> AttachmentParseJob | None:
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM attachment_parse_jobs WHERE job_id = ? "
                "AND status = 'queued' AND retry_at <= ?",
                (job_id, now),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            job = AttachmentParseJob.from_dict(json.loads(row["payload"]))
            job.status = "parsing"
            job.attempt += 1
            self._save(connection, job, lease_until=now + LEASE_SECONDS)
            connection.commit()
            return job

    def _run(self, job_id: str) -> None:
        heartbeat_stop = threading.Event()
        try:
            job = self._claim(job_id)
            if job is None:
                return
            heartbeat = threading.Thread(
                target=self._heartbeat,
                args=(job_id, heartbeat_stop),
                daemon=True,
            )
            heartbeat.start()
            try:
                results = self._parse_batch(
                    job.file_paths,
                    attachment_ids=job.attachment_ids,
                    options=job.options,
                    allowed_roots=job.allowed_roots,
                    cache_namespace=job.user_id,
                )
            except (TimeoutError, ConnectionError, TransientAttachmentJobError) as exc:
                self._retry_or_fail(job, "transient_worker_error", str(exc))
                return
            except (AttachmentPathError, AttachmentLimitError) as exc:
                job.status = "failed"
                job.error_code = "attachment_rejected"
                job.error_message = str(exc)[:1000]
                self._save_job(job, lease_until=0)
                return
            except Exception as exc:
                job.status = "failed"
                job.error_code = "parse_job_failed"
                job.error_message = str(exc)[:1000]
                self._save_job(job, lease_until=0)
                return

            if not results:
                job.status = "failed"
                job.error_code = "empty_parse_result"
                job.error_message = "Parser returned no attachment results"
            else:
                try:
                    from .storage import get_attachment_chunk_store

                    get_attachment_chunk_store().replace_results(job.user_id, results)
                except Exception as exc:
                    for result in results:
                        if result.status in {"completed", "partial"}:
                            result.warnings.append(
                                f"Search index could not be updated: {type(exc).__name__}"
                            )
                job.items = [self._item_summary(result) for result in results]
                statuses = {result.status for result in results}
                if statuses == {"completed"}:
                    job.status = "completed"
                elif "completed" in statuses:
                    job.status = "partial"
                elif statuses == {"needs_ocr"}:
                    job.status = "needs_ocr"
                elif statuses == {"unsupported"}:
                    job.status = "unsupported"
                else:
                    job.status = "failed"
            self._save_job(job, lease_until=0)
        finally:
            heartbeat_stop.set()
            with self._lock:
                self._futures.pop(job_id, None)
            self._dispatch_queued()

    @staticmethod
    def _default_parse_batch(*args: Any, **kwargs: Any) -> List[AttachmentParseResult]:
        cache_namespace = kwargs.pop("cache_namespace", "attachment-job")
        service = AttachmentParseService(cache_namespace=cache_namespace)
        return service.parse_files(*args, **kwargs)

    def _retry_or_fail(
        self, job: AttachmentParseJob, error_code: str, error_message: str
    ) -> None:
        if job.attempt <= job.max_retries:
            job.status = "queued"
            job.error_code = error_code
            job.error_message = error_message[:1000]
            job.retry_at = time.time() + min(2 ** (job.attempt - 1), 30)
            self._save_job(job, lease_until=0)
            timer = threading.Timer(
                max(0, job.retry_at - time.time()), self._dispatch_queued
            )
            timer.daemon = True
            timer.start()
        else:
            job.status = "failed"
            job.error_code = error_code
            job.error_message = error_message[:1000]
            self._save_job(job, lease_until=0)

    def _heartbeat(self, job_id: str, stop: threading.Event) -> None:
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE attachment_parse_jobs SET lease_until = ?, updated_at = ? "
                        "WHERE job_id = ? AND status = 'parsing'",
                        (time.time() + LEASE_SECONDS, time.time(), job_id),
                    )
            except sqlite3.Error:
                continue

    def _recover_expired_leases(self) -> None:
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT payload FROM attachment_parse_jobs "
                "WHERE status = 'parsing' AND lease_until < ?",
                (now,),
            ).fetchall()
            for row in rows:
                job = AttachmentParseJob.from_dict(json.loads(row["payload"]))
                if job.attempt <= job.max_retries:
                    job.status = "queued"
                    job.retry_at = now
                    job.error_code = "worker_recovered"
                    job.error_message = "Recovered after worker lease expired"
                else:
                    job.status = "failed"
                    job.error_code = "worker_retry_exhausted"
                    job.error_message = "Worker lease expired after retry limit"
                self._save(connection, job, lease_until=0)
            connection.commit()

    def _save_job(self, job: AttachmentParseJob, *, lease_until: float) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._save(connection, job, lease_until=lease_until)
            connection.commit()

    @staticmethod
    def _save(
        connection: sqlite3.Connection,
        job: AttachmentParseJob,
        *,
        lease_until: float,
    ) -> None:
        now = time.time()
        connection.execute(
            "UPDATE attachment_parse_jobs SET status = ?, retry_at = ?, updated_at = ?, "
            "lease_until = ?, payload = ? WHERE job_id = ?",
            (
                job.status,
                job.retry_at,
                now,
                lease_until,
                json.dumps(job.to_dict(include_paths=True), ensure_ascii=False),
                job.job_id,
            ),
        )

    @staticmethod
    def _item_summary(result: AttachmentParseResult) -> Dict[str, Any]:
        return {
            "attachment_id": result.attachment_id,
            "filename": result.filename,
            "format": result.format,
            "status": result.status,
            "block_count": len(result.blocks),
            "text_chars": len(result.plain_text),
            "page_count": result.metadata.get("page_count"),
            "slide_count": result.metadata.get("slide_count"),
            "warnings": result.warnings[:10],
            "error_code": result.error_code,
            "cache_hit": bool(result.metadata.get("cache_hit")),
        }


_default_manager: AttachmentParseJobManager | None = None
_default_manager_lock = threading.Lock()


def get_attachment_job_manager() -> AttachmentParseJobManager:
    global _default_manager
    with _default_manager_lock:
        if _default_manager is None:
            _default_manager = AttachmentParseJobManager()
        return _default_manager
