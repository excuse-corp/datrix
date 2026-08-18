#!/usr/bin/env python
"""Create the information-project Scene after DataMan and PostgreSQL are ready."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    semantic_path = root / "dataman1111/scenes/information_project_contract_report.semantic.md"
    api_base_url = os.environ.get("DATAMAN_API_BASE_URL", "http://127.0.0.1:5670")
    access_token = os.environ.get("DATAMAN_ADMIN_ACCESS_TOKEN", "")
    if not access_token:
        raise SystemExit("DATAMAN_ADMIN_ACCESS_TOKEN is required")

    payload = {
        "scene_id": "information_project_contract_report",
        "name": "信息化项目合同分析",
        "description": "查询信息化项目、合同金额、付款、项目状态及负责人分布。",
        "data_source_name": "dataman_data",
        "view_name": "reporting.vw_information_project_contract_report",
        "semantic_md": semantic_path.read_text(encoding="utf-8"),
    }
    response = httpx.post(
        f"{api_base_url.rstrip('/')}/api/v1/ask-data/scenes",
        json=payload,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if response.status_code == 409:
        print("Scene already exists; no changes made")
        return 0
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
