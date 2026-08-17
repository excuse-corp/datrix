#!/usr/bin/env python
"""Create local DB-GPT authentication users without exposing passwords in argv."""

from __future__ import annotations

import argparse
import getpass
import os

from dbgpt_app.auth import AuthStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("--user-id")
    parser.add_argument("--role", action="append", dest="roles")
    parser.add_argument("--scene", action="append", dest="scene_ids")
    parser.add_argument(
        "--database",
        default=os.getenv("DBGPT_AUTH_DB", "sqlite:///pilot/meta_data/dbgpt_auth.db"),
    )
    args = parser.parse_args()
    password = getpass.getpass("Password: ")
    if not password:
        parser.error("password cannot be empty")
    store = AuthStore(args.database)
    store.create_user(
        args.username,
        password,
        user_id=args.user_id,
        roles=tuple(args.roles or ["normal"]),
        scene_ids=tuple(args.scene_ids or []),
    )
    print(f"Created DB-GPT user: {args.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
