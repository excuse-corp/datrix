from __future__ import annotations

import pytest

from dbgpt_app.scene.ask_data.scene.markdown import SemanticMarkdownParser
from dbgpt_app.scene.ask_data.schemas.semantic import SemanticMarkdownError

DOCUMENT = """---
schema_version: "1"
scene_id: contracts
name: Contract analysis
data_source: ecology
view: dbo.vw_contracts
dimensions:
  - key: department
    field: department_name
    filter_operators: [eq, in]
metrics:
  - key: contract_amount
    field: contract_amount
    aggregation: sum
time:
  field: signed_date
  required: false
---

# Contract analysis

Contract amount is measured in yuan.
"""


def test_parser_normalizes_and_hashes_semantic_document():
    parsed = SemanticMarkdownParser().parse(DOCUMENT)

    assert parsed.config.scene_id == "contracts"
    assert parsed.config.metrics[0].aggregation == "sum"
    assert parsed.body_markdown.startswith("# Contract analysis")
    assert parsed.semantic_hash.startswith("sha256:")


def test_parser_hash_is_stable_for_line_endings():
    parser = SemanticMarkdownParser()
    assert (
        parser.parse(DOCUMENT).semantic_hash
        == parser.parse(DOCUMENT.replace("\n", "\r\n")).semantic_hash
    )


def test_parser_rejects_duplicate_keys():
    with pytest.raises(SemanticMarkdownError) as raised:
        SemanticMarkdownParser().parse(
            DOCUMENT.replace("name: Contract analysis", "name: first\nname: second")
        )

    assert raised.value.code == "DUPLICATE_YAML_KEY"


def test_parser_rejects_executable_configuration():
    with pytest.raises(SemanticMarkdownError) as raised:
        SemanticMarkdownParser().parse(
            DOCUMENT.replace("dimensions:", "sql: SELECT 1\ndimensions:")
        )

    assert raised.value.code == "FORBIDDEN_CONFIG_KEY"


def test_parser_rejects_unsupported_schema_version():
    with pytest.raises(SemanticMarkdownError) as raised:
        SemanticMarkdownParser().parse(
            DOCUMENT.replace('schema_version: "1"', 'schema_version: "2"')
        )

    assert raised.value.code == "UNSUPPORTED_SCHEMA_VERSION"
