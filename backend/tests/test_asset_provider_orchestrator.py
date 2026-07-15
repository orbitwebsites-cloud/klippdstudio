import io
import asyncio
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from asset_pack_manager import AssetPackManager, AssetPolicyError
from asset_provider_orchestrator import AssetProviderOrchestrator, CATALOG, rank_pack_assets


def _png_bytes():
    output = io.BytesIO()
    Image.new("RGBA", (16, 16), (1, 2, 3, 255)).save(output, "PNG")
    return output.getvalue()


def _zip(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return output.getvalue()


class MockKenney:
    def __init__(self, archive, page_final="https://kenney.nl/assets/ui-pack", archive_final="https://kenney.nl/media/ui-pack.zip"):
        self.archive = archive
        self.page_final = page_final
        self.archive_final = archive_final
        self.calls = 0

    async def __call__(self, url, max_bytes):
        self.calls += 1
        if "/assets/" in url:
            return self.page_final, b'<a href="https://kenney.nl/media/ui-pack.zip">Donate-text download</a>', {"content-type": "text/html"}
        return self.archive_final, self.archive, {"content-type": "application/zip"}


def test_mocked_real_provider_full_pack_and_second_run_zero_network(tmp_path):
    archive = _zip({"License.txt": b"Creative Commons Zero CC0 1.0", "UI/button.png": _png_bytes()})
    fetch = MockKenney(archive)
    manager = AssetPackManager(tmp_path / "library", tmp_path / "quarantine")
    resolver = AssetProviderOrchestrator(manager, fetch)
    first = asyncio.run(resolver.install_kenney_pack(CATALOG["gaming"][0]))
    assert first and first[0]["provenance"]["canonical_url"] == "https://kenney.nl/assets/ui-pack"
    assert first[0]["provenance"]["commercial_use"] is True
    assert first[0]["provenance"]["modification_allowed"] is True
    assert first[0]["attribution"] == "Kenney - CC0-1.0"
    assert fetch.calls == 2
    second = asyncio.run(resolver.install_kenney_pack(CATALOG["gaming"][0]))
    assert second and fetch.calls == 2
    assert resolver.status()["asset_count"] == 1


def test_resolve_installs_and_aggregates_all_three_gaming_packs(tmp_path):
    archive = _zip({"License.txt": b"CC0 1.0", "asset.png": _png_bytes()})
    calls = []
    async def fetch(url, max_bytes):
        calls.append(url)
        if "/assets/" in url:
            slug = url.rstrip("/").split("/")[-1]
            return url, f'<a href="https://kenney.nl/media/{slug}.zip">download</a>'.encode(), {}
        return url, archive, {}
    resolver = AssetProviderOrchestrator(AssetPackManager(tmp_path / "library"), fetch)
    result = asyncio.run(resolver.resolve("gaming"))
    assert result["source"] == "approved_provider_full_pack"
    assert len(result["pack_ids"]) == 3
    assert result["counts"] == {"cache_packs": 0, "provider_packs": 3, "total_packs": 3}
    assert len(result["assets"]) == 3
    first_calls = len(calls)
    cached = asyncio.run(resolver.resolve("gaming"))
    assert cached["source"] == "semantic_cache"
    assert cached["counts"] == {"cache_packs": 3, "provider_packs": 0, "total_packs": 3}
    assert len(calls) == first_calls


def test_official_pack_can_include_more_metadata_members_than_publishable_media(tmp_path):
    files = {f"metadata/item-{index}.txt": b"metadata" for index in range(1201)}
    files.update({"License.txt": b"Creative Commons Zero CC0 1.0", "UI/button.png": _png_bytes()})
    resolver = AssetProviderOrchestrator(
        AssetPackManager(tmp_path / "library", tmp_path / "quarantine"),
        MockKenney(_zip(files)),
    )
    records = asyncio.run(resolver.install_kenney_pack(CATALOG["gaming"][0]))
    assert len(records) == 1


def test_provider_bad_license_and_final_host_fail_closed(tmp_path):
    bad_license = MockKenney(_zip({"License.txt": b"all rights reserved", "button.png": _png_bytes()}))
    resolver = AssetProviderOrchestrator(AssetPackManager(tmp_path / "one"), bad_license)
    with pytest.raises(AssetPolicyError, match="CC0 license"):
        asyncio.run(resolver.install_kenney_pack(CATALOG["gaming"][0]))
    assert list(resolver.manager.published_records()) == []

    evil = MockKenney(_zip({"License.txt": b"CC0", "button.png": _png_bytes()}), page_final="https://evil.example/assets/ui-pack")
    resolver = AssetProviderOrchestrator(AssetPackManager(tmp_path / "two"), evil)
    with pytest.raises(AssetPolicyError, match="redirected outside"):
        asyncio.run(resolver.install_kenney_pack(CATALOG["gaming"][0]))


def test_provider_traversal_and_bad_member_roll_back_whole_pack(tmp_path):
    traversal = MockKenney(_zip({"License.txt": b"CC0", "../escape.png": _png_bytes()}))
    resolver = AssetProviderOrchestrator(AssetPackManager(tmp_path / "one"), traversal)
    with pytest.raises(AssetPolicyError, match="traversal"):
        asyncio.run(resolver.install_kenney_pack(CATALOG["gaming"][0]))

    bad = MockKenney(_zip({"License.txt": b"CC0", "good.png": _png_bytes(), "bad.png": b"not-png"}))
    resolver = AssetProviderOrchestrator(AssetPackManager(tmp_path / "two"), bad)
    with pytest.raises(AssetPolicyError, match="magic"):
        asyncio.run(resolver.install_kenney_pack(CATALOG["gaming"][0]))
    assert list(resolver.manager.published_records()) == []

    bomb = MockKenney(_zip({"License.txt": b"CC0", "huge.png": b"0" * (2 * 1024 * 1024)}))
    resolver = AssetProviderOrchestrator(AssetPackManager(tmp_path / "three"), bomb)
    with pytest.raises(AssetPolicyError, match="compression ratio"):
        asyncio.run(resolver.install_kenney_pack(CATALOG["gaming"][0]))


def test_provider_failure_orders_to_generated_fallback(tmp_path):
    async def failing_fetch(url, max_bytes):
        raise AssetPolicyError("offline")
    resolver = AssetProviderOrchestrator(AssetPackManager(tmp_path / "library"), failing_fetch)
    result = asyncio.run(resolver.resolve("gaming", ["ui"]))
    assert result["source"] == "generated_editorial_fallback"
    assert result["assets"] == []


def test_semantic_matching_uses_only_relevant_visual_assets():
    records = [
        {"name": "a.png", "original_name": "health_bar_green.png", "mime_type": "image/png", "tags": ["health", "ui"]},
        {"name": "b.png", "original_name": "generic_panel.png", "mime_type": "image/png", "tags": ["panel", "ui"]},
        {"name": "c.ogg", "original_name": "click.ogg", "mime_type": "audio/ogg", "tags": ["health"]},
    ]
    matches = rank_pack_assets(records, "low health bar reveal")
    assert [record["name"] for record in matches] == ["a.png"]
    assert matches[0]["matched_terms"] == ["bar", "health"]
    assert rank_pack_assets(records, "dragon gameplay proof") == []


def test_provider_source_rights_override_conflicting_member_claim(tmp_path):
    manager = AssetPackManager(tmp_path / "library")
    source = tmp_path / "button.png"
    source.write_bytes(_png_bytes())
    decision = {
        "status": "approved", "provider_id": "kenney", "provider_asset_id": "ui-pack",
        "canonical_url": "https://kenney.nl/assets/ui-pack", "author": "Kenney",
        "retrieved_at": "2026-01-01T00:00:00+00:00", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "license_version": "CC0-1.0", "terms_snapshot": "abc", "commercial_use": True,
        "modification_allowed": True, "rights_status": "cc0_vendored_approved",
    }
    with pytest.raises(AssetPolicyError, match="rights conflict"):
        manager.publish_pack_transaction(
            "gaming-kenney-ui-pack", [(source, {"mime_type": "image/png", "rights_status": "user_owned_attested"})],
            decision, {"max_files": 2, "max_file_bytes": 100000, "max_total_bytes": 100000},
        )
