"""Automatic, rights-validated niche asset provider orchestration."""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import urljoin, urlparse

import httpx

from asset_pack_manager import AssetPackManager, AssetPolicyError


KENNEY_HOSTS = {"kenney.nl", "www.kenney.nl"}
NICHE_IDS = (
    "gaming", "building_in_public_talking_head", "ai_tech_tutorial",
    "podcast_interview", "business_finance", "education_explainer",
    "fitness_wellness", "food_cooking", "travel_lifestyle",
    "product_ecommerce_review",
)
CATALOG = {niche: [] for niche in NICHE_IDS}
CATALOG["gaming"] = [
        {"provider_id": "kenney", "provider_asset_id": "ui-pack", "page_url": "https://kenney.nl/assets/ui-pack", "tags": ["gaming", "ui", "buttons", "panels"]},
        {"provider_id": "kenney", "provider_asset_id": "emotes-pack", "page_url": "https://kenney.nl/assets/emotes-pack", "tags": ["gaming", "emotes", "reactions"]},
        {"provider_id": "kenney", "provider_asset_id": "interface-sounds", "page_url": "https://kenney.nl/assets/interface-sounds", "tags": ["gaming", "audio", "ui", "sfx"]},
    ]
NICHE_PARENT = {"minecraft_narrative": "gaming"}
PACK_BUDGETS = {"max_files": 2500, "max_file_bytes": 25 * 1024 * 1024, "max_total_bytes": 350 * 1024 * 1024}
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
# Official packs can contain source/project metadata alongside the advertised
# media files. Keep the archive traversal bounded while the stricter
# PACK_BUDGETS.max_files limit still caps publishable assets at 2,500.
MAX_MEMBERS = 5000
MAX_RATIO = 150
Fetch = Callable[[str, int], Awaitable[tuple[str, bytes, Dict[str, str]]]]
SEMANTIC_STOPWORDS = {"gaming", "game", "asset", "video", "clip", "the", "and", "for", "with", "from"}


def _semantic_tokens(value: str) -> set[str]:
    return {
        token.lower() for token in re.findall(r"[A-Za-z0-9]+", value or "")
        if len(token) > 1 and token.lower() not in SEMANTIC_STOPWORDS
    }


def rank_pack_assets(records: list[Dict[str, Any]], query: str, limit: int = 6) -> list[Dict[str, Any]]:
    """Return only assets with an explainable filename/tag match."""
    need = _semantic_tokens(query)
    if not need:
        return []
    ranked = []
    for record in records:
        if not str(record.get("mime_type", "")).startswith(("image/", "video/")):
            continue
        haystack = _semantic_tokens(str(record.get("original_name", "")))
        haystack.update(_semantic_tokens(" ".join(record.get("tags", []))))
        overlap = need & haystack
        if overlap:
            ranked.append((len(overlap), sorted(overlap), record))
    ranked.sort(key=lambda item: (-item[0], str(item[2].get("original_name", "")), item[2]["name"]))
    return [{**record, "matched_terms": terms} for _, terms, record in ranked[:max(0, limit)]]


def niche_requirements(niche: str, tags: Optional[list[str]] = None) -> list[Dict[str, Any]]:
    resolved = NICHE_PARENT.get(niche, niche)
    candidates = CATALOG.get(resolved, [])
    requested = {tag.lower() for tag in (tags or [])}
    if not requested:
        return list(candidates)
    ranked = sorted(candidates, key=lambda item: -len(requested & set(item["tags"])))
    return ranked


def _validate_https_url(url: str, allowed_hosts: set[str]) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in allowed_hosts or parsed.username or parsed.password:
        raise AssetPolicyError("Provider URL failed HTTPS/host policy")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise AssetPolicyError("Provider host DNS lookup failed") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise AssetPolicyError("Provider URL resolves to a forbidden IP range")


async def secure_fetch(url: str, max_bytes: int) -> tuple[str, bytes, Dict[str, str]]:
    current = url
    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
        for _ in range(5):
            _validate_https_url(current, KENNEY_HOSTS)
            async with client.stream("GET", current, headers={"User-Agent": "KlippedStudioAssetResolver/1.0"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise AssetPolicyError("Provider redirect has no location")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                declared = int(response.headers.get("content-length") or 0)
                if declared > max_bytes:
                    raise AssetPolicyError("Provider response exceeds byte budget")
                body = bytearray()
                async for chunk in response.aiter_bytes(256 * 1024):
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise AssetPolicyError("Provider response exceeds byte budget")
                _validate_https_url(str(response.url), KENNEY_HOSTS)
                return str(response.url), bytes(body), {key.lower(): value for key, value in response.headers.items()}
    raise AssetPolicyError("Provider exceeded redirect limit")


def _discover_download(page_url: str, page_bytes: bytes) -> str:
    text = page_bytes.decode("utf-8", errors="replace")
    links = [html.unescape(match) for match in re.findall(r'href=["\']([^"\']+)["\']', text, re.IGNORECASE)]
    zip_links = [urljoin(page_url, link) for link in links if ".zip" in link.lower()]
    donate_links = [link for link in zip_links if "donate" in link.lower() or "media" in link.lower()]
    candidates = donate_links or zip_links
    if not candidates:
        raise AssetPolicyError("Official provider page exposes no current ZIP URL")
    chosen = candidates[0]
    parsed = urlparse(chosen)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in KENNEY_HOSTS:
        raise AssetPolicyError("Discovered provider download leaves approved host")
    return chosen


def _safe_extract_zip(archive_bytes: bytes, destination: Path) -> list[Path]:
    archive_path = destination / "provider.zip"
    archive_path.write_bytes(archive_bytes)
    extracted: list[Path] = []
    total = 0
    try:
        with zipfile.ZipFile(archive_path) as bundle:
            members = bundle.infolist()
            if not members or len(members) > MAX_MEMBERS:
                raise AssetPolicyError("ZIP member-count budget exceeded")
            for member in members:
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise AssetPolicyError("ZIP symlinks are rejected")
                relative = Path(member.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise AssetPolicyError("ZIP path traversal rejected")
                total += member.file_size
                if total > PACK_BUDGETS["max_total_bytes"]:
                    raise AssetPolicyError("ZIP expanded-size budget exceeded")
                if member.compress_size == 0 and member.file_size > 0:
                    raise AssetPolicyError("ZIP compression ratio is unsafe")
                if member.compress_size and member.file_size / member.compress_size > MAX_RATIO:
                    raise AssetPolicyError("ZIP compression ratio is unsafe")
            for member in members:
                if member.is_dir():
                    continue
                target = (destination / "contents" / member.filename).resolve()
                content_root = (destination / "contents").resolve()
                if not target.is_relative_to(content_root):
                    raise AssetPolicyError("ZIP target escapes staging")
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("wb") as output:
                    remaining = member.file_size
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        output.write(chunk)
                        remaining -= len(chunk)
                extracted.append(target)
    finally:
        archive_path.unlink(missing_ok=True)
    return extracted


def _verify_cc0_license(files: list[Path]) -> Path:
    licenses = [path for path in files if re.search(r"(license|copying|cc0)", path.name, re.IGNORECASE)]
    for path in licenses:
        if path.stat().st_size > 512 * 1024:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        if "cc0" in text or "creative commons zero" in text or "public domain dedication" in text:
            return path
    raise AssetPolicyError("Provider archive lacks a verifiable included CC0 license")


class AssetProviderOrchestrator:
    def __init__(self, manager: AssetPackManager, fetch: Fetch = secure_fetch):
        self.manager = manager
        self.fetch = fetch
        self.last_status: Dict[str, Any] = {}

    async def install_kenney_pack(self, catalog_entry: Dict[str, Any]) -> list[Dict[str, Any]]:
        provider_asset_id = catalog_entry["provider_asset_id"]
        pack_id = f"gaming-kenney-{provider_asset_id}"
        cached = self.manager.pack_records(pack_id)
        if cached:
            self.last_status[pack_id] = {"status": "cached", "count": len(cached), "network_requests": 0}
            return cached
        page_final, page_bytes, _ = await self.fetch(catalog_entry["page_url"], 4 * 1024 * 1024)
        if page_final.rstrip("/") != catalog_entry["page_url"].rstrip("/"):
            if urlparse(page_final).hostname not in KENNEY_HOSTS:
                raise AssetPolicyError("Official page redirected outside approved provider")
        download_url = _discover_download(page_final, page_bytes)
        archive_final, archive_bytes, _ = await self.fetch(download_url, MAX_ARCHIVE_BYTES)
        if urlparse(archive_final).hostname not in KENNEY_HOSTS:
            raise AssetPolicyError("Archive final host is not approved")
        terms_hash = hashlib.sha256(page_bytes).hexdigest()
        retrieved_at = datetime.now(timezone.utc).isoformat()
        source_decision = {
            "status": "approved", "provider_id": "kenney", "provider_asset_id": provider_asset_id,
            "canonical_url": catalog_entry["page_url"], "download_url": archive_final,
            "author": "Kenney", "retrieved_at": retrieved_at,
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "license_version": "CC0-1.0", "terms_snapshot": terms_hash,
            "commercial_use": True, "modification_allowed": True,
            "rights_status": "cc0_vendored_approved",
        }
        staging_root = self.manager.quarantine_dir / "provider-staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"{pack_id}-", dir=staging_root) as temp:
            files = _safe_extract_zip(archive_bytes, Path(temp))
            _verify_cc0_license(files)
            allowed = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg"}
            staged = []
            for path in files:
                mime = allowed.get(path.suffix.lower())
                if mime:
                    semantic_tags = sorted(set(catalog_entry["tags"]) | _semantic_tokens(str(path.relative_to(Path(temp) / "contents"))))
                    staged.append((path, {
                        "mime_type": mime, "niche": "gaming", "tags": semantic_tags,
                        "original_name": path.name,
                    }))
            if not staged:
                raise AssetPolicyError("Provider pack contains no supported media")
            records = self.manager.publish_pack_transaction(pack_id, staged, source_decision, PACK_BUDGETS)
        self.last_status[pack_id] = {"status": "published", "count": len(records), "provider": source_decision}
        return records

    async def resolve(self, niche: str, tags: Optional[list[str]] = None) -> Dict[str, Any]:
        requested = {tag.lower() for tag in (tags or []) if tag}
        user_assets = [
            record for record in self.manager.published_records()
            if record.get("rights_status") == "user_owned_attested"
            and record.get("niche") in {niche, NICHE_PARENT.get(niche)}
        ]
        exact = [record for record in user_assets if requested and requested <= set(record.get("tags", []))]
        if exact:
            return {"source": "exact_approved_user", "pack_id": None, "assets": exact}
        semantic = sorted(
            ((len(requested & set(record.get("tags", []))), record) for record in user_assets),
            key=lambda item: -item[0],
        )
        if semantic and semantic[0][0] > 0:
            return {"source": "semantic_approved_user", "pack_id": None, "assets": [item[1] for item in semantic if item[0] == semantic[0][0]]}
        requirements = niche_requirements(niche, tags)
        # A niche bundle is complete only when every selected catalog pack is
        # present. Resolve each pack cache-first, then install all misses.
        assets = []
        pack_ids = []
        cache_count = 0
        provider_count = 0
        errors = []
        for candidate in requirements:
            pack_id = f"gaming-kenney-{candidate['provider_asset_id']}"
            pack_ids.append(pack_id)
            cached = self.manager.pack_records(pack_id)
            if cached:
                assets.extend(cached)
                cache_count += 1
                continue
            try:
                installed = await self.install_kenney_pack(candidate)
                assets.extend(installed)
                provider_count += 1
            except Exception as exc:
                errors.append({"pack_id": pack_id, "error": str(exc)})
        if assets and not errors:
            source = (
                "semantic_cache" if provider_count == 0 else
                "approved_provider_full_pack" if cache_count == 0 else
                "mixed_cache_provider"
            )
            return {
                "source": source, "pack_id": pack_ids[0] if len(pack_ids) == 1 else None,
                "pack_ids": pack_ids, "assets": assets,
                "counts": {"cache_packs": cache_count, "provider_packs": provider_count, "total_packs": len(pack_ids)},
            }
        return {
            "source": "generated_editorial_fallback", "pack_id": None,
            "pack_ids": pack_ids, "assets": [], "errors": errors,
            "counts": {"cache_packs": cache_count, "provider_packs": provider_count, "total_packs": len(pack_ids)},
        }

    def status(self) -> Dict[str, Any]:
        records = [record for record in self.manager.published_records() if record.get("pack_id")]
        providers = sorted({record.get("source_id") for record in records if record.get("source_id")})
        generated = len([record for record in self.manager.published_records() if record.get("rights_status") == "generated_editorial"])
        return {
            "status": "ready" if records or generated else "empty",
            "asset_count": len(records) + generated,
            "cache": {"pack_count": len({record.get('pack_id') for record in records}), "asset_count": len(records)},
            "provider": providers,
            "generated": generated,
            "catalog": CATALOG,
            "last_status": self.last_status,
        }
