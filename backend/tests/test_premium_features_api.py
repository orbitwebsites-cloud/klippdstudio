import asyncio

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from local_store import LocalDatabase
from premium_features import register_premium_routes


def _client(tmp_path):
    db = LocalDatabase(tmp_path / "premium.json")
    project = {
        "id": "owned-project", "user_id": "user", "duration": 60,
        "transcript": {"words": [{"word": f"w{i}", "start": i, "end": i + 0.5} for i in range(20)]},
        "analysis": {
            "filler_indices": [2, 7], "emphasis_indices": [10],
            "story_beats": [{"word_index": 10, "beat_type": "reveal"}],
            "transitions": [{"word_index": 10, "type": "hard_cut"}],
            "audio_cues": [], "broll_moments": [],
        },
        "render_options": {},
    }
    asyncio.run(db.projects.insert_one(project))

    async def get_project(pid):
        value = await db.projects.find_one({"id": pid}, {"_id": 0})
        if not value:
            raise HTTPException(404, "Project not found")
        return value

    async def update_project(pid, **fields):
        await db.projects.update_one({"id": pid}, {"$set": fields})

    app, api = FastAPI(), APIRouter(prefix="/api")
    register_premium_routes(api, db, "user", get_project, update_project)
    app.include_router(api)
    return TestClient(app), db


def test_creator_dna_owned_project_analysis_and_list(tmp_path):
    client, _ = _client(tmp_path)
    response = client.post("/api/creator-profiles/analyze", json={
        "name": "My pacing", "rights_attested": True,
        "references": [{"type": "owned_upload", "value": "owned-project"}],
    })
    assert response.status_code == 200, response.text
    profile = response.json()["profile"]
    assert profile["id"].startswith("dna_")
    # The fixture has event evidence but no analyzer confidence measurement.
    assert profile["confidence"] == 0
    assert profile["safety"]["identity_cloning"] is False
    listed = client.get("/api/creator-profiles").json()["profiles"]
    assert [item["id"] for item in listed] == [profile["id"]]


def test_creator_dna_accepts_ui_owned_project_reference_type(tmp_path):
    client, _ = _client(tmp_path)
    response = client.post("/api/creator-profiles/analyze", json={
        "name": "UI project", "rights_attested": True,
        "references": [{"type": "owned_project", "value": "owned-project"}],
    })
    assert response.status_code == 200, response.text
    assert response.json()["profile"]["source_provenance"][0]["asset_id"] == "owned-project"


def test_creator_dna_does_not_fake_url_analysis(tmp_path):
    client, _ = _client(tmp_path)
    response = client.post("/api/creator-profiles/analyze", json={
        "name": "Unknown URL", "rights_attested": True,
        "references": [{"type": "url", "value": "https://example.com/video"}],
    })
    assert response.status_code == 422
    assert "never scored without evidence" in response.json()["detail"]


def test_creator_dna_rejects_client_supplied_observations(tmp_path):
    client, _ = _client(tmp_path)
    response = client.post("/api/creator-profiles/analyze", json={
        "name": "Untrusted", "rights_attested": True,
        "references": [{"type": "url", "value": "https://example.com/video"}],
        "observations": [{"source_id": "source_1", "duration_seconds": 60, "observation_confidence": 1, "evidence_count": 99}],
    })
    assert response.status_code == 422
    assert any(item["type"] == "extra_forbidden" for item in response.json()["detail"])


def test_creator_dna_converts_internal_schema_validation_to_422(tmp_path):
    client, _ = _client(tmp_path)
    response = client.post("/api/creator-profiles/analyze", json={
        "name": "Bad source", "rights_attested": True,
        "references": [{"type": "url", "value": "not-an-absolute-url"}],
    })
    assert response.status_code == 422
    assert any("absolute http" in item["msg"] for item in response.json()["detail"])


def test_edit_chat_preview_apply_undo_redo(tmp_path):
    client, _ = _client(tmp_path)
    preview = client.post("/api/projects/owned-project/edit-chat/preview", json={
        "message": "Make the first 10 seconds faster",
    })
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert payload["operations"][0]["summary"] == "Adjust hook pacing"

    applied = client.post("/api/projects/owned-project/edit-chat/apply", json={"preview_id": payload["preview_id"]})
    assert applied.status_code == 200, applied.text
    assert applied.json()["can_undo"] is True
    history = client.get("/api/projects/owned-project/edit-chat/history").json()
    assert history["can_undo"] is True
    assert len(history["messages"]) == 2

    undone = client.post("/api/projects/owned-project/edit-chat/undo")
    assert undone.status_code == 200
    assert undone.json() == {**undone.json(), "can_undo": False, "can_redo": True}
    redone = client.post("/api/projects/owned-project/edit-chat/redo")
    assert redone.status_code == 200
    assert redone.json()["can_undo"] is True
    assert redone.json()["can_redo"] is False


def test_edit_chat_rejects_unsupported_request_without_mutation(tmp_path):
    client, _ = _client(tmp_path)
    response = client.post("/api/projects/owned-project/edit-chat/preview", json={"message": "Do magic"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_request"
    assert client.get("/api/projects/owned-project/edit-chat/history").json()["can_undo"] is False


def test_undo_refuses_to_clobber_newer_project_changes(tmp_path):
    client, db = _client(tmp_path)
    preview = client.post("/api/projects/owned-project/edit-chat/preview", json={
        "message": "Make the first 10 seconds faster",
    }).json()
    assert client.post("/api/projects/owned-project/edit-chat/apply", json={"preview_id": preview["preview_id"]}).status_code == 200
    project = asyncio.run(db.projects.find_one({"id": "owned-project"}))
    changed = dict(project["analysis"])
    changed["manual_marker"] = "newer-work"
    asyncio.run(db.projects.update_one({"id": "owned-project"}, {"$set": {"analysis": changed}}))
    response = client.post("/api/projects/owned-project/edit-chat/undo")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "project_changed"
    saved = asyncio.run(db.projects.find_one({"id": "owned-project"}))
    assert saved["analysis"]["manual_marker"] == "newer-work"
