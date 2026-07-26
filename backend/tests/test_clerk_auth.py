"""Tests for Clerk-authenticated mode.

With Clerk configured the backend derives identity from a verified session JWT
(Authorization: Bearer) or a server-signed media token (?mt=), scopes data to the
Clerk user id, and returns 401 for unauthenticated access to protected paths.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import auth
from local_store import LocalDatabase
import server


@pytest.fixture
def clerk_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    for name in ("videos", "audio", "output", "subtitles", "broll", "library"):
        (data_dir / name).mkdir(parents=True, exist_ok=True)
    database = LocalDatabase(data_dir / "test-db.json")
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "db", database)
    monkeypatch.setattr(server, "APP_ACCESS_TOKEN", "")

    # Turn on Clerk mode with a fake verifier: the token string *is* the subject.
    monkeypatch.setattr(auth, "clerk_enabled", lambda: True)

    def fake_verify(token: str) -> str:
        if not token or token.startswith("bad"):
            raise auth.AuthError("invalid")
        return token  # e.g. "alice" -> user id "alice"

    monkeypatch.setattr(auth, "verify_session_token", fake_verify)

    with TestClient(server.app) as client:
        yield SimpleNamespace(client=client, data_dir=data_dir, db=database)


def _bearer(sub: str) -> dict:
    return {"Authorization": f"Bearer {sub}"}


def _seed_project(env, pid: str, user_id: str) -> None:
    original = env.data_dir / "videos" / f"{pid}.mp4"
    original.write_bytes(b"source")
    asyncio.run(env.db.projects.insert_one({
        "id": pid, "user_id": user_id, "name": f"{pid}-name",
        "status": "uploaded", "original_path": str(original),
        "duration": 1.0, "width": 1920, "height": 1080,
    }))


def test_protected_path_requires_authentication(clerk_env):
    assert clerk_env.client.get("/api/projects").status_code == 401


def test_public_paths_do_not_require_authentication(clerk_env):
    # Health is reachable without auth (its own readiness logic may still report
    # 503); the point is it is never blocked with 401.
    assert clerk_env.client.get("/api/health").status_code != 401


def test_invalid_token_is_rejected(clerk_env):
    resp = clerk_env.client.get("/api/projects", headers=_bearer("bad-token"))
    assert resp.status_code == 401


def test_projects_are_scoped_to_the_clerk_user(clerk_env):
    _seed_project(clerk_env, "alice-project", "user_alice")
    _seed_project(clerk_env, "bob-project", "user_bob")

    alice = clerk_env.client.get("/api/projects", headers=_bearer("alice"))
    assert [p["id"] for p in alice.json()] == ["alice-project"]

    bob = clerk_env.client.get("/api/projects", headers=_bearer("bob"))
    assert [p["id"] for p in bob.json()] == ["bob-project"]


def test_cannot_read_another_users_project(clerk_env):
    _seed_project(clerk_env, "alice-project", "user_alice")
    resp = clerk_env.client.get("/api/projects/alice-project", headers=_bearer("bob"))
    assert resp.status_code == 404


def test_media_token_authorizes_url_loaded_media(clerk_env):
    _seed_project(clerk_env, "alice-project", "user_alice")

    token = clerk_env.client.get("/api/media-token", headers=_bearer("alice")).json()["token"]

    # The media token authorizes the browser-loaded media URL without a header.
    owner = clerk_env.client.get(f"/api/media/original/alice-project?mt={token}")
    assert owner.status_code == 200

    # No credential at all is rejected.
    assert clerk_env.client.get("/api/media/original/alice-project").status_code == 401


def test_media_token_is_scoped_to_its_owner(clerk_env):
    _seed_project(clerk_env, "alice-project", "user_alice")
    bob_token = clerk_env.client.get("/api/media-token", headers=_bearer("bob")).json()["token"]

    # Bob's media token cannot reach Alice's project.
    resp = clerk_env.client.get(f"/api/media/original/alice-project?mt={bob_token}")
    assert resp.status_code == 404
