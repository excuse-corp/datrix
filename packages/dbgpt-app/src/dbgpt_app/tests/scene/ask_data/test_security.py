from __future__ import annotations

import pytest

from dbgpt_app.scene.ask_data.security import (
    AskDataAuthorizationError,
    AskDataAuthorizer,
    AskDataPrincipal,
)


def test_scene_principal_access_and_runtime_redaction():
    principal = AskDataPrincipal(user_id="user", scene_ids=frozenset({"contracts"}))
    authorizer = AskDataAuthorizer()
    authorizer.require_scene_access(principal, "contracts")
    redacted = authorizer.redact_runtime(
        principal,
        "contracts",
        {
            "revision_id": "1",
            "schema": {"columns": ["secret"]},
            "compiler_version": "1",
        },
    )
    assert redacted == {"revision_id": "1", "compiler_version": "1"}


def test_non_admin_cannot_perform_admin_action():
    with pytest.raises(AskDataAuthorizationError) as error:
        AskDataAuthorizer().require_admin(AskDataPrincipal(user_id="user"))
    assert error.value.code == "ADMIN_REQUIRED"
