from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

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


def test_ask_data_admin_manages_but_only_accesses_assigned_scenes():
    principal = AskDataPrincipal(
        user_id="manager",
        roles=frozenset({"ask_data_admin"}),
        scene_ids=frozenset({"contracts"}),
    )
    authorizer = AskDataAuthorizer()

    assert principal.can_manage_ask_data
    assert not principal.is_platform_admin
    authorizer.require_admin(principal)
    authorizer.require_scene_access(principal, "contracts")
    with pytest.raises(AskDataAuthorizationError):
        authorizer.require_scene_access(principal, "projects")


def test_only_platform_admin_has_unrestricted_scene_access():
    principal = AskDataPrincipal(
        user_id="admin",
        roles=frozenset({"normal", "admin"}),
    )

    assert principal.is_platform_admin
    assert principal.can_access_scene("any-scene")


def test_principal_conversion_preserves_all_roles_and_authorization_version():
    from dbgpt_app.scene.ask_data.security import principal_from_user

    principal = principal_from_user(
        {
            "user_id": "alice",
            "roles": ["normal", "ask_data_admin"],
            "scene_ids": ["contracts"],
            "authz_version": 7,
        }
    )

    assert principal.roles == frozenset({"normal", "ask_data_admin"})
    assert principal.scene_ids == frozenset({"contracts"})
    assert principal.authz_version == 7
    assert not principal.can_access_scene("projects")


def test_required_auth_does_not_accept_client_supplied_principal_headers():
    from dbgpt_app.scene.ask_data.api.scenes import get_principal

    app = FastAPI()
    app.state.auth_required = True

    @app.get("/principal")
    def principal(request: Request):
        return get_principal(request).user_id

    response = TestClient(app).get(
        "/principal",
        headers={
            "X-User-Id": "attacker",
            "X-AskData-Role": "admin",
            "X-AskData-Scenes": "secret-scene",
        },
    )

    assert response.status_code == 401


def test_main_chat_does_not_fall_back_to_header_user_when_auth_resolver_is_configured():
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException

    from dbgpt_app.openapi.api_v1.agentic_data_api import chat_react_agent

    app = FastAPI()
    app.state.auth_required = False
    app.state.ask_data_principal_resolver = lambda request: AskDataPrincipal(
        user_id="authenticated"
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/chat/react-agent",
            "headers": [],
            "query_string": b"",
            "app": app,
        }
    )
    dialogue = SimpleNamespace(
        chat_mode="chat", select_param=None, model_name=None, ext_info=None
    )
    untrusted_user_token = SimpleNamespace(user_id="attacker", role="admin")

    with pytest.raises(HTTPException) as error:
        asyncio.run(chat_react_agent(request, dialogue, untrusted_user_token))

    assert error.value.status_code == 401
