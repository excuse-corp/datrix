"""Data models shared by attachment parsers and API/tool adapters."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ParseOptions:
    """Limits and feature switches applied during document parsing."""

    extract_tables: bool = True
    extract_metadata: bool = True
    max_text_chars: int = 2_000_000
    max_blocks: int = 50_000

    def normalized(self) -> "ParseOptions":
        """Return options with safe, bounded values."""

        return ParseOptions(
            extract_tables=bool(self.extract_tables),
            extract_metadata=bool(self.extract_metadata),
            max_text_chars=max(1, min(int(self.max_text_chars), 2_000_000)),
            max_blocks=max(1, min(int(self.max_blocks), 50_000)),
        )


@dataclass
class AttachmentUpload:
    """Stable metadata returned when an attachment is stored."""

    attachment_id: str
    filename: str
    size_bytes: int
    mime_type: Optional[str]
    sha256: str
    storage_path: str
    status: str = "uploaded"
    format: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "mime_type": self.mime_type,
            "sha256": self.sha256,
            "storage_path": self.storage_path,
            "status": self.status,
            "format": self.format,
        }


@dataclass
class AttachmentParseResult:
    """Normalized result returned by every supported document parser."""

    attachment_id: str
    filename: str
    format: str
    mime_type: Optional[str]
    status: str = "completed"
    parser: str = ""
    parser_version: str = "1.0.0"
    metadata: Dict[str, Any] = field(default_factory=dict)
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    plain_text: str = ""
    warnings: List[str] = field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AttachmentParseResult":
        """Rehydrate a persisted parse result."""

        return cls(
            attachment_id=str(data.get("attachment_id") or ""),
            filename=str(data.get("filename") or ""),
            format=str(data.get("format") or ""),
            mime_type=data.get("mime_type"),
            status=str(data.get("status") or "failed"),
            parser=str(data.get("parser") or ""),
            parser_version=str(data.get("parser_version") or "1.0.0"),
            metadata=dict(data.get("metadata") or {}),
            blocks=list(data.get("blocks") or []),
            plain_text=str(data.get("plain_text") or ""),
            warnings=list(data.get("warnings") or []),
            error_code=data.get("error_code"),
            error_message=data.get("error_message"),
        )

    def to_dict(self, *, include_text: bool = True) -> Dict[str, Any]:
        """Serialize the result without exposing the absolute source path."""

        data: Dict[str, Any] = {
            "attachment_id": self.attachment_id,
            "filename": self.filename,
            "format": self.format,
            "mime_type": self.mime_type,
            "status": self.status,
            "parser": self.parser,
            "parser_version": self.parser_version,
            "metadata": self.metadata,
            "blocks": self.blocks,
            "warnings": self.warnings,
        }
        if include_text:
            data["plain_text"] = self.plain_text
        if self.error_code:
            data["error_code"] = self.error_code
        if self.error_message:
            data["error_message"] = self.error_message
        return data
