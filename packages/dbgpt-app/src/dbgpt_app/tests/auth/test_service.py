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
    store.create_user(
        "admin",
        "admin password 123",
        roles=("admin",),
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


def test_setup_creates_first_admin_once_and_logs_in(tmp_path):
    app = FastAPI()
    token = "setup-token-" + "x" * 40
    store = configure_authentication(
        app,
        database_url=f"sqlite:///{tmp_path / 'setup.db'}",
        secret="b" * 48,
        setup_token=token,
        required=True,
        cookie_secure=False,
    )
    client = TestClient(app)

    assert client.get("/api/v1/auth/setup/status").json() == {"initialized": False}
    invalid = client.post(
        "/api/v1/auth/setup",
        json={"token": "wrong-token-value", "username": "owner", "password": "long secure password"},
    )
    assert invalid.status_code == 403

    response = client.post(
        "/api/v1/auth/setup",
        json={"token": token, "username": "owner", "password": "long secure password"},
    )
    assert response.status_code == 200
    assert response.json()["user"]["roles"] == ["admin"]
    assert response.cookies.get("dataman_session")
    assert client.get("/api/v1/auth/setup/status").json() == {"initialized": True}
    assert client.get("/api/v1/auth/me").status_code == 200

    repeated = client.post(
        "/api/v1/auth/setup",
        json={"token": token, "username": "second-owner", "password": "long secure password"},
    )
    assert repeated.status_code == 409
    assert store.list_users(page=1, page_size=10)[1] == 1


def test_auth_middleware_allows_static_pages_with_stale_cookie(tmp_path):
    app = FastAPI()
    app.get("/login")(lambda: {"page": "login"})
    configure_authentication(
        app,
        database_url=f"sqlite:///{tmp_path / 'static.db'}",
        secret="c" * 48,
        required=True,
    )

    response = TestClient(app).get("/login", cookies={"dataman_session": "expired"})
    assert response.status_code == 200


def test_controller_api_requires_internal_token(auth_app):
    auth_app.post("/api/controller/models")(lambda: {"status": "ok"})
    client = TestClient(auth_app)

    assert client.post("/api/controller/models").status_code == 401
    response = client.post(
        "/api/controller/models",
        headers={"X-DataMan-Internal-Token": auth_app.state.auth_internal_token},
    )
    assert response.status_code == 200


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


def test_public_auth_endpoints_ignore_stale_access_cookie(auth_app):
    client = TestClient(auth_app)
    client.cookies.set("dataman_session", "expired")

    login = client.post(
        "/api/v1/auth/local/login",
        json={"username": "alice", "password": "correct horse battery staple"},
    )
    assert login.status_code == 200

    client.cookies.set("dataman_session", "expired")
    refresh = client.post("/api/v1/auth/refresh")
    assert refresh.status_code == 200


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


def test_admin_user_management_requires_admin(auth_app):
    user_client = TestClient(auth_app)
    denied = user_client.post(
        "/api/v1/auth/local/login",
        json={"username": "alice", "password": "correct horse battery staple"},
    )
    assert denied.status_code == 200
    assert user_client.get("/api/v1/admin/users").status_code == 403

    client = TestClient(auth_app)
    admin_login = client.post(
        "/api/v1/auth/local/login",
        json={"username": "admin", "password": "admin password 123"},
    )
    assert admin_login.status_code == 200
    users = client.get("/api/v1/admin/users")
    assert users.status_code == 200
    assert users.json()["total"] == 2
    assert all("password_hash" not in item for item in users.json()["items"])


def test_admin_can_create_update_and_reset_user(auth_app):
    client = TestClient(auth_app)
    login = client.post(
        "/api/v1/auth/local/login",
        json={"username": "admin", "password": "admin password 123"},
    )
    assert login.status_code == 200

    created = client.post(
        "/api/v1/admin/users",
        json={
            "username": "analyst",
            "password": "analyst password 123",
            "roles": ["normal"],
            "scene_ids": ["contracts"],
        },
    )
    assert created.status_code == 200
    user_id = created.json()["user_id"]

    updated = client.patch(
        f"/api/v1/admin/users/{user_id}",
        json={"scene_ids": ["contracts", "receipts"]},
    )
    assert updated.status_code == 200
    assert updated.json()["scene_ids"] == ["contracts", "receipts"]

    reset = client.post(
        f"/api/v1/admin/users/{user_id}/password-reset",
        json={"password": "new analyst password 123"},
    )
    assert reset.status_code == 200
    assert auth_app.state.auth_store.authenticate(
        "analyst", "new analyst password 123", auth_app.state.auth_config
    ) is not None


def test_admin_cannot_remove_last_active_admin(auth_app):
    client = TestClient(auth_app)
    login = client.post(
        "/api/v1/auth/local/login",
        json={"username": "admin", "password": "admin password 123"},
    )
    assert login.status_code == 200
    user_id = login.json()["user"]["user_id"]
    response = client.patch(
        f"/api/v1/admin/users/{user_id}",
        json={"is_active": False},
    )
    assert response.status_code == 409


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
