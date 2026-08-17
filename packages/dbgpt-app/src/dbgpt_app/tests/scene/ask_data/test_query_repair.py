from __future__ import annotations

import pytest

from dbgpt_app.scene.ask_data.query import (
    QuerySpec,
    QuerySpecRepair,
    QuerySpecRepairError,
)


def test_query_spec_repair_allows_one_business_field_patch():
    spec = QuerySpec(metrics=["amount"], limit=100)
    repaired = QuerySpecRepair().repair(spec, {"limit": 10}, attempt=0)
    assert repaired.limit == 10


@pytest.mark.parametrize(
    "patch",
    [{"sql": "DROP TABLE x"}, {"view": "other_view"}, {"formula": "amount * 2"}],
)
def test_query_spec_repair_rejects_execution_fields(patch):
    with pytest.raises(QuerySpecRepairError) as error:
        QuerySpecRepair().repair(QuerySpec(metrics=["amount"]), patch, attempt=0)
    assert error.value.args[0] == "QUERY_SPEC_REPAIR_FORBIDDEN_FIELD"


def test_query_spec_repair_has_single_attempt_limit():
    with pytest.raises(QuerySpecRepairError) as error:
        QuerySpecRepair().repair(
            QuerySpec(metrics=["amount"]), {"limit": 10}, attempt=1
        )
    assert error.value.args[0] == "QUERY_SPEC_REPAIR_LIMIT"
