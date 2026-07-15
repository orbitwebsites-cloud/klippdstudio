"""Fail-closed asset-pack ingestion and render authorization.

No network download or archive extraction belongs here. A file becomes usable
only after its rights, bytes, path, and manifest record all pass validation.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import random
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from PIL import Image
from malware_scanner import MalwareScanner


SCHEMA_VERSION = "klippd.asset_pack.v1"
INDEX_VERSION = "klippd.asset_index.v1"
MANAGER_VERSION = "1.0.0"
MAX_IMAGE_DIMENSION = 12000
MAX_IMAGE_PIXELS = 40_000_000
MAX_GIF_FRAMES = 500
MAX_DECODED_IMAGE_BYTES = 512 * 1024 * 1024
APPROVED_RIGHTS = {
    "user_owned_attested",
    "cc0_vendored_approved",
    "oss_vendored_approved",
    "generated_editorial",
}
ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"}
ALLOWED_MEDIA = {
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"}, ".jpeg": {"image/jpeg"},
    ".webp": {"image/webp"}, ".gif": {"image/gif"},
    ".mp4": {"video/mp4"}, ".mov": {"video/quicktime"},
    ".webm": {"video/webm"},
    ".wav": {"audio/wav", "audio/x-wav"}, ".mp3": {"audio/mpeg"},
    ".ogg": {"audio/ogg", "application/ogg"},
    ".ttf": {"font/ttf", "application/x-font-ttf"},
    ".otf": {"font/otf", "application/vnd.ms-opentype"},
}


class AssetPolicyError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def contained_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise AssetPolicyError("Asset path must be a non-empty relative path")
    root = root.resolve()
    candidate = (root / relative).resolve()
    if candidate == root or not candidate.is_relative_to(root):
        raise AssetPolicyError("Asset path escapes its authorized root")
    return candidate


def validate_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise AssetPolicyError("Unsupported asset-pack manifest schema")
    for key in ("pack_id", "version", "niche", "status", "sources", "assets", "budgets"):
        if key not in manifest:
            raise AssetPolicyError(f"Asset-pack manifest missing {key}")
    if not all(isinstance(manifest[key], str) and manifest[key].strip() for key in ("pack_id", "version", "niche", "status")):
        raise AssetPolicyError("Pack identity fields must be non-empty strings")
    allowed_niches = {
        "gaming", "building_in_public_talking_head", "ai_tech_tutorial",
        "podcast_interview", "business_finance", "education_explainer",
        "fitness_wellness", "food_cooking", "travel_lifestyle",
        "product_ecommerce_review",
    }
    if manifest["niche"] not in allowed_niches:
        raise AssetPolicyError("Unknown niche ID")
    if manifest["status"] not in {"enabled", "disabled", "review_only"}:
        raise AssetPolicyError("Invalid pack status")
    if not isinstance(manifest["sources"], list) or not isinstance(manifest["assets"], list):
        raise AssetPolicyError("Manifest sources and assets must be arrays")
    budgets = manifest["budgets"]
    if not isinstance(budgets, dict) or not all(isinstance(budgets.get(k), int) and budgets[k] > 0 for k in ("max_files", "max_file_bytes", "max_total_bytes")):
        raise AssetPolicyError("Manifest budgets must be positive integers")
    source_ids = set()
    for source in manifest["sources"]:
        if not isinstance(source, dict) or not isinstance(source.get("source_id"), str) or not source["source_id"]:
            raise AssetPolicyError("Every source needs a stable source_id")
        if source["source_id"] in source_ids:
            raise AssetPolicyError("Duplicate source_id")
        source_ids.add(source["source_id"])
        if source.get("rights_status", "unknown") not in APPROVED_RIGHTS | {"review_only", "unknown", "quarantine_reference_only"}:
            raise AssetPolicyError("Unknown rights status")
    for asset in manifest["assets"]:
        if not isinstance(asset, dict):
            raise AssetPolicyError("Asset entries must be objects")
        required = ("asset_id", "source_id", "relative_path", "sha256", "mime_type", "rights_status", "license_id", "provenance")
        if any(not isinstance(asset.get(k), str) or not asset[k].strip() for k in required):
            raise AssetPolicyError("Asset entry is missing license/provenance/checksum fields")
        if asset["source_id"] not in source_ids:
            raise AssetPolicyError("Asset references an unknown source")
        if not re_full_sha256(asset["sha256"]):
            raise AssetPolicyError("Asset sha256 must contain 64 lowercase hex characters")
    return manifest


def re_full_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def validate_media(path: Path, declared_mime: str) -> str:
    ext = path.suffix.lower()
    if ext in ARCHIVE_EXTENSIONS:
        raise AssetPolicyError("Archives are rejected; extraction is disabled")
    if ext not in ALLOWED_MEDIA or declared_mime not in ALLOWED_MEDIA[ext]:
        raise AssetPolicyError("Extension and declared MIME are not an allowed pair")
    header = path.read_bytes()[:16]
    signatures = {
        ".png": header.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": header.startswith(b"\xff\xd8\xff"), ".jpeg": header.startswith(b"\xff\xd8\xff"),
        ".gif": header.startswith((b"GIF87a", b"GIF89a")),
        ".webp": header.startswith(b"RIFF") and header[8:12] == b"WEBP",
        ".webm": header.startswith(b"\x1aE\xdf\xa3"),
        ".mp4": len(header) >= 12 and header[4:8] == b"ftyp",
        ".mov": len(header) >= 12 and header[4:8] == b"ftyp",
        ".wav": header.startswith(b"RIFF") and header[8:12] == b"WAVE",
        ".mp3": header.startswith(b"ID3") or header.startswith(b"\xff"),
        ".ogg": header.startswith(b"OggS"),
        ".ttf": header.startswith((b"\x00\x01\x00\x00", b"true")),
        ".otf": header.startswith(b"OTTO"),
    }
    if not signatures.get(ext, False):
        raise AssetPolicyError("File magic does not match its extension")
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        try:
            with Image.open(path) as image:
                width, height = image.size
                frames = int(getattr(image, "n_frames", 1))
                if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION or width * height > MAX_IMAGE_PIXELS:
                    raise AssetPolicyError("Image dimension/pixel budget exceeded")
                if frames > MAX_GIF_FRAMES:
                    raise AssetPolicyError("GIF frame-count budget exceeded")
                if width * height * 4 * frames > MAX_DECODED_IMAGE_BYTES:
                    raise AssetPolicyError("Decoded image-byte budget exceeded")
                image.verify()
            with Image.open(path) as image:
                image.load()
        except AssetPolicyError:
            raise
        except Exception as exc:
            raise AssetPolicyError("Image failed decode validation") from exc
    elif ext in {".mp4", ".mov", ".webm", ".wav", ".mp3", ".ogg"}:
        if not shutil.which("ffprobe"):
            raise AssetPolicyError("ffprobe is required for media decode validation")
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if result.returncode != 0:
            raise AssetPolicyError("Media failed decode/probe validation")
    return ext


class AssetPackManager:
    def __init__(self, library_dir: Path, quarantine_dir: Optional[Path] = None, scanner: Optional[MalwareScanner] = None):
        self.library_dir = library_dir.resolve()
        self.quarantine_dir = (quarantine_dir or self.library_dir.parent / "quarantine").resolve()
        self.index_path = self.library_dir / ".asset-index.json"
        self.ledger_path = self.library_dir / ".attribution-ledger.json"
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        self._publish_lock = threading.RLock()
        self.scanner = scanner or MalwareScanner()

    def _index(self) -> Dict[str, Any]:
        if not self.index_path.exists():
            return {"schema_version": INDEX_VERSION, "assets": {}}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise AssetPolicyError("Asset index is unreadable; failing closed") from exc
        if data.get("schema_version") != INDEX_VERSION or not isinstance(data.get("assets"), dict):
            raise AssetPolicyError("Asset index schema is invalid; failing closed")
        return data

    def _save_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        index = self._index()
        index["assets"][record["name"]] = record
        _atomic_json(self.index_path, index)
        ledger = []
        if self.ledger_path.exists():
            ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        ledger = [item for item in ledger if item.get("name") != record["name"]]
        ledger.append({key: record.get(key) for key in ("name", "sha256", "source_id", "rights_status", "license_id", "attribution", "provenance", "status")})
        _atomic_json(self.ledger_path, ledger)
        return record

    def pack_records(self, pack_id: str) -> list[Dict[str, Any]]:
        index = self._index()
        return [
            record for record in index["assets"].values()
            if record.get("pack_id") == pack_id and self._record_bytes_are_current(record)
        ]

    def _record_bytes_are_current(self, record: Dict[str, Any]) -> bool:
        try:
            path = contained_path(self.library_dir, str(record.get("name", "")))
            return (
                path.is_file()
                and record.get("status") == "published"
                and record.get("rights_status") in APPROVED_RIGHTS
                and sha256_file(path) == record.get("sha256")
            )
        except (OSError, RuntimeError, AssetPolicyError):
            return False

    def publish_pack_transaction(
        self,
        pack_id: str,
        staged_files: list[tuple[Path, Dict[str, Any]]],
        source_decision: Dict[str, Any],
        budgets: Dict[str, int],
    ) -> list[Dict[str, Any]]:
        """Validate every member, then atomically expose the complete pack."""
        with self._publish_lock:
            return self._publish_pack_transaction_unlocked(pack_id, staged_files, source_decision, budgets)

    def _publish_pack_transaction_unlocked(
        self,
        pack_id: str,
        staged_files: list[tuple[Path, Dict[str, Any]]],
        source_decision: Dict[str, Any],
        budgets: Dict[str, int],
    ) -> list[Dict[str, Any]]:
        if source_decision.get("status") != "approved" or source_decision.get("rights_status") not in APPROVED_RIGHTS:
            raise AssetPolicyError("Provider source decision is not approved")
        required = ("provider_id", "provider_asset_id", "canonical_url", "author", "retrieved_at", "license_url", "license_version", "terms_snapshot", "commercial_use", "modification_allowed")
        if any(key not in source_decision for key in required):
            raise AssetPolicyError("Provider source decision lacks immutable provenance")
        if source_decision["commercial_use"] is not True or source_decision["modification_allowed"] is not True:
            raise AssetPolicyError("Provider terms do not allow commercial use and modification")
        if len(staged_files) > budgets["max_files"]:
            raise AssetPolicyError("Pack exceeds file-count budget")
        total = sum(path.stat().st_size for path, _ in staged_files)
        if total > budgets["max_total_bytes"]:
            raise AssetPolicyError("Pack exceeds total-byte budget")

        prepared = []
        seen_hashes = set()
        for path, entry in staged_files:
            if entry.get("rights_status") not in (None, source_decision["rights_status"]):
                raise AssetPolicyError("Asset rights conflict with validated provider source")
            if path.stat().st_size > budgets["max_file_bytes"]:
                raise AssetPolicyError("Asset exceeds per-file budget")
            validate_media(path, entry["mime_type"])
            scan = self.scanner.scan(path)
            digest = sha256_file(path)
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            name = f"{pack_id}_{digest[:16]}{path.suffix.lower()}"
            prepared.append((path, name, digest, entry, scan))

        temp_paths: list[Path] = []
        created_final_paths: list[Path] = []
        old_index = self._index()
        try:
            records = []
            for source, name, digest, entry, scan in prepared:
                destination = contained_path(self.library_dir, name)
                if destination.exists():
                    if sha256_file(destination) != digest:
                        raise AssetPolicyError("Pack destination collision")
                else:
                    temp = self.library_dir / f".{name}.{uuid.uuid4().hex}.publishing"
                    shutil.copyfile(source, temp)
                    temp_paths.append(temp)
                    created_final_paths.append(destination)
                records.append({
                    "name": name, "sha256": digest, "size": source.stat().st_size,
                    "mime_type": entry["mime_type"], "source_id": source_decision["provider_id"],
                    "rights_status": source_decision["rights_status"], "license_id": source_decision["license_version"],
                    "attribution": f"{source_decision['author']} - {source_decision['license_version']}",
                    "provenance": {**source_decision, "malware_scan": scan}, "malware_scan": scan,
                    "status": "published", "niche": entry.get("niche", "gaming"),
                    "is_evidence": False, "pack_id": pack_id, "tags": sorted(set(entry.get("tags", []))),
                    "provider_asset_id": source_decision["provider_asset_id"],
                    "original_name": str(entry.get("original_name") or source.name)[:240],
                })
            for temp, final in zip(temp_paths, created_final_paths):
                os.replace(temp, final)
            index = self._index()
            for record in records:
                index["assets"][record["name"]] = record
            _atomic_json(self.index_path, index)
            ledger = [] if not self.ledger_path.exists() else json.loads(self.ledger_path.read_text(encoding="utf-8"))
            ledger.extend(records)
            _atomic_json(self.ledger_path, ledger)
            return records
        except Exception:
            for path in temp_paths + created_final_paths:
                path.unlink(missing_ok=True)
            _atomic_json(self.index_path, old_index)
            raise

    def register_generated(self, path: Path, metadata: Dict[str, Any]) -> Dict[str, Any]:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.library_dir):
            raise AssetPolicyError("Generated asset is outside the library")
        validate_media(resolved, "image/png")
        scan = self.scanner.scan(resolved)
        digest = sha256_file(resolved)
        if metadata.get("sha256") != digest or metadata.get("is_evidence") is not False:
            raise AssetPolicyError("Generated asset metadata is incomplete or does not match bytes")
        record = {
            "name": resolved.name, "sha256": digest, "size": resolved.stat().st_size,
            "mime_type": "image/png", "source_id": "klipped_generator",
            "rights_status": "generated_editorial", "license_id": "in-house-generated",
            "attribution": "Klipped Studio generated editorial graphic",
            "provenance": metadata.get("provenance"), "generator": metadata.get("generator"),
            "generator_version": metadata.get("generator_version"), "is_evidence": False,
            "status": "published", "niche": metadata.get("niche", "general"),
            "malware_scan": scan,
        }
        return self._save_record(record)

    def ingest_file(self, source_root: Path, entry: Dict[str, Any], budgets: Dict[str, int], *, audit_sample_rate: float = 0.1, rng: Optional[random.Random] = None) -> Dict[str, Any]:
        source = contained_path(source_root, entry.get("relative_path", ""))
        if not source.is_file():
            raise AssetPolicyError("Manifest asset does not exist")
        if source.suffix.lower() in ARCHIVE_EXTENSIONS:
            raise AssetPolicyError("Archives are rejected")
        if source.stat().st_size > budgets["max_file_bytes"]:
            raise AssetPolicyError("Asset exceeds per-file budget")
        validate_media(source, entry.get("mime_type", ""))
        scan = self.scanner.scan(source)
        digest = sha256_file(source)
        if digest != entry.get("sha256"):
            raise AssetPolicyError("Asset checksum mismatch")
        rights = entry.get("rights_status", "unknown")
        if rights not in APPROVED_RIGHTS:
            return self._quarantine(source, entry, "unknown_or_unapproved_rights")
        index = self._index()
        duplicate = next((record for record in index["assets"].values() if record.get("sha256") == digest and record.get("status") == "published"), None)
        if duplicate:
            return {**duplicate, "deduplicated": True}
        if len([record for record in index["assets"].values() if record.get("status") == "published"]) >= budgets["max_files"]:
            raise AssetPolicyError("Pack exceeds file-count budget")
        current_total = sum(int(record.get("size", 0)) for record in index["assets"].values() if record.get("status") == "published")
        if current_total + source.stat().st_size > budgets["max_total_bytes"]:
            raise AssetPolicyError("Pack exceeds total-byte budget")
        if audit_sample_rate > 0 and (rng or random.SystemRandom()).random() < audit_sample_rate:
            return self._quarantine(source, entry, "randomized_partial_audit")
        safe_name = f"{digest[:16]}_{Path(entry['relative_path']).name}"
        destination = contained_path(self.library_dir, safe_name)
        tmp = self.library_dir / f".{safe_name}.{uuid.uuid4().hex}.publishing"
        shutil.copyfile(source, tmp)
        os.replace(tmp, destination)
        record = {
            "name": destination.name, "sha256": digest, "size": destination.stat().st_size,
            "mime_type": entry["mime_type"], "source_id": entry["source_id"],
            "rights_status": rights, "license_id": entry["license_id"],
            "attribution": entry.get("attribution", ""), "provenance": entry["provenance"],
            "status": "published", "niche": entry.get("niche", "gaming"), "is_evidence": bool(entry.get("is_evidence", False)),
            "tags": sorted({str(tag).lower() for tag in entry.get("tags", []) if str(tag).strip()}),
            "malware_scan": scan,
        }
        return self._save_record(record)

    def _quarantine(self, source: Path, entry: Dict[str, Any], reason: str) -> Dict[str, Any]:
        source_id = str(entry.get("source_id", "unknown"))
        safe_source_id = "".join(char for char in source_id if char.isalnum() or char in "_-") or "unknown"
        target_root = contained_path(self.quarantine_dir / "denied", safe_source_id)
        target_root.mkdir(parents=True, exist_ok=True)
        digest = sha256_file(source)
        marker = {
            "schema_version": "klippd.asset_quarantine.v1", "source_id": source_id,
            "sha256": digest, "original_name": source.name, "reason": reason,
            "status": "quarantined", "allowed_actions": ["metadata_inventory", "request_rights_proof"],
            "denied_actions": ["download", "preview", "embedding", "training", "render", "redistribution"],
        }
        _atomic_json(target_root / f"{digest}.json", marker)
        return marker

    def resolve_renderable(self, candidate: str | Path) -> Optional[Path]:
        try:
            path = Path(candidate).resolve(strict=True)
            if not path.is_file() or not path.is_relative_to(self.library_dir):
                return None
            record = self._index()["assets"].get(path.name)
            if not record or record.get("status") != "published" or record.get("rights_status") not in APPROVED_RIGHTS:
                return None
            if sha256_file(path) != record.get("sha256"):
                return None
            return path
        except (OSError, RuntimeError, AssetPolicyError):
            return None

    def published_records(self) -> Iterable[Dict[str, Any]]:
        index = self._index()
        for record in index["assets"].values():
            if self._record_bytes_are_current(record):
                yield record

    def delete(self, name: str) -> bool:
        index = self._index()
        record = index["assets"].get(name)
        if not record:
            return False
        path = self.resolve_renderable(self.library_dir / name)
        if path:
            path.unlink()
        del index["assets"][name]
        _atomic_json(self.index_path, index)
        return True


def quarantine_denied_source(source_root: Path, quarantine_root: Path, source_id: str) -> Path:
    """Write a containment-checked deny marker without previewing or moving bytes."""
    source = source_root.resolve(strict=True)
    destination = contained_path(quarantine_root.resolve(), f"denied/{source_id}")
    destination.mkdir(parents=True, exist_ok=True)
    marker = {
        "schema_version": "klippd.denied_source_marker.v1", "source_id": source_id,
        "source_path": str(source), "status": "quarantine_reference_only",
        "bytes_moved": False, "bytes_inspected": False,
        "allowed_actions": ["metadata_inventory", "request_rights_proof"],
        "denied_actions": ["download", "preview", "embedding", "training", "render", "redistribution"],
    }
    marker_path = destination / "SOURCE_BLOCKED.json"
    _atomic_json(marker_path, marker)
    return marker_path
