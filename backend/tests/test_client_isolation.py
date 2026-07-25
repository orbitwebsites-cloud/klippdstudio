"""Regression tests for per-client data isolation.

The backend began as a single-user MVP that stored every project under one
constant user id. Once the site was public, that meant unrelated visitors shared
one bucket and could see each other's uploads and clips. These tests lock in the
fix: each client (identified by the X-Klippd-Client header, or a `client` query
parameter for URL-loaded media) only ever sees its own projects.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from local_store import LocalDatabase
import server


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    for name in ("videos", "audio", "output", "subtitles", "broll", "library"):
        (data_dir / name).mkdir(parents=True, exist_ok=True)
    database = LocalDatabase(data_dir / "test-db.json")
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "db", database)
    monkeypatch.setattr(server, "APP_ACCESS_TOKEN", "")
    with TestClient(server.app) as client:
        yield SimpleNamespace(client=client, data_dir=data_dir, db=database)


def _seed_project(env, pid: str, user_id: str) -> None:
    original = env.data_dir / "videos" / f"{pid}.mp4"
    original.write_bytes(b"source")
    asyncio.run(env.db.projects.insert_one({
        "id": pid, "user_id": user_id, "name": f"{pid}-name",
        "status": "uploaded", "original_path": str(original),
        "duration": 1.0, "width": 1920, "height": 1080,
    }))


def test_list_projects_is_scoped_to_the_requesting_client(api_env):
    _seed_project(api_env, "alice-project", "client_alice")
    _seed_project(api_env, "bob-project", "client_bob")

    alice = api_env.client.get("/api/projects", headers={"X-Klippd-Client": "alice"})
    bob = api_env.client.get("/api/projects", headers={"X-Klippd-Client": "bob"})

    assert [p["id"] for p in alice.json()] == ["alice-project"]
    assert [p["id"] for p in bob.json()] == ["bob-project"]


def test_client_cannot_read_another_clients_project_detail(api_env):
    _seed_project(api_env, "alice-project", "client_alice")

    mine = api_env.client.get("/api/projects/alice-project", headers={"X-Klippd-Client": "alice"})
    assert mine.status_code == 200

    stranger = api_env.client.get("/api/projects/alice-project", headers={"X-Klippd-Client": "bob"})
    assert stranger.status_code == 404


def test_client_cannot_delete_another_clients_project(api_env):
    _seed_project(api_env, "alice-project", "client_alice")

    denied = api_env.client.delete("/api/projects/alice-project", headers={"X-Klippd-Client": "bob"})
    assert denied.status_code == 404
    # The project must still exist for its real owner.
    assert asyncio.run(api_env.db.projects.find_one({"id": "alice-project"})) is not None


def test_media_uses_client_query_param_fallback(api_env):
    # Media/download URLs are loaded as <video src> etc. and cannot carry a
    # custom header, so the client id also travels as a `client` query param.
    _seed_project(api_env, "alice-project", "client_alice")

    owner = api_env.client.get("/api/media/original/alice-project?client=alice")
    assert owner.status_code == 200

    stranger = api_env.client.get("/api/media/original/alice-project?client=bob")
    assert stranger.status_code == 404


def test_missing_client_id_falls_back_to_the_legacy_bucket(api_env):
    # Requests with no client identity resolve to the deployment default so the
    # original owner's pre-isolation data remains reachable.
    _seed_project(api_env, "legacy-project", server.USER_ID)

    resp = api_env.client.get("/api/projects")
    assert [p["id"] for p in resp.json()] == ["legacy-project"]
