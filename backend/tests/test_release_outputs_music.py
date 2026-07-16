from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from local_store import LocalDatabase
import server


ATTESTATION = "I own or have commercial rights to this asset"


@pytest.fixture
def release_api(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    for name in ("videos", "audio", "output", "subtitles", "broll", "music", "library"):
        (data_dir / name).mkdir(parents=True, exist_ok=True)
    database = LocalDatabase(data_dir / "release-db.json")
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "db", database)
    monkeypatch.setattr(server, "APP_ACCESS_TOKEN", "")
    with TestClient(server.app) as client:
        yield SimpleNamespace(client=client, data_dir=data_dir, db=database)


def test_range_render_is_surfaced_and_downloadable(release_api, monkeypatch):
    source = release_api.data_dir / "videos" / "range-source.mp4"
    source.write_bytes(b"source-video")
    asyncio.run(release_api.db.projects.insert_one({
        "id": "range-project",
        "name": "Range demo.mp4",
        "status": "ready",
        "original_path": str(source),
        "duration": 30.0,
        "width": 1920,
        "height": 1080,
        "transcript": {"words": [{"word": "demo", "start": 5.0, "end": 6.0}]},
        "analysis": {"filler_indices": []},
    }))

    monkeypatch.setattr(
        server.vp,
        "build_keep_segments",
        lambda *_args, **_kwargs: [{"start": 0.0, "end": 30.0}],
    )
    monkeypatch.setattr(
        server.vp,
        "cut_and_concat",
        lambda _source, _keep, output, *_args: Path(output).write_bytes(b"cut-video"),
    )

    def fake_render(_cut, _ass, _sfx, _broll, _sfx_dir, output, *_args):
        Path(output).write_bytes(b"focused-render" * 100)

    monkeypatch.setattr(server.vp, "render_final", fake_render)
    monkeypatch.setattr(server.vp, "probe_video", lambda path: {
        "duration": 7.0,
        "width": 1920,
        "height": 1080,
        "size": Path(path).stat().st_size,
    })
    monkeypatch.setattr(
        server.post_render_qa,
        "review_render",
        lambda *_args, **_kwargs: {"passed": True, "issues": []},
    )

    response = release_api.client.post("/api/projects/range-project/render", json={
        "captions": False,
        "sfx": False,
        "broll": False,
        "clip_start": 5.0,
        "clip_end": 12.0,
        "clip_label": "range_5_12s",
    })
    assert response.status_code == 200, response.text

    project = release_api.client.get("/api/projects/range-project").json()
    assert project["status"] == "done"
    assert project["focused_render"] == {
        "label": "range_5_12s",
        "start": 5.0,
        "end": 12.0,
        "completed_at": project["focused_render"]["completed_at"],
    }
    assert project["viral_renders"]["range_5_12s"].endswith("range-project_clip_range_5_12s.mp4")

    download = release_api.client.get(
        "/api/projects/range-project/download",
        params={"clip": project["focused_render"]["label"]},
    )
    assert download.status_code == 200
    assert download.content == b"focused-render" * 100
    assert "Range demo_range_5_12s.mp4" in unquote(download.headers["content-disposition"])


def test_music_upload_reload_detach_and_cleanup(release_api, monkeypatch):
    asyncio.run(release_api.db.projects.insert_one({
        "id": "music-project",
        "name": "Music demo",
        "status": "ready",
        "edit_options": {"captions": True, "background_music": False},
    }))
    monkeypatch.setattr(
        server.vp,
        "probe_audio",
        lambda _path, expected_extension=None: {"duration": 9.5, "format_name": expected_extension},
    )

    uploaded = release_api.client.post(
        "/api/projects/music-project/music_upload",
        data={
            "rights_status": "user_owned_attested",
            "rights_attestation": ATTESTATION,
        },
        files={"file": ("licensed-bed.mp3", b"local-audio-content", "audio/mpeg")},
    )
    assert uploaded.status_code == 200, uploaded.text

    reloaded = release_api.client.get("/api/projects/music-project").json()
    attached_path = Path(reloaded["background_music_path"])
    assert reloaded["background_music_name"] == "licensed-bed.mp3"
    assert reloaded["edit_options"]["background_music"] is True
    assert reloaded["edit_options"]["captions"] is True
    assert attached_path.is_file()

    detached = release_api.client.delete("/api/projects/music-project/music")
    assert detached.status_code == 200, detached.text
    assert detached.json()["background_music"] is False

    after_reload = release_api.client.get("/api/projects/music-project").json()
    assert after_reload["background_music_path"] is None
    assert after_reload["background_music_name"] is None
    assert after_reload["edit_options"]["background_music"] is False
    assert after_reload["edit_options"]["captions"] is True
    assert not attached_path.exists()
