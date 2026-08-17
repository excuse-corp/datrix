#!/usr/bin/env python3
"""Measure AskData request latency at configured concurrency levels."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def post(base_url: str, question: str, user_id: str, token: str | None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-User-Id": user_id,
        "X-Request-Id": f"perf-{time.time_ns()}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"{base_url.rstrip('/')}/api/v1/ask-data/query",
        data=json.dumps({"question": question}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.monotonic()
    try:
        with urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return {
                "status": payload.get("status"),
                "duration_seconds": time.monotonic() - started,
            }
    except HTTPError as exc:
        return {
            "status": "http_failed",
            "http_status": exc.code,
            "duration_seconds": time.monotonic() - started,
        }
    except URLError as exc:
        return {
            "status": "connection_failed",
            "error": str(exc.reason),
            "duration_seconds": time.monotonic() - started,
        }


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * ratio) - 1))
    return ordered[index]


def run_level(
    base_url: str,
    question: str,
    user_id: str,
    token: str | None,
    concurrency: int,
    runs: int,
) -> dict:
    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(post, base_url, question, user_id, token)
            for _ in range(concurrency * runs)
        ]
        for future in as_completed(futures):
            records.append(future.result())
    durations = [item["duration_seconds"] for item in records]
    statuses: dict[str, int] = {}
    for item in records:
        status = str(item.get("status"))
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "concurrency": concurrency,
        "requests": len(records),
        "statuses": statuses,
        "mean_seconds": statistics.fmean(durations) if durations else 0.0,
        "p50_seconds": percentile(durations, 0.50),
        "p95_seconds": percentile(durations, 0.95),
        "max_seconds": max(durations) if durations else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=os.getenv("ASK_DATA_BASE_URL", "http://127.0.0.1:5670"),
    )
    parser.add_argument("--question", required=True)
    parser.add_argument("--user-id", default=os.getenv("ASK_DATA_USER", "perf-user"))
    parser.add_argument("--token", default=os.getenv("ASK_DATA_TOKEN"))
    parser.add_argument("--concurrency", nargs="+", type=int, default=[2, 5, 10])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.runs < 1 or any(level < 1 for level in args.concurrency):
        parser.error("--runs and --concurrency values must be positive")
    report = {
        "base_url": args.base_url,
        "question": args.question,
        "levels": [
            run_level(
                args.base_url,
                args.question,
                args.user_id,
                args.token,
                concurrency,
                args.runs,
            )
            for concurrency in args.concurrency
        ],
    }
    print(json.dumps(report, ensure_ascii=False))
    if args.report:
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
