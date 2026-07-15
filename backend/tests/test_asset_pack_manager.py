import json
import random
from pathlib import Path

import pytest
from PIL import Image

import asset_generator
import asset_pack_manager
from asset_pack_manager import (
    AssetPackManager,
    AssetPolicyError,
    contained_path,
    quarantine_denied_source,
    sha256_file,
    validate_manifest,
)


REPO = Path(__file__).resolve().parents[2]


def _png(path: Path, color=(20, 40, 60, 255)) -> Path:
    Image.new("RGBA", (32, 32), color).save(path, "PNG")
    return path


def _entry(path: Path, **overrides):
    entry = {
        "asset_id": path.stem,
        "source_id": "user_owned_gaming",
        "relative_path": path.name,
        "sha256": sha256_file(path),
        "mime_type": "image/png",
        "rights_status": "user_owned_attested",
        "license_id": "user-attestation",
        "provenance": "direct_user_upload",
        "attribution": "User supplied",
        "niche": "gaming",
        "is_evidence": True,
    }
    entry.update(overrides)
    return entry


def _budgets(**overrides):
    result = {"max_files": 10, "max_file_bytes": 1024 * 1024, "max_total_bytes": 10 * 1024 * 1024}
    result.update(overrides)
    return result


def test_gaming_manifest_validates_and_catalog_schema_supports_future_niches():
    manifest = json.loads((REPO / "training/assets/gaming_v1_manifest.json").read_text(encoding="utf-8"))
    assert validate_manifest(manifest)["pack_id"] == "gaming-v1"
    future = {**manifest, "niche": "building_in_public_talking_head", "status": "disabled"}
    assert validate_manifest(future)["status"] == "disabled"
    invalid = {**manifest, "niche": "unknown_niche"}
    with pytest.raises(AssetPolicyError, match="Unknown niche"):
        validate_manifest(invalid)


def test_drive_disclaimer_is_quarantine_only_and_downloader_has_no_gdown():
    decision = json.loads((REPO / "training/assets/sources/drive_sslixmc_editing_pack.json").read_text(encoding="utf-8"))
    tool = (REPO / "tools/download_public_drive_assets.py").read_text(encoding="utf-8")
    assert decision["status"] == "quarantine_reference_only"
    assert decision["allowed_actions"] == ["metadata_inventory", "request_rights_proof"]
    assert {"download", "preview", "embedding", "training", "render", "redistribution"} <= set(decision["denied_actions"])
    assert "import gdown" not in tool
    assert "download_folder" not in tool


def test_denied_source_marker_stays_inside_quarantine(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    marker_path = quarantine_denied_source(source, tmp_path / "quarantine", "drive_sslixmc_editing_pack")
    assert marker_path.resolve().is_relative_to((tmp_path / "quarantine/denied/drive_sslixmc_editing_pack").resolve())
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["bytes_moved"] is False
    assert marker["bytes_inspected"] is False


def test_unknown_license_quarantines_and_cannot_render(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    path = _png(source / "unknown.png")
    manager = AssetPackManager(tmp_path / "library", tmp_path / "quarantine")
    record = manager.ingest_file(source, _entry(path, rights_status="unknown", license_id="unknown"), _budgets(), audit_sample_rate=0)
    assert record["status"] == "quarantined"
    assert record["reason"] == "unknown_or_unapproved_rights"
    assert manager.resolve_renderable(path) is None
    assert (tmp_path / "quarantine/denied/user_owned_gaming" / f"{sha256_file(path)}.json").is_file()


def test_path_traversal_and_archives_are_rejected(tmp_path):
    with pytest.raises(AssetPolicyError, match="escapes"):
        contained_path(tmp_path, "../outside.png")
    source = tmp_path / "source"
    source.mkdir()
    archive = source / "pack.zip"
    archive.write_bytes(b"PK\x03\x04not-extracted")
    manager = AssetPackManager(tmp_path / "library")
    with pytest.raises(AssetPolicyError, match="Archives"):
        manager.ingest_file(source, _entry(archive, mime_type="application/zip"), _budgets(), audit_sample_rate=0)


def test_mime_magic_mismatch_is_rejected(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    path = _png(source / "image.png")
    manager = AssetPackManager(tmp_path / "library")
    with pytest.raises(AssetPolicyError, match="MIME"):
        manager.ingest_file(source, _entry(path, mime_type="image/jpeg"), _budgets(), audit_sample_rate=0)
    fake = source / "fake.png"
    fake.write_bytes(b"not a png")
    with pytest.raises(AssetPolicyError, match="magic"):
        manager.ingest_file(source, _entry(fake), _budgets(), audit_sample_rate=0)


def test_pixel_and_gif_frame_bombs_are_rejected_before_full_decode(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    large = _png(source / "large.png")
    monkeypatch.setattr(asset_pack_manager, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(AssetPolicyError, match="pixel budget"):
        asset_pack_manager.validate_media(large, "image/png")

    frames = [Image.new("RGBA", (8, 8), (index, 0, 0, 255)) for index in range(3)]
    gif = source / "animated.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:], format="GIF", duration=20)
    monkeypatch.setattr(asset_pack_manager, "MAX_IMAGE_PIXELS", 40_000_000)
    monkeypatch.setattr(asset_pack_manager, "MAX_GIF_FRAMES", 2)
    with pytest.raises(AssetPolicyError, match="frame-count"):
        asset_pack_manager.validate_media(gif, "image/gif")


def test_checksum_dedup_and_budget_fail_closed(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    first = _png(source / "first.png")
    second = source / "second.png"
    second.write_bytes(first.read_bytes())
    manager = AssetPackManager(tmp_path / "library")
    published = manager.ingest_file(source, _entry(first), _budgets(), audit_sample_rate=0)
    duplicate = manager.ingest_file(source, _entry(second), _budgets(), audit_sample_rate=0)
    assert published["status"] == "published"
    assert duplicate["deduplicated"] is True
    assert len(list(manager.published_records())) == 1

    third = _png(source / "third.png", (90, 20, 10, 255))
    with pytest.raises(AssetPolicyError, match="file-count budget"):
        manager.ingest_file(source, _entry(third), _budgets(max_files=1), audit_sample_rate=0)
    with pytest.raises(AssetPolicyError, match="per-file budget"):
        manager.ingest_file(source, _entry(third), _budgets(max_file_bytes=1), audit_sample_rate=0)


def test_randomized_partial_quarantine_is_not_publishable(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    path = _png(source / "audit.png")
    manager = AssetPackManager(tmp_path / "library", tmp_path / "quarantine")
    result = manager.ingest_file(source, _entry(path), _budgets(), audit_sample_rate=1.0, rng=random.Random(1))
    assert result["reason"] == "randomized_partial_audit"
    assert list(manager.published_records()) == []


def test_generated_fallback_has_strong_metadata_and_is_renderable(tmp_path):
    manager = AssetPackManager(tmp_path / "library", tmp_path / "quarantine")
    assets = asset_generator.generate_assets(
        [{"word_index": 1, "kind": "title_card", "text": "Round Won", "reason": "Clarify the captured payoff"}],
        "project-1", manager.library_dir, "gaming",
    )
    asset = assets[0]
    assert asset["provenance"] == "generated_editorial_graphic"
    assert asset["is_evidence"] is False
    assert asset["generator"] == "klipped_editorial_graphics"
    assert asset["generator_version"]
    assert asset["sha256"] == sha256_file(Path(asset["local_path"]))
    manager.register_generated(Path(asset["local_path"]), asset)
    assert manager.resolve_renderable(asset["local_path"]) == Path(asset["local_path"]).resolve()

    unmanifested = _png(manager.library_dir / "unmanifested.png")
    assert manager.resolve_renderable(unmanifested) is None
