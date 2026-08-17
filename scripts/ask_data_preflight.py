#!/usr/bin/env python3
"""Validate AskData deployment prerequisites without exposing secrets."""

from __future__ import annotations

import argparse
import os
import socket
from pathlib import Path

import tomllib


def check_source(name: str, config: dict, *, skip_network: bool) -> list[str]:
    failures = []
    for variable in ("ECOLOGY_DB_USER", "ECOLOGY_DB_PASSWORD"):
        if not os.getenv(variable):
            failures.append(f"missing environment variable: {variable}")
    host = str(config.get("host", ""))
    port_value = str(config.get("port", 1433))
    if port_value.startswith("${env:"):
        expression = port_value[6:-1]
        variable, _, fallback = expression.partition(":-")
        port_value = os.getenv(variable, fallback)
    try:
        port = int(port_value)
    except ValueError:
        failures.append(f"source {name} has an invalid port")
        return failures
    if not host:
        failures.append(f"source {name} has no host")
    elif not skip_network:
        try:
            with socket.create_connection((host, port), timeout=3):
                pass
        except OSError as exc:
            failures.append(f"source {name} is unreachable: {host}:{port} ({exc})")
    return failures


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path, default=Path("configs/ask-data.example.toml")
    )
    parser.add_argument("--env-file", type=Path, default=Path("configs/.env.ask-data"))
    parser.add_argument("--skip-network", action="store_true")
    args = parser.parse_args()
    load_env_file(args.env_file)
    document = tomllib.loads(args.config.read_text(encoding="utf-8"))
    failures: list[str] = []
    sources = document.get("ask_data", {}).get("sources", {})
    for name, source in sources.items():
        if source.get("enabled", True):
            failures.extend(
                check_source(name, source, skip_network=args.skip_network)
            )
    if not os.getenv("DBGPT_METADATA_DB"):
        failures.append("missing environment variable: DBGPT_METADATA_DB")
    report = {"ready": not failures, "failures": failures}
    print(report)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
