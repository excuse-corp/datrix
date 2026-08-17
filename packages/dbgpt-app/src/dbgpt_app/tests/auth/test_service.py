from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dbgpt_app.auth.service import (
    AuthConfig,
    ProviderConfig,
    _cas_attributes,
    configure_authentication,
)


@pytest.fixture
def auth_app(tmp_path):
    app = FastAPI()
    store = configure_authentication(
        app,
        database_url=f"sqlite:///{tmp_path / 'auth.db'}",
        secret="a" * 48,
        ttl_seconds=3600,
        required=True,
        cookie_secure=False,
    )
    store.create_user(
        "alice",
        "correct horse battery staple",
        roles=("normal",),
        scene_ids=("contracts",),
    )
    return app


def test_login_and_me_expose_authenticated_user(auth_app):
    client = TestClient(auth_app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["user_id"] == response.json()["user"]["user_id"]
    assert me.json()["roles"] == ["normal"]


def test_invalid_credentials_and_missing_auth_are_rejected(auth_app):
    client = TestClient(auth_app)
    assert client.get("/api/v1/auth/me").status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "wrong"},
        ).status_code
        == 401
    )


def test_expired_token_is_rejected(auth_app):
    auth_app.state.auth_config.ttl_seconds = -1
    client = TestClient(auth_app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    assert client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {response.json()['access_token']}"},
    ).status_code == 401
    auth_app.state.auth_config.ttl_seconds = 3600


def test_auth_secret_requires_sufficient_entropy():
    app = FastAPI()
    with pytest.raises(ValueError, match="at least 32"):
        configure_authentication(app, database_url="sqlite:///:memory:", secret="short")


def test_refresh_rotates_the_session(auth_app):
    client = TestClient(auth_app)
    login = client.post(
        "/api/v1/auth/local/login",
        json={"username": "alice", "password": "correct horse battery staple"},
    )
    assert login.status_code == 200

    refresh = client.post("/api/v1/auth/refresh")
    assert refresh.status_code == 200
    assert refresh.json()["access_token"] != login.json()["access_token"]
    assert refresh.json()["refresh_token"] != login.json()["refresh_token"]


def test_external_identity_is_bound_to_one_internal_user(auth_app):
    store = auth_app.state.auth_store
    config = AuthConfig("a" * 48)
    provider = ProviderConfig(
        provider_id="school-cas",
        provider_type="cas",
        display_name="School CAS",
        base_url="https://cas.example.edu",
        subject_candidates=("ID_NUMBER",),
        attribute_mapping={"username": "ID_NUMBER", "display_name": "USER_NAME"},
    )
    attributes = {"username": "20260001", "display_name": "Alice"}

    first = store.resolve_external_identity(provider, "20260001", attributes, config)
    second = store.resolve_external_identity(provider, "20260001", attributes, config)

    assert first is not None
    assert second is not None
    assert first.user_id == second.user_id
    assert first.roles == ("normal",)


def test_cas_xml_requires_configured_subject_attribute():
    provider = ProviderConfig(
        provider_id="school-cas",
        provider_type="cas",
        display_name="School CAS",
        base_url="https://cas.example.edu",
        subject_candidates=("ID_NUMBER",),
        attribute_mapping={"username": "ID_NUMBER", "display_name": "USER_NAME"},
    )
    subject, attributes = _cas_attributes(
        """
        <cas:serviceResponse xmlns:cas="http://www.yale.edu/tp/cas">
          <cas:authenticationSuccess>
            <cas:user>fallback-user</cas:user>
            <cas:attributes><cas:ID_NUMBER>20260001</cas:ID_NUMBER>
            <cas:USER_NAME>Alice</cas:USER_NAME></cas:attributes>
          </cas:authenticationSuccess>
        </cas:serviceResponse>
        """,
        provider,
    )
    assert subject == "20260001"
    assert attributes == {"username": "20260001", "display_name": "Alice"}
