"""Plan retention tests for project media cleanup."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from local_store import LocalDatabase
import server


def test_expired_basic_project_removes_record_and_media(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    video = data_dir / "videos" / "old.mp4"
    output = data_dir / "output" / "old_final.mp4"
    clip = data_dir / "output" / "old_clip_hook.mp4"
    for path in (video, output, clip):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video")

    database = LocalDatabase(data_dir / "test-db.json")
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "db", database)
    expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    asyncio.run(database.projects.insert_one({
        "id": "old", "user_id": server.USER_ID, "subscription_plan": "basic",
        "expires_at": expired_at, "original_path": str(video), "output_path": str(output),
        "viral_renders": {"hook": str(clip)},
    }))

    assert asyncio.run(server.purge_expired_projects()) == 1
    assert asyncio.run(database.projects.find_one({"id": "old"})) is None
    assert not video.exists()
    assert not output.exists()
    assert not clip.exists()


def test_plan_retention_policy(monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_PLAN", "pro")
    plan, expires_at = server.project_retention()
    assert plan == "pro"
    assert expires_at is not None
    assert 29 <= (expires_at - datetime.now(timezone.utc)).days <= 30

    monkeypatch.setenv("SUBSCRIPTION_PLAN", "enterprise")
    monkeypatch.delenv("ENTERPRISE_APPROVED", raising=False)
    plan, expires_at = server.project_retention()
    assert plan == "basic"
    assert expires_at is not None
