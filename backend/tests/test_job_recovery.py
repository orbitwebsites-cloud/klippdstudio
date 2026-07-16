from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from local_store import LocalDatabase
import server


class CapturedBackgroundTasks:
    def __init__(self):
        self.calls = []

    def add_task(self, function, *args, **kwargs):
        self.calls.append((function, args, kwargs))


def _iso(offset_seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def test_startup_recovers_persisted_stuck_analysis_and_render_states(tmp_path, monkeypatch):
    path = tmp_path / "jobs.json"
    persisted = LocalDatabase(path)
    statuses = [
        "queued",
        "extracting_audio",
        "transcribing",
        "analyzing",
        "queued_render",
        "rendering",
    ]
    for index, status in enumerate(statuses):
        document = {
            "id": f"stale-{index}",
            "user_id": server.USER_ID,
            "status": status,
            "job_lease_kind": "render" if status in server.RENDER_ACTIVE_STATUSES else "analysis",
        }
        if index:
            document.update({
                "job_lease_id": f"old-{index}",
                "job_lease_expires_at": _iso(-60),
            })
        asyncio.run(persisted.projects.insert_one(document))
    asyncio.run(persisted.projects.insert_one({
        "id": "fresh",
        "user_id": server.USER_ID,
        "status": "rendering",
        "job_lease_id": "live-worker",
        "job_lease_kind": "render",
        "job_lease_expires_at": _iso(600),
    }))
    asyncio.run(persisted.projects.insert_one({
        "id": "fresh-legacy",
        "user_id": server.USER_ID,
        "status": "analyzing",
        "updated_at": _iso(0),
    }))

    restarted = LocalDatabase(path)
    monkeypatch.setattr(server, "db", restarted)

    assert asyncio.run(server.recover_stale_jobs()) == len(statuses)

    reloaded = LocalDatabase(path)
    for index in range(len(statuses)):
        recovered = asyncio.run(reloaded.projects.find_one({"id": f"stale-{index}"}))
        assert recovered["status"] == "error"
        assert recovered["job_recovery_reason"] == "stale_lease"
        assert recovered["job_lease_id"] is None
        assert "interrupted" in recovered["status_message"]

    fresh = asyncio.run(reloaded.projects.find_one({"id": "fresh"}))
    assert fresh["status"] == "rendering"
    assert fresh["job_lease_id"] == "live-worker"
    fresh_legacy = asyncio.run(reloaded.projects.find_one({"id": "fresh-legacy"}))
    assert fresh_legacy["status"] == "analyzing"
    assert "job_recovery_reason" not in fresh_legacy


@pytest.mark.parametrize("kind", ["analysis", "render"])
def test_stale_persisted_job_is_requeued_once_with_a_new_lease(kind, tmp_path, monkeypatch):
    database = LocalDatabase(tmp_path / f"{kind}.json")
    project = {
        "id": f"stuck-{kind}",
        "user_id": server.USER_ID,
        "status": "analyzing" if kind == "analysis" else "queued_render",
        "job_lease_id": "crashed-worker",
        "job_lease_kind": kind,
        "job_lease_expires_at": _iso(-60),
        "duration": 10.0,
    }
    if kind == "render":
        project["transcript"] = {"words": [{"word": "test", "start": 0.0, "end": 0.5}]}
    asyncio.run(database.projects.insert_one(project))
    monkeypatch.setattr(server, "db", LocalDatabase(database.path))

    first_tasks = CapturedBackgroundTasks()
    if kind == "analysis":
        response = asyncio.run(server.analyze(project["id"], first_tasks, server.AnalyzeBody()))
        expected_status = "queued"
        expected_function = server._run_analysis
    else:
        response = asyncio.run(server.render(project["id"], server.RenderOptions(), first_tasks))
        expected_status = "queued_render"
        expected_function = server._run_render

    assert response == {"ok": True, "status": expected_status}
    assert len(first_tasks.calls) == 1
    assert first_tasks.calls[0][0] is expected_function

    queued = asyncio.run(server.db.projects.find_one({"id": project["id"]}))
    new_lease = queued["job_lease_id"]
    assert queued["status"] == expected_status
    assert queued["job_lease_kind"] == kind
    assert new_lease and new_lease != "crashed-worker"
    assert server._parse_project_expiry(queued["job_lease_expires_at"]) > datetime.now(timezone.utc)
    assert first_tasks.calls[0][1][-1] == new_lease

    second_tasks = CapturedBackgroundTasks()
    if kind == "analysis":
        duplicate = asyncio.run(server.analyze(project["id"], second_tasks, server.AnalyzeBody()))
    else:
        duplicate = asyncio.run(server.render(project["id"], server.RenderOptions(), second_tasks))

    assert duplicate == {"ok": True, "status": expected_status, "already_running": True}
    assert second_tasks.calls == []


def test_superseded_worker_cannot_commit_after_stale_recovery(tmp_path, monkeypatch):
    database = LocalDatabase(tmp_path / "superseded.json")
    asyncio.run(database.projects.insert_one({
        "id": "superseded",
        "status": "rendering",
        "job_lease_id": "old-lease",
        "job_lease_kind": "render",
        "job_lease_expires_at": _iso(-60),
        "transcript": {"words": []},
        "duration": 1.0,
    }))
    monkeypatch.setattr(server, "db", database)

    tasks = CapturedBackgroundTasks()
    asyncio.run(server.render("superseded", server.RenderOptions(), tasks))

    with pytest.raises(server.JobLeaseLost):
        asyncio.run(server._update_leased_project(
            "superseded", "old-lease", terminal=True, status="done", progress=100,
        ))

    current = asyncio.run(database.projects.find_one({"id": "superseded"}))
    assert current["status"] == "queued_render"
    assert current["job_lease_id"] != "old-lease"
