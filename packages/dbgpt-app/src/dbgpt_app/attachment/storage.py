"""Durable per-user attachment chunk storage and lexical retrieval."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from dbgpt.configs.model_config import resolve_root_path

from .chunking import chunk_parse_result
from .models import AttachmentParseResult

_TOKEN_PATTERN = re.compile(r"\w+|[^\W_]", re.UNICODE)


class AttachmentChunkStore:
    """Store parsed chunks in SQLite and search them with FTS5 when available."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        default_dir = resolve_root_path("pilot/meta_data/attachment_index")
        self.storage_dir = Path(storage_dir or default_dir).expanduser().resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.storage_dir.chmod(0o700)
        except OSError:
            pass
        self.database_path = self.storage_dir / "chunks.sqlite3"
        self._lock = threading.RLock()
        self._fts_enabled = False
        self._initialize_database()

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
            connection.execute(
                """CREATE TABLE IF NOT EXISTS attachment_chunks (
                    user_id TEXT NOT NULL,
                    attachment_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    format TEXT NOT NULL,
                    block_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    block_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    location_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, attachment_id, chunk_id)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_attachment_chunks_scope "
                "ON attachment_chunks(user_id, attachment_id)"
            )
            try:
                connection.execute(
                    """CREATE VIRTUAL TABLE IF NOT EXISTS attachment_chunks_fts
                    USING fts5(chunk_id UNINDEXED, user_id UNINDEXED,
                    attachment_id UNINDEXED, text, tokenize='unicode61')"""
                )
                self._fts_enabled = True
            except sqlite3.OperationalError:
                self._fts_enabled = False

    def replace_result(
        self,
        user_id: str,
        result: AttachmentParseResult,
        *,
        chunk_size: int = 1200,
        chunk_overlap: int = 150,
    ) -> int:
        """Replace one attachment's chunks and return the inserted count."""

        chunks = chunk_parse_result(
            result, chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        owner = str(user_id or "default")
        now = time.time()
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "DELETE FROM attachment_chunks WHERE user_id = ? AND attachment_id = ?",
                    (owner, result.attachment_id),
                )
                if self._fts_enabled:
                    connection.execute(
                        "DELETE FROM attachment_chunks_fts WHERE user_id = ? AND attachment_id = ?",
                        (owner, result.attachment_id),
                    )
                for chunk in chunks:
                    connection.execute(
                        """INSERT INTO attachment_chunks
                        (user_id, attachment_id, filename, format, block_id,
                         chunk_id, chunk_index, block_type, text, location_json, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            owner,
                            result.attachment_id,
                            result.filename,
                            result.format,
                            chunk["block_id"],
                            chunk["chunk_id"],
                            chunk["chunk_index"],
                            chunk["type"],
                            chunk["text"],
                            json.dumps(chunk["location"], ensure_ascii=False),
                            now,
                        ),
                    )
                    if self._fts_enabled:
                        connection.execute(
                            "INSERT INTO attachment_chunks_fts "
                            "(chunk_id, user_id, attachment_id, text) VALUES (?, ?, ?, ?)",
                            (
                                chunk["chunk_id"],
                                owner,
                                result.attachment_id,
                                chunk["text"],
                            ),
                        )
                connection.commit()
        return len(chunks)

    def replace_results(
        self, user_id: str, results: Iterable[AttachmentParseResult]
    ) -> int:
        count = 0
        for result in results:
            inserted = self.replace_result(user_id, result)
            if result.status in {"completed", "partial"}:
                count += inserted
        return count

    def search(
        self,
        user_id: str,
        query: str,
        *,
        attachment_ids: Sequence[str] | None = None,
        top_k: int = 6,
        include_tables: bool = True,
    ) -> List[Dict[str, Any]]:
        """Search chunks in one user's scope using BM25 or bounded lexical fallback."""

        tokens = [token.lower() for token in _TOKEN_PATTERN.findall(query or "")]
        if not tokens:
            return []
        owner = str(user_id or "default")
        requested_ids = [str(value) for value in attachment_ids or [] if value]
        limit = max(1, min(int(top_k), 50))
        with self._connect() as connection:
            rows = self._search_fts(
                connection, owner, tokens, requested_ids, limit, include_tables
            )
            if not rows:
                rows = self._search_lexical(
                    connection, owner, tokens, requested_ids, include_tables
                )
        matches = []
        for row, score in rows:
            matches.append(
                {
                    "chunk_id": row["chunk_id"],
                    "block_id": row["block_id"],
                    "attachment_id": row["attachment_id"],
                    "filename": row["filename"],
                    "score": round(float(score), 6),
                    "location": self._decode_location(row["location_json"]),
                    "type": row["block_type"],
                    "text": row["text"][:2000],
                }
            )
        matches.sort(key=lambda item: item["score"], reverse=True)
        return matches[:limit]

    def _search_fts(
        self,
        connection: sqlite3.Connection,
        owner: str,
        tokens: Sequence[str],
        attachment_ids: Sequence[str],
        limit: int,
        include_tables: bool,
    ) -> List[tuple[sqlite3.Row, float]]:
        if not self._fts_enabled:
            return []
        match_query = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
        conditions = ["fts.user_id = ?", "attachment_chunks_fts MATCH ?"]
        parameters: List[Any] = [owner, match_query]
        if attachment_ids:
            placeholders = ",".join("?" for _ in attachment_ids)
            conditions.append(f"chunks.attachment_id IN ({placeholders})")
            parameters.extend(attachment_ids)
        if not include_tables:
            conditions.append("chunks.block_type != 'table'")
        query = (
            "SELECT chunks.*, -bm25(attachment_chunks_fts) AS relevance "
            "FROM attachment_chunks_fts AS fts "
            "JOIN attachment_chunks AS chunks ON chunks.user_id = fts.user_id "
            "AND chunks.attachment_id = fts.attachment_id "
            "AND chunks.chunk_id = fts.chunk_id "
            f"WHERE {' AND '.join(conditions)} ORDER BY relevance DESC LIMIT ?"
        )
        parameters.append(limit)
        try:
            return [
                (row, float(row["relevance"]))
                for row in connection.execute(query, parameters).fetchall()
            ]
        except sqlite3.Error:
            return []

    @staticmethod
    def _search_lexical(
        connection: sqlite3.Connection,
        owner: str,
        tokens: Sequence[str],
        attachment_ids: Sequence[str],
        include_tables: bool,
    ) -> List[tuple[sqlite3.Row, float]]:
        conditions = ["user_id = ?"]
        parameters: List[Any] = [owner]
        if attachment_ids:
            placeholders = ",".join("?" for _ in attachment_ids)
            conditions.append(f"attachment_id IN ({placeholders})")
            parameters.extend(attachment_ids)
        if not include_tables:
            conditions.append("block_type != 'table'")
        rows = connection.execute(
            f"SELECT * FROM attachment_chunks WHERE {' AND '.join(conditions)}",
            parameters,
        ).fetchall()
        matches = []
        for row in rows:
            lowered = row["text"].lower()
            score = sum(lowered.count(token) for token in tokens)
            if score:
                matches.append((row, float(score)))
        return matches

    @staticmethod
    def _decode_location(value: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}


_default_store: AttachmentChunkStore | None = None
_default_store_lock = threading.Lock()


def get_attachment_chunk_store() -> AttachmentChunkStore:
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = AttachmentChunkStore()
        return _default_store
