from __future__ import annotations

from fastapi import FastAPI

from dbgpt_app.dbgpt_server import mount_routers


def test_mount_routers_registers_ask_data_scene_routes():
    app = FastAPI()
    mount_routers(app)
    paths = {route.path for route in app.routes}

    assert "/api/v1/ask-data/scenes" in paths
    assert "/api/v1/ask-data/scenes/{scene_id}" in paths


def test_mount_routers_registers_query_snapshot_and_capability_routes():
    app = FastAPI()
    mount_routers(app)
    paths = {route.path for route in app.routes}

    expected = {
        "/api/v1/ask-data/query",
        "/api/v1/ask-data/queries/{query_id}/reply",
        "/api/v1/ask-data/conversations/{conversation_id}/reset",
        "/api/v1/ask-data/capabilities",
        "/api/v1/ask-data/runs",
        "/api/v1/ask-data/runs/{query_id}",
        "/api/v1/ask-data/scenes/{scene_id}/snapshot/check-drift",
    }
    assert expected <= paths
