"""FastAPI integration tests for asset rights and render authorization."""
from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import asset_pack_manager
from asset_pack_manager import AssetPackManager, sha256_file
from local_store import LocalDatabase
import server


ATTESTATION = "I own or have commercial rights to this asset"


def _png_bytes(color=(32, 96, 180, 255)) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (24, 24), color).save(output, "PNG")
    return output.getvalue()


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    library_dir = data_dir / "library"
    quarantine_dir = data_dir / "quarantine"
    for name in ("videos", "audio", "output", "subtitles", "broll", "library"):
        (data_dir / name).mkdir(parents=True, exist_ok=True)
    manager = AssetPackManager(library_dir, quarantine_dir)
    database = LocalDatabase(data_dir / "test-db.json")
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "LIBRARY_DIR", library_dir)
    monkeypatch.setattr(server, "ASSET_QUARANTINE_DIR", quarantine_dir)
    monkeypatch.setattr(server, "ASSET_MANAGER", manager)
    monkeypatch.setattr(server, "db", database)
    monkeypatch.setattr(server, "APP_ACCESS_TOKEN", "")
    with TestClient(server.app) as client:
        yield SimpleNamespace(
            client=client,
            data_dir=data_dir,
            library_dir=library_dir,
            quarantine_dir=quarantine_dir,
            manager=manager,
            db=database,
        )


def test_library_unknown_rights_are_quarantined_and_never_listed(api_env):
    response = api_env.client.post(
        "/api/library/upload",
        files={"file": ("unknown.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "status": "quarantined",
        "reason": "unknown_or_unapproved_rights",
        "rights_proof_required": True,
    }
    assert api_env.client.get("/api/library").json() == {"items": []}
    assert list(api_env.manager.published_records()) == []
    markers = list((api_env.quarantine_dir / "denied" / "user_owned_gaming").glob("*.json"))
    assert len(markers) == 1


def test_library_explicit_attestation_publishes_and_can_be_served(api_env):
    response = api_env.client.post(
        "/api/library/upload",
        data={"rights_status": "user_owned_attested", "rights_attestation": ATTESTATION},
        files={"file": ("my overlay.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 200
    published = response.json()
    assert published["ok"] is True
    assert published["sha256"] == sha256_file(api_env.library_dir / published["name"])

    listing = api_env.client.get("/api/library").json()["items"]
    assert len(listing) == 1
    assert listing[0]["rights_status"] == "user_owned_attested"
    assert listing[0]["license_id"] == "user-attestation"
    preview = api_env.client.get(f"/api/library/file/{published['name']}")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/png")


def test_broll_upload_requires_the_same_explicit_rights_contract(api_env, monkeypatch):
    asyncio.run(api_env.db.projects.insert_one({"id": "project-1"}))
    monkeypatch.setattr(server.vp, "probe_video", lambda _path: {"duration": 2.5, "width": 1280, "height": 720})
    original_validate = asset_pack_manager.validate_media

    def validate_test_media(path: Path, declared_mime: str):
        if path.suffix.lower() == ".mp4" and declared_mime == "video/mp4":
            assert path.read_bytes()[4:8] == b"ftyp"
            return ".mp4"
        return original_validate(path, declared_mime)

    monkeypatch.setattr(asset_pack_manager, "validate_media", validate_test_media)
    fake_mp4 = b"\x00\x00\x00\x18ftypisom" + b"test-media" * 32

    denied = api_env.client.post(
        "/api/projects/project-1/broll_upload",
        files={"file": ("clip.mp4", fake_mp4, "video/mp4")},
    )
    assert denied.status_code == 200
    assert denied.json()["status"] == "quarantined"

    accepted = api_env.client.post(
        "/api/projects/project-1/broll_upload",
        data={"rights_status": "user_owned_attested", "rights_attestation": ATTESTATION},
        files={"file": ("clip.mp4", fake_mp4, "video/mp4")},
    )
    assert accepted.status_code == 200
    assert accepted.json()["ok"] is True
    body = accepted.json()
    assert body["rights_status"] == "user_owned_attested"
    assert body["license_id"] == "user-attestation"
    assert api_env.manager.resolve_renderable(body["local_path"]) == Path(body["local_path"])


def test_asset_pack_status_and_resolve_preserve_orchestrator_response_shape(api_env, monkeypatch):
    class StubOrchestrator:
        def status(self):
            return {
                "status": "ready",
                "asset_count": 3,
                "cache": {"pack_count": 1, "asset_count": 2},
                "provider": ["kenney"],
                "generated": 1,
                "catalog": {"gaming": []},
                "last_status": {},
            }

        async def resolve(self, niche, tags):
            return {
                "source": "semantic_approved_user",
                "pack_id": None,
                "pack_ids": [],
                "assets": [{"name": "health.png", "tags": ["health", "ui"]}],
                "counts": {"cache_packs": 0, "provider_packs": 0, "total_packs": 0},
                "requested": {"niche": niche, "tags": tags},
            }

    monkeypatch.setattr(server, "ASSET_ORCHESTRATOR", StubOrchestrator())
    status = api_env.client.get("/api/asset-packs/status")
    assert status.status_code == 200
    assert status.json()["cache"] == {"pack_count": 1, "asset_count": 2}

    resolved = api_env.client.post("/api/asset-packs/resolve?niche=gaming&tags=health,ui")
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["source"] == "semantic_approved_user"
    assert body["assets"][0]["name"] == "health.png"
    assert body["requested"] == {"niche": "gaming", "tags": ["health", "ui"]}

    resolved_body = api_env.client.post("/api/asset-packs/resolve", json={"niche": "minecraft", "tags": ["inventory", "hud"]})
    assert resolved_body.status_code == 200
    assert resolved_body.json()["requested"] == {"niche": "minecraft", "tags": ["inventory", "hud"]}


def test_checkout_rejects_existing_active_subscription(api_env, monkeypatch):
    monkeypatch.setattr(server, "STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro")
    asyncio.run(api_env.db.settings.insert_one({
        "user_id": server.USER_ID,
        "billing_subscription": {"customer_id": "cus_123", "status": "active", "plan": "basic"},
    }))

    response = api_env.client.post("/api/billing/checkout", json={"plan": "pro"})

    assert response.status_code == 409
    assert "already has an active Stripe subscription" in response.json()["detail"]


def test_training_url_canonicalization_preserves_semantic_query_params():
    first = server._canonical_training_url("https://example.com/video?id=1&utm_source=newsletter")
    second = server._canonical_training_url("https://example.com/video?id=2&utm_source=newsletter")

    assert first == "https://example.com/video?id=1"
    assert second == "https://example.com/video?id=2"
    assert first != second


def test_training_url_canonicalization_preserves_youtube_semantic_query_params():
    first = server._canonical_training_url("https://www.youtube.com/watch?v=abc123&t=12s&utm_source=x")
    second = server._canonical_training_url("https://youtu.be/abc123?start=30&list=playlist&utm_medium=y")

    assert first == "https://www.youtube.com/watch?t=12s&v=abc123"
    assert second == "https://www.youtube.com/watch?list=playlist&start=30&v=abc123"
    assert first != second


def test_analysis_uses_semantic_pack_match_and_generates_only_the_unmatched_request(api_env, monkeypatch):
    incoming = api_env.quarantine_dir / "incoming" / "semantic"
    incoming.mkdir(parents=True)
    source = incoming / "health-bar.png"
    source.write_bytes(_png_bytes((0, 220, 80, 255)))
    record = api_env.manager.ingest_file(
        incoming,
        {
            "asset_id": "health-bar",
            "source_id": "user_owned_gaming",
            "relative_path": source.name,
            "sha256": sha256_file(source),
            "mime_type": "image/png",
            "rights_status": "user_owned_attested",
            "license_id": "user-attestation",
            "provenance": "direct_user_upload",
            "niche": "gaming",
            "is_evidence": True,
            "tags": ["health", "bar", "ui"],
        },
        {"max_files": 10, "max_file_bytes": 1024 * 1024, "max_total_bytes": 10 * 1024 * 1024},
        audit_sample_rate=0,
    )
    original = api_env.data_dir / "videos" / "analysis.mp4"
    original.write_bytes(b"source")
    asyncio.run(api_env.db.projects.insert_one({
        "id": "project-analysis", "status": "uploaded", "original_path": str(original),
        "duration": 4.0, "width": 1920, "height": 1080,
    }))

    async def fake_keys():
        return {"groq": "test-key"}

    async def fake_transcribe(_audio_path, _key):
        return {"words": [
            {"word": "health", "start": 0.0, "end": 0.4},
            {"word": "bar", "start": 0.4, "end": 0.8},
            {"word": "victory", "start": 1.0, "end": 1.5},
        ]}

    async def fake_analyze(_words, _keys, profile=None):
        assert profile is None
        return {
            "quality_review": {"passed": True, "profile": "gaming"},
            "filler_indices": [],
            "emphasis_indices": [],
            "broll_moments": [
                {"word_index": 1, "query": "health bar", "visual_intent": "show low health UI"},
                {"word_index": 2, "query": "victory quote", "visual_intent": "show the payoff"},
            ],
            "asset_requests": [
                {"word_index": 1, "kind": "title_card", "text": "Low health", "reason": "Proof"},
                {"word_index": 2, "kind": "quote_card", "text": "Victory", "reason": "Payoff"},
            ],
        }

    class SemanticOrchestrator:
        async def resolve(self, niche, tags):
            assert niche == "gaming"
            assert tags == ["health bar", "victory quote"]
            return {
                "source": "semantic_approved_user", "pack_id": None, "pack_ids": [],
                "counts": {"cache_packs": 0, "provider_packs": 0, "total_packs": 0},
                "assets": [{**record, "original_name": "health-bar.png"}],
            }

    monkeypatch.setattr(server, "get_keys", fake_keys)
    monkeypatch.setattr(server.vp, "extract_audio", lambda _source, destination: Path(destination).write_bytes(b"audio"))
    monkeypatch.setattr(server.ai, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(server.ai, "analyze_transcript", fake_analyze)
    monkeypatch.setattr(server, "ASSET_ORCHESTRATOR", SemanticOrchestrator())

    asyncio.run(server._run_analysis("project-analysis"))
    project = asyncio.run(api_env.db.projects.find_one({"id": "project-analysis"}))
    assert project["status"] == "ready"
    analysis = project["analysis"]
    assert [asset["word_index"] for asset in analysis["resolved_pack_assets"]] == [1]
    assert analysis["resolved_pack_assets"][0]["matched_terms"] == ["bar", "health"]
    assert [asset["word_index"] for asset in analysis["generated_assets"]] == [2]
    generated = analysis["generated_assets"][0]
    assert generated["rights_status"] == "generated_editorial"
    assert generated["is_evidence"] is False
    assert api_env.manager.resolve_renderable(generated["local_path"]) == Path(generated["local_path"])


def test_render_uses_only_checksum_registered_assets(api_env, monkeypatch):
    incoming = api_env.quarantine_dir / "incoming" / "approved"
    incoming.mkdir(parents=True)
    approved_source = incoming / "approved.png"
    approved_source.write_bytes(_png_bytes((20, 200, 90, 255)))
    record = api_env.manager.ingest_file(
        incoming,
        {
            "asset_id": "approved",
            "source_id": "user_owned_gaming",
            "relative_path": approved_source.name,
            "sha256": sha256_file(approved_source),
            "mime_type": "image/png",
            "rights_status": "user_owned_attested",
            "license_id": "user-attestation",
            "provenance": "direct_user_upload",
            "niche": "gaming",
            "is_evidence": True,
        },
        {"max_files": 10, "max_file_bytes": 1024 * 1024, "max_total_bytes": 10 * 1024 * 1024},
        audit_sample_rate=0,
    )
    approved = api_env.library_dir / record["name"]
    arbitrary_inside_data = api_env.data_dir / "broll" / "forged.png"
    arbitrary_inside_data.write_bytes(_png_bytes((220, 20, 30, 255)))
    quarantined = api_env.quarantine_dir / "denied" / "unknown" / "denied.png"
    quarantined.parent.mkdir(parents=True)
    quarantined.write_bytes(_png_bytes((70, 40, 200, 255)))
    outside = api_env.data_dir.parent / "outside.png"
    outside.write_bytes(_png_bytes())
    original = api_env.data_dir / "videos" / "project.mp4"
    original.write_bytes(b"source")
    asyncio.run(api_env.db.projects.insert_one({
        "id": "project-render",
        "status": "analyzed",
        "original_path": str(original),
        "duration": 2.0,
        "width": 1920,
        "height": 1080,
        "transcript": {"words": [{"word": "win", "start": 0.1, "end": 0.5}]},
        "analysis": {"filler_indices": []},
    }))

    captured = {}
    monkeypatch.setattr(
        server.vp,
        "build_keep_segments",
        lambda *_args, **_kwargs: [{"start": 0.0, "end": 2.0}],
    )
    monkeypatch.setattr(server.vp, "cut_and_concat", lambda *_args: Path(_args[2]).write_bytes(b"cut"))

    def fake_render(_cut, _ass, _sfx, broll_events, _sfx_dir, output, *_args):
        captured["events"] = broll_events
        Path(output).write_bytes(b"rendered" * 200)

    monkeypatch.setattr(server.vp, "render_final", fake_render)
    monkeypatch.setattr(server.vp, "probe_video", lambda path: {
        "duration": 2.0, "width": 1920, "height": 1080, "size": Path(path).stat().st_size,
    })
    selections = [
        {"video_url": f"file://{approved}", "local_path": str(approved), "word_index": 0},
        {"video_url": f"file://{arbitrary_inside_data}", "local_path": str(arbitrary_inside_data), "word_index": 0},
        {"video_url": f"file://{quarantined}", "local_path": str(quarantined), "word_index": 0},
        {"video_url": f"file://{outside}", "local_path": str(outside), "word_index": 0},
        {"video_url": "https://evil.example/asset.mp4", "word_index": 0},
    ]
    options = server.RenderOptions(captions=False, sfx=False, selected_broll=selections)
    asyncio.run(server._run_render("project-render", options))

    assert [event["local_path"] for event in captured["events"]] == [str(approved)]
    project = asyncio.run(api_env.db.projects.find_one({"id": "project-render"}))
    assert project["status"] == "done"
