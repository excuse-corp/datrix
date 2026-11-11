"""Injectable authorization policy for AskData resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class AskDataAuthorizationError(PermissionError):
    def __init__(self, code: str = "FORBIDDEN"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AskDataPrincipal:
    user_id: str
    roles: frozenset[str] = frozenset()
    scene_ids: frozenset[str] = frozenset()
    authz_version: int = 0

    @property
    def is_admin(self) -> bool:
        return self.is_platform_admin

    @property
    def is_platform_admin(self) -> bool:
        return "admin" in self.roles

    @property
    def can_manage_ask_data(self) -> bool:
        return bool({"admin", "ask_data_admin"} & self.roles)

    def can_access_scene(self, scene_id: str) -> bool:
        return self.is_platform_admin or scene_id in self.scene_ids


def principal_from_user(user: Any) -> AskDataPrincipal:
    if isinstance(user, dict):
        user_id = user.get("user_id") or user.get("username") or "anonymous"
        roles = user.get("roles") or user.get("role") or ()
        scene_ids = user.get("scene_ids") or ()
        authz_version = user.get("authz_version") or 0
    else:
        user_id = (
            getattr(user, "user_id", None)
            or getattr(user, "username", None)
            or "anonymous"
        )
        roles = getattr(user, "roles", None) or getattr(user, "role", ())
        scene_ids = getattr(user, "scene_ids", ())
        authz_version = getattr(user, "authz_version", 0)
    if isinstance(roles, str):
        roles = (roles,)
    roles = roles or ()
    if isinstance(scene_ids, str):
        scene_ids = (scene_ids,)
    scene_ids = scene_ids or ()
    return AskDataPrincipal(
        user_id=str(user_id),
        roles=frozenset(str(item) for item in roles if item),
        scene_ids=frozenset(str(item) for item in scene_ids if item),
        authz_version=int(authz_version or 0),
    )


class AskDataAuthorizer:
    def require_admin(self, principal: AskDataPrincipal) -> None:
        if not principal.can_manage_ask_data:
            raise AskDataAuthorizationError("ADMIN_REQUIRED")

    def require_scene_access(self, principal: AskDataPrincipal, scene_id: str) -> None:
        if not principal.can_access_scene(scene_id):
            raise AskDataAuthorizationError("SCENE_ACCESS_FORBIDDEN")

    def redact_runtime(
        self, principal: AskDataPrincipal, scene_id: str, value: dict
    ) -> dict:
        self.require_scene_access(principal, scene_id)
        if principal.is_admin:
            return value
        return {
            key: value[key]
            for key in (
                "revision_id",
                "schema_version",
                "query_spec_version",
                "compiler_version",
            )
            if key in value
        }


__all__ = [
    "AskDataAuthorizationError",
    "AskDataAuthorizer",
    "AskDataPrincipal",
    "principal_from_user",
]
