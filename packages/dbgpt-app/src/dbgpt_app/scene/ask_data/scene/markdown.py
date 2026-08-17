"""Parse and normalize semantic Markdown documents."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import yaml
from pydantic import ValidationError

from ..schemas.semantic import (
    ParsedSemanticDocument,
    SemanticConfig,
    SemanticMarkdownError,
)


class _UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate mapping keys."""


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep=False):
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise SemanticMarkdownError(
                "DUPLICATE_YAML_KEY", f"Duplicate YAML key: {key}", str(key)
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)

_FRONT_MATTER = re.compile(
    r"\A(?:\ufeff)?---[ \t]*\r?\n(?P<yaml>.*?)(?:\r?\n)"
    r"(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)
DATA_DICTIONARY_MARKER = "<!-- dataman:document=data-dictionary -->"
BUSINESS_SEMANTICS_MARKER = "<!-- dataman:document=business-semantics -->"
_FORBIDDEN_KEYS = {
    "sql",
    "raw_sql",
    "raw_query",
    "python",
    "shell",
    "html",
    "code",
    "executable",
    "raw_table",
    "raw_tables",
    "table_allowlist",
    "connection_string",
    "password",
}


def _find_forbidden(value: Any, path: str = "") -> tuple[str, str] | None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_text.lower() in _FORBIDDEN_KEYS:
                return child_path, key_text
            found = _find_forbidden(child, child_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_forbidden(child, f"{path}[{index}]")
            if found:
                return found
    return None


class SemanticMarkdownParser:
    """Turn a semantic Markdown document into a stable protocol object."""

    parser_version = "1"

    def parse(self, document: str) -> ParsedSemanticDocument:
        if not isinstance(document, str) or not document.strip():
            raise SemanticMarkdownError("EMPTY_DOCUMENT", "Semantic Markdown is empty")

        normalized_document = document.replace("\r\n", "\n").replace("\r", "\n")
        match = _FRONT_MATTER.match(normalized_document)
        if not match:
            raise SemanticMarkdownError(
                "MISSING_FRONT_MATTER",
                "Semantic Markdown must start with a YAML front matter block",
            )

        yaml_text = match.group("yaml")
        body = normalized_document[match.end() :]
        try:
            config_data = yaml.load(yaml_text, Loader=_UniqueKeyLoader)
        except SemanticMarkdownError:
            raise
        except yaml.YAMLError as exc:
            raise SemanticMarkdownError("INVALID_YAML", str(exc)) from exc

        if not isinstance(config_data, dict):
            raise SemanticMarkdownError(
                "INVALID_YAML_ROOT", "YAML front matter must be a mapping"
            )
        if config_data.get("schema_version", "1") != "1":
            raise SemanticMarkdownError(
                "UNSUPPORTED_SCHEMA_VERSION",
                "Only semantic schema version 1 is supported",
                "schema_version",
            )
        forbidden = _find_forbidden(config_data)
        if forbidden:
            path, key = forbidden
            raise SemanticMarkdownError(
                "FORBIDDEN_CONFIG_KEY",
                f"Configuration key '{key}' is not executable configuration",
                path,
            )

        try:
            config = SemanticConfig.model_validate(config_data)
        except ValidationError as exc:
            error = exc.errors()[0]
            path = ".".join(str(part) for part in error.get("loc", ()))
            raise SemanticMarkdownError(
                "INVALID_SEMANTIC_CONFIG", error["msg"], path
            ) from exc

        normalized_body = body.strip() + ("\n" if body.strip() else "")
        canonical = {
            "parser_version": self.parser_version,
            "config": config.model_dump(mode="json", exclude_none=True),
            "body": normalized_body,
        }
        digest = hashlib.sha256(
            json.dumps(
                canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        return ParsedSemanticDocument(
            config=config,
            body_markdown=normalized_body,
            semantic_hash=f"sha256:{digest}",
        )


def split_scene_documents(body_markdown: str) -> dict[str, str]:
    """Split the versioned Markdown body into the two full scene documents."""
    body = body_markdown.strip()
    dictionary_start = body.find(DATA_DICTIONARY_MARKER)
    semantics_start = body.find(BUSINESS_SEMANTICS_MARKER)
    if dictionary_start < 0 or semantics_start <= dictionary_start:
        return {
            "data_dictionary_md": "",
            "business_semantics_md": body,
        }

    dictionary = body[
        dictionary_start + len(DATA_DICTIONARY_MARKER) : semantics_start
    ].strip()
    semantics = body[semantics_start + len(BUSINESS_SEMANTICS_MARKER) :].strip()
    dictionary = re.sub(r"\A#\s+(?:视图)?数据字典\s*", "", dictionary).strip()
    semantics = re.sub(r"\A#\s+业务语义说明\s*", "", semantics).strip()
    return {
        "data_dictionary_md": dictionary,
        "business_semantics_md": semantics,
    }
