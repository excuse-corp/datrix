"""Small per-user manifest for resolving uploaded attachment IDs."""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List

_MANIFEST_NAME = ".attachments.json"
_manifest_lock = threading.RLock()


def _manifest_path(upload_dir: str | Path) -> Path:
    return Path(upload_dir).expanduser().resolve() / _MANIFEST_NAME


def _read(upload_dir: str | Path) -> List[Dict[str, Any]]:
    path = _manifest_path(upload_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def record_upload(upload_dir: str | Path, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Persist upload metadata without exposing it outside the user scope."""

    clean = {
        key: value
        for key, value in metadata.items()
        if key in {
            "attachment_id",
            "filename",
            "size_bytes",
            "mime_type",
            "sha256",
            "storage_path",
            "status",
            "format",
        }
    }
    clean.setdefault("attachment_id", f"att_{uuid.uuid4().hex}")
    with _manifest_lock:
        path = _manifest_path(upload_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [
            item
            for item in _read(upload_dir)
            if item.get("attachment_id") != clean["attachment_id"]
            and item.get("storage_path") != clean.get("storage_path")
        ]
        records.append(clean)
        temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        temp_path.write_text(
            json.dumps(records[-1000:], ensure_ascii=False), encoding="utf-8"
        )
        temp_path.chmod(0o600)
        os.replace(temp_path, path)
    return clean


def find_uploads(
    upload_dir: str | Path, attachment_ids: Iterable[str]
) -> List[Dict[str, Any]]:
    requested = {str(value) for value in attachment_ids if value}
    if not requested:
        return []
    with _manifest_lock:
        records = _read(upload_dir)
    return [item for item in records if item.get("attachment_id") in requested]
