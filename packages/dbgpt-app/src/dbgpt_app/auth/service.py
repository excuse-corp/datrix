"""DataMan local accounts and pluggable unified identity authentication."""
# ruff: noqa: E501

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    inspect,
    select,
    update,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse, Response

_COOKIE_NAME = "dataman_session"
_DEFAULT_PUBLIC_PATHS = {
    "/api/v1/auth/config",
    "/api/v1/auth/health",
    "/api/v1/auth/login",
    "/api/v1/auth/local/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/docs",
    "/openapi.json",
    "/redoc",
}


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _utcnow() -> datetime:
    return datetime.utcnow()


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return f"pbkdf2_sha256$240000${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), _unb64(salt), int(iterations)
        )
        return hmac.compare_digest(_b64(actual), expected)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    username: str
    roles: tuple[str, ...]
    scene_ids: tuple[str, ...] = ()
    is_active: bool = True
    authz_version: int = 1

    @property
    def role(self) -> str:
        return self.roles[0] if self.roles else "normal"


@dataclass(frozen=True)
class AuthSession:
    session_id: str
    refresh_token: str
    expires_at: datetime


@dataclass(frozen=True)
class ProviderConfig:
    provider_id: str
    provider_type: str
    display_name: str
    base_url: str
    login_path: str = "/login"
    validate_path: str = "/serviceValidate"
    callback_path: str = ""
    subject_candidates: tuple[str, ...] = ("user",)
    attribute_mapping: dict[str, str] | None = None
    enabled: bool = True
    verify_tls: bool = True
    connect_timeout_seconds: float = 3
    read_timeout_seconds: float = 5

    @property
    def callback_url(self) -> str:
        return self.callback_path


class AuthConfig:
    def __init__(
        self,
        secret: str,
        ttl_seconds: int = 600,
        *,
        issuer: str = "dataman",
        audience: str = "dataman-api",
        mode: str = "hybrid",
        public_base_url: str = "",
        providers: tuple[ProviderConfig, ...] = (),
        refresh_ttl_seconds: int = 28800,
        session_idle_timeout_seconds: int = 3600,
        cookie_secure: bool = True,
        cookie_name: str = _COOKIE_NAME,
        provisioning_strategy: str = "create_active",
        default_roles: tuple[str, ...] = ("normal",),
        max_failed_attempts: int = 5,
        lockout_seconds: int = 900,
    ):
        if len(secret) < 32:
            raise ValueError("DBGPT_AUTH_SECRET must contain at least 32 characters")
        if mode not in {"local_only", "external_only", "hybrid"}:
            raise ValueError("authentication mode must be local_only, external_only, or hybrid")
        if provisioning_strategy not in {"disabled", "create_pending", "create_active"}:
            raise ValueError("invalid authentication provisioning strategy")
        self.secret = secret.encode()
        self.ttl_seconds = ttl_seconds
        self.issuer = issuer
        self.audience = audience
        self.mode = mode
        self.public_base_url = public_base_url.rstrip("/")
        self.providers = {provider.provider_id: provider for provider in providers}
        self.refresh_ttl_seconds = refresh_ttl_seconds
        self.session_idle_timeout_seconds = session_idle_timeout_seconds
        self.cookie_secure = cookie_secure
        self.cookie_name = cookie_name
        self.provisioning_strategy = provisioning_strategy
        self.default_roles = default_roles
        self.max_failed_attempts = max_failed_attempts
        self.lockout_seconds = lockout_seconds
        if mode == "external_only" and not self.enabled_providers:
            raise ValueError("external_only mode requires an enabled identity provider")
        if any(not provider.provider_id.replace("-", "").replace("_", "").isalnum() for provider in providers):
            raise ValueError("provider ids may contain only letters, numbers, hyphens, and underscores")

    @property
    def enabled_providers(self) -> tuple[ProviderConfig, ...]:
        return tuple(provider for provider in self.providers.values() if provider.enabled)

    def provider(self, provider_id: str) -> ProviderConfig:
        provider = self.providers.get(provider_id)
        if provider is None or not provider.enabled:
            raise HTTPException(status_code=404, detail="AUTH_PROVIDER_UNAVAILABLE")
        return provider

    def issue(self, user: AuthUser, session_id: str, *, auth_method: str, provider_id: str | None) -> str:
        header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
        now = int(time.time())
        payload = _b64(
            json.dumps(
                {
                    "iss": self.issuer,
                    "aud": self.audience,
                    "sub": user.user_id,
                    "sid": session_id,
                    "username": user.username,
                    "auth_method": auth_method,
                    "provider_id": provider_id,
                    "authz_version": user.authz_version,
                    "iat": now,
                    "exp": now + self.ttl_seconds,
                },
                separators=(",", ":"),
            ).encode()
        )
        unsigned = f"{header}.{payload}".encode()
        signature = _b64(hmac.new(self.secret, unsigned, hashlib.sha256).digest())
        return f"{header}.{payload}.{signature}"

    def verify(self, token: str) -> dict[str, Any]:
        try:
            header, payload, signature = token.split(".", 2)
            header_claims = json.loads(_unb64(header))
            if header_claims != {"alg": "HS256", "typ": "JWT"}:
                raise ValueError
            unsigned = f"{header}.{payload}".encode()
            expected = _b64(hmac.new(self.secret, unsigned, hashlib.sha256).digest())
            claims = json.loads(_unb64(payload))
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            if claims["iss"] != self.issuer or claims["aud"] != self.audience:
                raise ValueError
            if int(claims["exp"]) <= int(time.time()):
                raise ValueError
            if not claims["sub"] or not claims["sid"]:
                raise ValueError
            return claims
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=401, detail="AUTH_SESSION_EXPIRED")


class AuthStore:
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, future=True)
        metadata = MetaData()
        self.users = Table(
            "auth_user", metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("user_id", String(64), nullable=False, unique=True),
            Column("username", String(128), nullable=False, unique=True),
            Column("display_name", String(256), nullable=True),
            Column("roles", String(1024), nullable=False, default="normal"),
            Column("scene_ids", String(4096), nullable=False, default=""),
            Column("is_active", Boolean, nullable=False, default=True),
            Column("authz_version", Integer, nullable=False, default=1),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
        )
        self.credentials = Table(
            "auth_local_credential", metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("user_id", String(64), nullable=False, unique=True),
            Column("password_hash", String(512), nullable=False),
            Column("failed_attempts", Integer, nullable=False, default=0),
            Column("locked_until", DateTime(timezone=True), nullable=True),
            Column("changed_at", DateTime(timezone=True), nullable=False),
        )
        self.identities = Table(
            "auth_external_identity", metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("user_id", String(64), nullable=False),
            Column("provider_id", String(128), nullable=False),
            Column("subject", String(256), nullable=False),
            Column("attributes", Text, nullable=False, default="{}"),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
            UniqueConstraint("provider_id", "subject", name="uq_auth_identity_provider_subject"),
        )
        self.sessions = Table(
            "auth_session", metadata,
            Column("session_id", String(64), primary_key=True),
            Column("user_id", String(64), nullable=False),
            Column("auth_method", String(64), nullable=False),
            Column("provider_id", String(128), nullable=True),
            Column("refresh_token_hash", String(128), nullable=False, unique=True),
            Column("expires_at", DateTime(timezone=True), nullable=False),
            Column("idle_expires_at", DateTime(timezone=True), nullable=False),
            Column("revoked_at", DateTime(timezone=True), nullable=True),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("last_seen_at", DateTime(timezone=True), nullable=False),
        )
        self.transactions = Table(
            "auth_login_transaction", metadata,
            Column("state", String(128), primary_key=True),
            Column("provider_id", String(128), nullable=False),
            Column("return_to", String(2048), nullable=False),
            Column("expires_at", DateTime(timezone=True), nullable=False),
        )
        metadata.create_all(self.engine)
        self._migrate_legacy_users()

    def _migrate_legacy_users(self) -> None:
        if not inspect(self.engine).has_table("dbgpt_auth_users"):
            return
        legacy_metadata = MetaData()
        legacy_users = Table("dbgpt_auth_users", legacy_metadata, autoload_with=self.engine)
        now = _utcnow()
        with self.engine.begin() as connection:
            rows = connection.execute(select(legacy_users)).mappings().all()
            for row in rows:
                exists = connection.execute(select(self.users.c.user_id).where(self.users.c.username == row["username"])).first()
                if exists:
                    continue
                user_id = row["user_id"]
                connection.execute(self.users.insert().values(user_id=user_id, username=row["username"], display_name=row["username"], roles=row["roles"], scene_ids=row["scene_ids"], is_active=row["is_active"], authz_version=1, created_at=now, updated_at=now))
                connection.execute(self.credentials.insert().values(user_id=user_id, password_hash=row["password_hash"], failed_attempts=0, locked_until=None, changed_at=now))

    @staticmethod
    def _user(row: Any) -> AuthUser:
        return AuthUser(
            user_id=row["user_id"],
            username=row["username"],
            roles=tuple(filter(None, row["roles"].split(","))),
            scene_ids=tuple(filter(None, row["scene_ids"].split(","))),
            is_active=row["is_active"],
            authz_version=row["authz_version"],
        )

    def create_user(self, username: str, password: str, *, user_id: str | None = None, roles: tuple[str, ...] = ("normal",), scene_ids: tuple[str, ...] = ()) -> AuthUser:
        if not username.strip() or not password:
            raise ValueError("username and password are required")
        now = _utcnow()
        user_id = user_id or str(uuid.uuid4())
        with self.engine.begin() as connection:
            connection.execute(self.users.insert().values(user_id=user_id, username=username, display_name=username, roles=",".join(roles), scene_ids=",".join(scene_ids), is_active=True, authz_version=1, created_at=now, updated_at=now))
            connection.execute(self.credentials.insert().values(user_id=user_id, password_hash=hash_password(password), failed_attempts=0, locked_until=None, changed_at=now))
        return AuthUser(user_id, username, roles, scene_ids)

    def get_user(self, user_id: str) -> AuthUser | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.users).where(self.users.c.user_id == user_id)).mappings().first()
        return self._user(row) if row else None

    def authenticate(self, username: str, password: str, config: AuthConfig) -> AuthUser | None:
        now = _utcnow()
        with self.engine.begin() as connection:
            row = connection.execute(select(self.users, self.credentials.c.password_hash, self.credentials.c.failed_attempts, self.credentials.c.locked_until).join(self.credentials, self.users.c.user_id == self.credentials.c.user_id).where(self.users.c.username == username)).mappings().first()
            if not row or not row["is_active"] or (row["locked_until"] and row["locked_until"] > now):
                return None
            if not verify_password(password, row["password_hash"]):
                attempts = row["failed_attempts"] + 1
                locked_until = now + timedelta(seconds=config.lockout_seconds) if attempts >= config.max_failed_attempts else None
                connection.execute(update(self.credentials).where(self.credentials.c.user_id == row["user_id"]).values(failed_attempts=attempts, locked_until=locked_until))
                return None
            connection.execute(update(self.credentials).where(self.credentials.c.user_id == row["user_id"]).values(failed_attempts=0, locked_until=None))
        return self._user(row)

    def change_password(self, user_id: str, old_password: str, new_password: str, config: AuthConfig) -> bool:
        if len(new_password) < 12:
            raise HTTPException(status_code=422, detail="Password must contain at least 12 characters")
        user = self.get_user(user_id)
        if user is None or self.authenticate(user.username, old_password, config) is None:
            return False
        now = _utcnow()
        with self.engine.begin() as connection:
            connection.execute(update(self.credentials).where(self.credentials.c.user_id == user_id).values(password_hash=hash_password(new_password), changed_at=now))
            connection.execute(update(self.users).where(self.users.c.user_id == user_id).values(authz_version=self.users.c.authz_version + 1, updated_at=now))
        self.revoke_user_sessions(user_id)
        return True

    def resolve_external_identity(self, provider: ProviderConfig, subject: str, attributes: dict[str, str], config: AuthConfig) -> AuthUser | None:
        now = _utcnow()
        with self.engine.begin() as connection:
            identity = connection.execute(select(self.identities).where(and_(self.identities.c.provider_id == provider.provider_id, self.identities.c.subject == subject))).mappings().first()
            if identity:
                connection.execute(update(self.identities).where(self.identities.c.id == identity["id"]).values(attributes=json.dumps(attributes, ensure_ascii=False), updated_at=now))
                row = connection.execute(select(self.users).where(self.users.c.user_id == identity["user_id"])).mappings().first()
                return self._user(row) if row and row["is_active"] else None
            if config.provisioning_strategy == "disabled":
                return None
            username = self._available_username(connection, attributes.get("username") or subject)
            active = config.provisioning_strategy == "create_active"
            user_id = str(uuid.uuid4())
            connection.execute(self.users.insert().values(user_id=user_id, username=username, display_name=attributes.get("display_name") or username, roles=",".join(config.default_roles), scene_ids="", is_active=active, authz_version=1, created_at=now, updated_at=now))
            connection.execute(self.identities.insert().values(user_id=user_id, provider_id=provider.provider_id, subject=subject, attributes=json.dumps(attributes, ensure_ascii=False), created_at=now, updated_at=now))
            return AuthUser(user_id, username, config.default_roles, (), active, 1) if active else None

    def _available_username(self, connection: Any, base: str) -> str:
        base = base.strip()[:120] or "external-user"
        candidate = base
        sequence = 1
        while connection.execute(select(self.users.c.user_id).where(self.users.c.username == candidate)).first():
            sequence += 1
            candidate = f"{base[:120]}-{sequence}"
        return candidate

    def create_session(self, user: AuthUser, *, auth_method: str, provider_id: str | None, config: AuthConfig) -> AuthSession:
        now = _utcnow()
        refresh_token = secrets.token_urlsafe(48)
        expires_at = now + timedelta(seconds=config.refresh_ttl_seconds)
        with self.engine.begin() as connection:
            connection.execute(self.sessions.insert().values(session_id=str(uuid.uuid4()), user_id=user.user_id, auth_method=auth_method, provider_id=provider_id, refresh_token_hash=_token_hash(refresh_token), expires_at=expires_at, idle_expires_at=now + timedelta(seconds=config.session_idle_timeout_seconds), revoked_at=None, created_at=now, last_seen_at=now))
            row = connection.execute(select(self.sessions).where(self.sessions.c.refresh_token_hash == _token_hash(refresh_token))).mappings().one()
        return AuthSession(row["session_id"], refresh_token, expires_at)

    def authenticate_access_token(self, token: str, config: AuthConfig) -> AuthUser:
        claims = config.verify(token)
        now = _utcnow()
        with self.engine.begin() as connection:
            session = connection.execute(select(self.sessions).where(self.sessions.c.session_id == claims["sid"])).mappings().first()
            user_row = connection.execute(select(self.users).where(self.users.c.user_id == claims["sub"])).mappings().first()
            if not session or not user_row or session["user_id"] != claims["sub"] or session["revoked_at"] or session["expires_at"] <= now or session["idle_expires_at"] <= now or not user_row["is_active"]:
                raise HTTPException(status_code=401, detail="AUTH_SESSION_EXPIRED")
            user = self._user(user_row)
            if user.authz_version != int(claims["authz_version"]):
                raise HTTPException(status_code=401, detail="AUTH_SESSION_EXPIRED")
            connection.execute(update(self.sessions).where(self.sessions.c.session_id == session["session_id"]).values(last_seen_at=now, idle_expires_at=now + timedelta(seconds=config.session_idle_timeout_seconds)))
        return user

    def rotate_session(self, refresh_token: str, config: AuthConfig) -> tuple[AuthUser, AuthSession, str, str | None] | None:
        now = _utcnow()
        refresh_hash = _token_hash(refresh_token)
        with self.engine.begin() as connection:
            session = connection.execute(select(self.sessions).where(self.sessions.c.refresh_token_hash == refresh_hash)).mappings().first()
            if not session or session["revoked_at"] or session["expires_at"] <= now or session["idle_expires_at"] <= now:
                return None
            user_row = connection.execute(select(self.users).where(self.users.c.user_id == session["user_id"])).mappings().first()
            if not user_row or not user_row["is_active"]:
                return None
            connection.execute(update(self.sessions).where(self.sessions.c.session_id == session["session_id"]).values(revoked_at=now))
        user = self._user(user_row)
        next_session = self.create_session(user, auth_method=session["auth_method"], provider_id=session["provider_id"], config=config)
        return user, next_session, session["auth_method"], session["provider_id"]

    def revoke_session(self, session_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(update(self.sessions).where(self.sessions.c.session_id == session_id).values(revoked_at=_utcnow()))

    def revoke_user_sessions(self, user_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(update(self.sessions).where(self.sessions.c.user_id == user_id).values(revoked_at=_utcnow()))

    def create_login_transaction(self, provider_id: str, return_to: str) -> str:
        state = secrets.token_urlsafe(32)
        with self.engine.begin() as connection:
            connection.execute(self.transactions.insert().values(state=state, provider_id=provider_id, return_to=return_to, expires_at=_utcnow() + timedelta(minutes=5)))
        return state

    def consume_login_transaction(self, state: str, provider_id: str) -> str | None:
        now = _utcnow()
        with self.engine.begin() as connection:
            transaction = connection.execute(select(self.transactions).where(self.transactions.c.state == state)).mappings().first()
            connection.execute(self.transactions.delete().where(self.transactions.c.state == state))
        if not transaction or transaction["provider_id"] != provider_id or transaction["expires_at"] <= now:
            return None
        return transaction["return_to"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class PasswordChangeRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=12, max_length=512)


def _safe_return_to(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


def _cas_attributes(document: str, provider: ProviderConfig) -> tuple[str, dict[str, str]]:
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        raise HTTPException(status_code=502, detail="AUTH_TICKET_INVALID") from exc
    namespace = "{http://www.yale.edu/tp/cas}"
    success = root.find(f".//{namespace}authenticationSuccess")
    if success is None:
        raise HTTPException(status_code=401, detail="AUTH_TICKET_INVALID")
    values: dict[str, str] = {}
    for child in success.iter():
        name = child.tag.rsplit("}", 1)[-1]
        if child.text and child.text.strip():
            values[name] = child.text.strip()
    subject = next((values[key] for key in provider.subject_candidates if values.get(key)), None)
    if not subject:
        raise HTTPException(status_code=422, detail="AUTH_REQUIRED_ATTRIBUTE_MISSING")
    mapped = {target: values[source] for target, source in (provider.attribute_mapping or {}).items() if values.get(source)}
    mapped.setdefault("username", subject)
    return subject, mapped


async def _validate_cas_ticket(provider: ProviderConfig, ticket: str, service_url: str) -> tuple[str, dict[str, str]]:
    timeout = httpx.Timeout(provider.read_timeout_seconds, connect=provider.connect_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=provider.verify_tls, follow_redirects=False) as client:
            response = await client.get(f"{provider.base_url.rstrip('/')}{provider.validate_path}", params={"service": service_url, "ticket": ticket})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="AUTH_PROVIDER_UNAVAILABLE") from exc
    return _cas_attributes(response.text, provider)


class AuthenticationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, auth_config: AuthConfig, store: AuthStore, *, required: bool = False):
        super().__init__(app)
        self.auth_config = auth_config
        self.store = store
        self.required = required

    def _public(self, path: str) -> bool:
        return path in _DEFAULT_PUBLIC_PATHS or path.startswith("/api/v1/auth/sso/") or path.startswith("/api/v1/auth/provider/")

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        authorization = request.headers.get("Authorization", "")
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else request.cookies.get(self.auth_config.cookie_name)
        if token:
            try:
                request.state.user = self.store.authenticate_access_token(token, self.auth_config)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        elif self.required and not self._public(request.url.path):
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
        return await call_next(request)


def ask_data_principal_resolver(request: Request):
    from dbgpt_app.scene.ask_data.security import AskDataPrincipal

    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return AskDataPrincipal(user_id=user.user_id, roles=frozenset(user.roles), scene_ids=frozenset(user.scene_ids))


def configure_authentication(app: Any, *, database_url: str, secret: str, ttl_seconds: int = 600, required: bool = False, **options: Any) -> AuthStore:
    config = AuthConfig(secret, ttl_seconds, **options)
    store = AuthStore(database_url)
    app.state.auth_store = store
    app.state.auth_config = config
    app.state.auth_required = required
    app.add_middleware(AuthenticationMiddleware, auth_config=config, store=store, required=required)
    app.state.ask_data_principal_resolver = ask_data_principal_resolver
    router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])

    def session_response(user: AuthUser, session: AuthSession, *, auth_method: str, provider_id: str | None) -> JSONResponse:
        access_token = config.issue(user, session.session_id, auth_method=auth_method, provider_id=provider_id)
        response = JSONResponse({"access_token": access_token, "refresh_token": session.refresh_token, "token_type": "bearer", "expires_in": config.ttl_seconds, "user": user.__dict__})
        response.set_cookie(config.cookie_name, access_token, max_age=config.ttl_seconds, httponly=True, secure=config.cookie_secure, samesite="lax", path="/")
        response.set_cookie(f"{config.cookie_name}_refresh", session.refresh_token, max_age=config.refresh_ttl_seconds, httponly=True, secure=config.cookie_secure, samesite="lax", path="/api/v1/auth")
        return response

    @router.get("/health")
    def auth_health():
        return {"status": "ok"}

    @router.get("/config")
    def auth_public_config():
        return {"mode": config.mode, "providers": [{"id": provider.provider_id, "type": provider.provider_type, "display_name": provider.display_name} for provider in config.enabled_providers]}

    @router.post("/local/login")
    @router.post("/login", include_in_schema=False)
    def local_login(payload: LoginRequest):
        if config.mode == "external_only":
            raise HTTPException(status_code=403, detail="AUTH_PERMISSION_DENIED")
        user = store.authenticate(payload.username, payload.password, config)
        if user is None:
            raise HTTPException(status_code=401, detail="AUTH_INVALID_CREDENTIALS")
        return session_response(user, store.create_session(user, auth_method="local", provider_id=None, config=config), auth_method="local", provider_id=None)

    @router.get("/sso/{provider_id}/login")
    def sso_login(provider_id: str, return_to: str | None = None):
        if config.mode == "local_only":
            raise HTTPException(status_code=403, detail="AUTH_PERMISSION_DENIED")
        provider = config.provider(provider_id)
        if provider.provider_type != "cas":
            raise HTTPException(status_code=501, detail="unsupported_operation")
        if not provider.callback_url:
            raise HTTPException(status_code=500, detail="AUTH_PROVIDER_UNAVAILABLE")
        state = store.create_login_transaction(provider_id, _safe_return_to(return_to))
        service_url = f"{provider.callback_url}?{urlencode({'state': state})}"
        login_url = f"{provider.base_url.rstrip('/')}{provider.login_path}?{urlencode({'service': service_url})}"
        return RedirectResponse(login_url, status_code=302)

    @router.get("/sso/{provider_id}/callback")
    async def sso_callback(provider_id: str, ticket: str, state: str):
        provider = config.provider(provider_id)
        return_to = store.consume_login_transaction(state, provider_id)
        if return_to is None:
            raise HTTPException(status_code=401, detail="AUTH_TICKET_REPLAYED")
        service_url = f"{provider.callback_url}?{urlencode({'state': state})}"
        subject, attributes = await _validate_cas_ticket(provider, ticket, service_url)
        user = store.resolve_external_identity(provider, subject, attributes, config)
        if user is None:
            raise HTTPException(status_code=403, detail="AUTH_PERMISSION_DENIED")
        session = store.create_session(user, auth_method="cas", provider_id=provider_id, config=config)
        access_token = config.issue(user, session.session_id, auth_method="cas", provider_id=provider_id)
        response = RedirectResponse(return_to, status_code=302)
        response.set_cookie(config.cookie_name, access_token, max_age=config.ttl_seconds, httponly=True, secure=config.cookie_secure, samesite="lax", path="/")
        return response

    @router.post("/refresh")
    def refresh(request: Request):
        refresh_token = request.headers.get("X-Refresh-Token") or request.cookies.get(f"{config.cookie_name}_refresh")
        if not refresh_token:
            raise HTTPException(status_code=401, detail="AUTH_SESSION_EXPIRED")
        rotated = store.rotate_session(refresh_token, config)
        if rotated is None:
            raise HTTPException(status_code=401, detail="AUTH_SESSION_EXPIRED")
        user, session, auth_method, provider_id = rotated
        return session_response(user, session, auth_method=auth_method, provider_id=provider_id)

    @router.post("/logout")
    def logout(request: Request):
        token = request.headers.get("Authorization", "")[7:].strip() if request.headers.get("Authorization", "").lower().startswith("bearer ") else request.cookies.get(config.cookie_name)
        if token:
            try:
                store.revoke_session(config.verify(token)["sid"])
            except HTTPException:
                pass
        response = JSONResponse({"status": "ok"})
        response.delete_cookie(config.cookie_name, path="/")
        response.delete_cookie(f"{config.cookie_name}_refresh", path="/api/v1/auth")
        return response

    @router.get("/me")
    def me(request: Request):
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return user.__dict__

    @router.post("/password/change")
    def change_password(payload: PasswordChangeRequest, request: Request):
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not store.change_password(user.user_id, payload.old_password, payload.new_password, config):
            raise HTTPException(status_code=401, detail="AUTH_INVALID_CREDENTIALS")
        return {"status": "ok"}

    app.include_router(router)
    from dbgpt_app.scene.ask_data.security import AskDataAuthorizer
    app.state.ask_data_authorizer = AskDataAuthorizer()
    return store
