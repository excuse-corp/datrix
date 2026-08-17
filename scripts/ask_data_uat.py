#!/usr/bin/env python3
"""Run the fixed AskData UAT case set against a deployed API."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import tomllib


def _post(
    base_url: str, question: str, *, user_id: str, token: str | None
) -> tuple[dict, float]:
    started = time.monotonic()
    headers = {
        "Content-Type": "application/json",
        "X-User-Id": user_id,
        "X-Request-Id": f"uat-{time.time_ns()}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"{base_url.rstrip('/')}/api/v1/ask-data/query",
        data=json.dumps({"question": question}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:
            return (
                json.loads(response.read().decode("utf-8")),
                time.monotonic() - started,
            )
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        return (
            {"status": "http_failed", "http_status": exc.code, "error": payload},
            time.monotonic() - started,
        )
    except URLError as exc:
        return (
            {"status": "connection_failed", "error": str(exc.reason)},
            time.monotonic() - started,
        )


def _check(case: dict, response: dict) -> list[str]:
    failures: list[str] = []
    expected_status = case.get("expected_status")
    if expected_status and response.get("status") != expected_status:
        failures.append(
            f"status={response.get('status')!r}, expected={expected_status!r}"
        )
    expected_error = case.get("expected_error_code")
    if expected_error:
        errors = response.get("errors") or response.get("data", {}).get("errors", [])
        codes = {item.get("code") for item in errors if isinstance(item, dict)}
        if expected_error not in codes:
            failures.append(
                f"error_code={sorted(codes)!r}, expected={expected_error!r}"
            )
    if "expected_row_count" in case:
        results = response.get("results") or response.get("data", {}).get("results", [])
        row_count = sum(item.get("row_count", 0) for item in results)
        if row_count != case["expected_row_count"]:
            failures.append(
                f"row_count={row_count}, expected={case['expected_row_count']}"
            )
    if "expected_truncated" in case:
        results = response.get("results") or response.get("data", {}).get("results", [])
        truncated = any(item.get("truncated") for item in results)
        if truncated != case["expected_truncated"]:
            failures.append(
                f"truncated={truncated}, expected={case['expected_truncated']}"
            )
    expected_mode = case.get("expected_combine_mode")
    if expected_mode:
        combined = response.get("combined") or response.get("data", {}).get(
            "bundle", {}
        ).get("combined", {})
        mode = combined.get("mode") if isinstance(combined, dict) else None
        if mode != expected_mode:
            failures.append(f"combine_mode={mode!r}, expected={expected_mode!r}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/ask-data-uat.example.toml"),
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("ASK_DATA_BASE_URL", "http://127.0.0.1:5670"),
    )
    parser.add_argument("--token", default=os.getenv("ASK_DATA_TOKEN"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    user_id = config["uat"].get("ordinary_user", "uat-user")
    cases = config["uat"]["cases"]
    passed = 0
    route_correct = 0
    query_spec_valid = 0
    sql_rejections = 0
    sql_rejection_expected = 0
    records = []
    started = time.monotonic()
    for case in cases:
        response, duration = _post(
            args.base_url,
            case["question"],
            user_id=user_id,
            token=args.token,
        )
        failures = _check(case, response)
        if response.get("status") == case.get("expected_status"):
            route_correct += 1
        results = response.get("results") or response.get("data", {}).get(
            "results", []
        )
        if response.get("status") in {"succeeded", "partial_succeeded"}:
            if all(item.get("query_spec_hash") for item in results):
                query_spec_valid += 1
        expected_error = case.get("expected_error_code")
        if expected_error == "INVALID_PLAN":
            sql_rejection_expected += 1
            errors = response.get("errors") or response.get("data", {}).get(
                "errors", []
            )
            if any(item.get("code") == expected_error for item in errors):
                sql_rejections += 1
        records.append(
            {
                "name": case["name"],
                "status": response.get("status"),
                "duration_seconds": duration,
                "passed": not failures,
                "failures": failures,
                "query_id": response.get("query_id")
                or response.get("data", {}).get("query_id"),
            }
        )
        if failures:
            print(f"FAIL {case['name']}: {'; '.join(failures)}")
        else:
            passed += 1
            print(f"PASS {case['name']}")
    elapsed = time.monotonic() - started
    durations = sorted(item["duration_seconds"] for item in records)
    p95_index = max(0, min(len(durations) - 1, int(len(durations) * 0.95) - 1))
    report = {
        "passed": passed,
        "total": len(cases),
        "elapsed_seconds": elapsed,
        "metrics": {
            "route_accuracy": route_correct / len(cases) if cases else 1.0,
            "query_spec_validity": query_spec_valid / len(cases) if cases else 1.0,
            "sql_rejection_rate": (
                sql_rejections / sql_rejection_expected
                if sql_rejection_expected
                else 1.0
            ),
            "p95_seconds": durations[p95_index] if durations else 0.0,
        },
        "cases": records,
    }
    print(json.dumps(report, ensure_ascii=False))
    if args.report:
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
