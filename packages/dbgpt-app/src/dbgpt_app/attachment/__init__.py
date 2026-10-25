"""Attachment parsing and retrieval primitives for Datrix."""

from .models import AttachmentParseResult, AttachmentUpload, ParseOptions
from .service import AttachmentParseService
from .jobs import (
    AttachmentJobLimitError,
    AttachmentJobNotFound,
    AttachmentParseJobManager,
    get_attachment_job_manager,
)
from .storage import AttachmentChunkStore, get_attachment_chunk_store

__all__ = [
    "AttachmentParseResult",
    "AttachmentParseService",
    "AttachmentParseJobManager",
    "AttachmentJobLimitError",
    "AttachmentJobNotFound",
    "AttachmentUpload",
    "ParseOptions",
    "get_attachment_job_manager",
    "AttachmentChunkStore",
    "get_attachment_chunk_store",
]
