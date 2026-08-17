"""Injectable authorization policy for AskData resources."""

from __future__ import annotations

from dataclasses import dataclass


class AskDataAuthorizationError(PermissionError):
    def __init__(self, code: str = "FORBIDDEN"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AskDataPrincipal:
    user_id: str
    roles: frozenset[str] = frozenset()
    scene_ids: frozenset[str] = frozenset()

    @property
    def is_admin(self) -> bool:
        return bool({"admin", "ask_data_admin"} & self.roles)

    def can_access_scene(self, scene_id: str) -> bool:
        return self.is_admin or scene_id in self.scene_ids


class AskDataAuthorizer:
    def require_admin(self, principal: AskDataPrincipal) -> None:
        if not principal.is_admin:
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
]
