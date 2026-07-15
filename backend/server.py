"""AI Video Editor Backend
Endpoints:
  POST   /api/projects/upload          Upload a video file
  GET    /api/projects                 List projects
  GET    /api/projects/{id}            Project details
  DELETE /api/projects/{id}            Delete
  POST   /api/projects/{id}/analyze    Transcribe + LLM analyze (background)
  GET    /api/projects/{id}/broll_search  Resolve approved niche-pack assets
  POST   /api/projects/{id}/render     Render final video (background)
  GET    /api/projects/{id}/download   Serve final MP4
  GET    /api/media/original/{id}      Stream original video
  GET    /api/media/output/{id}        Stream output video
"""
from fastapi import FastAPI, APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi import Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from cryptography.fernet import Fernet
from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import List, Optional, Dict, Any, Literal
from pathlib import Path
import os
import re
import json
import logging
import mimetypes
import uuid
import shutil
import asyncio
import aiofiles
from datetime import datetime, timezone
from urllib.parse import urlparse

import ai_services as ai
import asset_generator
from asset_pack_manager import AssetPackManager, AssetPolicyError, sha256_file
from asset_provider_orchestrator import AssetProviderOrchestrator, rank_pack_assets
import video_processor as vp
import post_render_qa
from local_store import LocalDatabase
from premium_features import register_premium_routes


# ---------- BOOTSTRAP ----------
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT_DIR.parent / "data"))).resolve()
SFX_DIR = os.environ.get("SFX_DIR", str(ROOT_DIR / "assets" / "sfx"))
LIBRARY_DIR = DATA_DIR / "library"
ASSET_QUARANTINE_DIR = DATA_DIR / "quarantine"
for sub in ("videos", "audio", "output", "subtitles", "broll", "library"):
    (DATA_DIR / sub).mkdir(parents=True, exist_ok=True)
ASSET_MANAGER = AssetPackManager(LIBRARY_DIR, ASSET_QUARANTINE_DIR)
ASSET_ORCHESTRATOR = AssetProviderOrchestrator(ASSET_MANAGER)

mongo_url = os.environ.get("MONGO_URL", "").strip()
if mongo_url:
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
    db = client[os.environ.get("DB_NAME", "klipped")]
else:
    client = None
    db = LocalDatabase(DATA_DIR / "klipped-db.json")


def _master_key() -> bytes:
    configured = os.environ.get("MASTER_ENCRYPTION_KEY", "").strip()
    if configured:
        return configured.encode()
    key_path = DATA_DIR / ".master-key"
    if key_path.exists():
        return key_path.read_bytes().strip()
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    return key


cipher = Fernet(_master_key())

MAX_VIDEO_BYTES = int(os.environ.get("MAX_VIDEO_BYTES", str(2 * 1024 * 1024 * 1024)))
MAX_ASSET_BYTES = int(os.environ.get("MAX_ASSET_BYTES", str(500 * 1024 * 1024)))
CHUNK_BYTES = 4 * 1024 * 1024

USER_ID = "default_user"  # single-user MVP

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")
logger = logging.getLogger("backend")

app = FastAPI(title="AI Video Editor")
api = APIRouter(prefix="/api")
APP_ACCESS_TOKEN = os.environ.get("APP_ACCESS_TOKEN", "").strip()


@app.middleware("http")
async def optional_access_token(request: Request, call_next):
    """Optional MVP gate; leave unset locally and protect public deployments."""
    if APP_ACCESS_TOKEN and request.method != "OPTIONS" and request.url.path != "/api/health":
        bearer = request.headers.get("authorization", "")
        supplied = request.headers.get("x-app-token", "")
        if bearer.lower().startswith("bearer "):
            supplied = bearer[7:].strip()
        import secrets
        if not supplied or not secrets.compare_digest(supplied, APP_ACCESS_TOKEN):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


# ---------- KEY MANAGEMENT ----------
def _enc(v: str) -> str:
    return cipher.encrypt(v.encode()).decode()


def _dec(v: str) -> str:
    return cipher.decrypt(v.encode()).decode()


async def get_keys() -> Dict[str, str]:
    doc = await db.settings.find_one({"user_id": USER_ID})
    if not doc:
        return {}
    encrypted = doc.get("keys", {})
    out = {}
    for k, v in encrypted.items():
        try:
            out[k] = _dec(v)
        except Exception:
            pass
    return out


async def save_keys(new_keys: Dict[str, str]) -> None:
    existing = await db.settings.find_one({"user_id": USER_ID}) or {}
    encrypted = existing.get("keys", {})
    for k, v in new_keys.items():
        if v and v.strip() and not v.startswith("***"):
            encrypted[k] = _enc(v.strip())
    await db.settings.update_one(
        {"user_id": USER_ID},
        {"$set": {"keys": encrypted, "updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )


@app.on_event("startup")
async def seed_keys_from_env():
    """If DB has no keys yet, seed from env vars (SEED_*_KEY)."""
    existing = await db.settings.find_one({"user_id": USER_ID})
    if existing and existing.get("keys"):
        logger.info("Keys already present in DB; skipping seed.")
        return
    seed = {}
    if os.environ.get("SEED_GROQ_KEY"):
        seed["groq"] = os.environ["SEED_GROQ_KEY"]
    if os.environ.get("SEED_CEREBRAS_KEY"):
        seed["cerebras"] = os.environ["SEED_CEREBRAS_KEY"]
    if seed:
        await save_keys(seed)
        logger.info(f"Seeded keys from env: {list(seed.keys())}")


# ---------- MODELS ----------
class RenderOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")
    style: Literal["tiktok", "youtube", "luxury"] = "tiktok"
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    remove_fillers: bool = True
    captions: bool = True
    sfx: bool = True
    zoom_ins: bool = True
    broll: bool = True
    excluded_filler_indices: List[int] = Field(default_factory=list)
    added_filler_indices: List[int] = Field(default_factory=list)
    selected_broll: List[Dict[str, Any]] = Field(default_factory=list)
    # If set, only render a slice of the source (viral-clip mode)
    clip_start: Optional[float] = None
    clip_end: Optional[float] = None
    clip_label: Optional[str] = None  # used to name the output file

    @model_validator(mode="after")
    def validate_clip_window(self):
        if (self.clip_start is None) != (self.clip_end is None):
            raise ValueError("clip_start and clip_end must be provided together")
        if self.clip_start is not None:
            if self.clip_start < 0 or self.clip_end <= self.clip_start:
                raise ValueError("clip range must have a non-negative start and end after start")
        return self


class AnalyzeBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    requested_profile: Optional[Literal["general", "gaming", "minecraft_narrative", "talking_head"]] = None
    training_profile_id: Optional[str] = Field(default=None, max_length=64)


class TrainingReferenceBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(min_length=2, max_length=120)
    source_url: Optional[str] = Field(default=None, max_length=500)
    niche: str = Field(default="gaming", min_length=2, max_length=40)
    game: Optional[str] = Field(default=None, max_length=60)
    rights_status: Literal["owned", "licensed", "research_only"]
    notes: str = Field(min_length=20, max_length=3000)
    principles: List[str] = Field(default_factory=list, max_length=8)


class TrainingProfileBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=2, max_length=80)
    niche: str = Field(default="gaming", min_length=2, max_length=40)
    game: Optional[str] = Field(default=None, max_length=60)
    base_profile: Literal["general", "gaming", "minecraft_narrative", "talking_head"] = "gaming"
    reference_ids: List[str] = Field(default_factory=list, max_length=50)
    principles: List[str] = Field(default_factory=list, min_length=1, max_length=12)


# ---------- HELPERS ----------
async def update_project(pid: str, **fields) -> None:
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.projects.update_one({"id": pid}, {"$set": fields})


async def get_project(pid: str) -> Dict:
    doc = await db.projects.find_one({"id": pid}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Project not found")
    return doc


def _clean_training_principles(values: List[str]) -> List[str]:
    """Keep a small, human-reviewable set of general editing principles."""
    cleaned = []
    for value in values:
        item = re.sub(r"\s+", " ", str(value)).strip()
        if len(item) < 12:
            continue
        if re.search(r"\b(copy|clone|imitate|exactly like)\b", item, re.IGNORECASE):
            raise HTTPException(400, "Use general editing principles, not creator imitation instructions")
        if item not in cleaned:
            cleaned.append(item[:360])
    return cleaned


async def _training_context(profile_id: Optional[str]) -> tuple[Optional[Dict[str, Any]], str]:
    if not profile_id:
        return None, ""
    profile = await db.training_profiles.find_one({"id": profile_id, "user_id": USER_ID}, {"_id": 0})
    if not profile:
        raise HTTPException(404, "Training profile not found")
    if profile.get("status") != "active":
        raise HTTPException(400, "Activate this training profile before using it on an edit")
    principles = profile.get("principles", [])
    context = "\n".join(f"- {rule}" for rule in principles)
    return profile, context


async def _save_upload(file: UploadFile, destination: Path, max_bytes: int) -> int:
    """Stream one upload to disk with a hard size limit and atomic publish."""
    tmp = destination.with_suffix(destination.suffix + ".uploading")
    total = 0
    try:
        async with aiofiles.open(tmp, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(413, f"File exceeds the {max_bytes // (1024 * 1024)} MB limit")
                await out.write(chunk)
        if total == 0:
            raise HTTPException(400, "Empty file uploaded")
        os.replace(tmp, destination)
        return total
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _safe_data_file(candidate: str) -> Optional[str]:
    """Allow renderer access only to regular files inside this app's data directory."""
    try:
        path = Path(candidate).resolve(strict=True)
        root = DATA_DIR.resolve(strict=True)
        if not path.is_file() or not path.is_relative_to(root):
            return None
        return str(path)
    except (OSError, RuntimeError):
        return None


# ---------- ROUTES ----------
@api.get("/")
async def root():
    return {"ok": True, "app": "Klipped Studio", "version": "0.2.0"}


@api.get("/health")
async def health():
    """Deployment readiness check for database, FFmpeg and persistent storage."""
    checks = {
        "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
        "storage": DATA_DIR.exists() and os.access(DATA_DIR, os.W_OK),
        "database": False,
    }
    try:
        await db.command("ping")
        checks["database"] = True
    except Exception:
        pass
    if not all(checks.values()):
        raise HTTPException(503, detail={"ok": False, "checks": checks})
    return {"ok": True, "checks": checks}


# ---------- TRAINING LAB ----------
@api.get("/training/dashboard")
async def training_dashboard():
    references = await db.training_references.find({"user_id": USER_ID}, {"_id": 0}).sort("created_at", -1).to_list(100)
    profiles = await db.training_profiles.find({"user_id": USER_ID}, {"_id": 0}).sort("updated_at", -1).to_list(100)
    return {
        "policy": "References are editorial research and annotations, not uploaded creator footage or model fine-tuning data.",
        "references": references,
        "profiles": profiles,
        "stats": {
            "references": len(references),
            "active_profiles": sum(1 for profile in profiles if profile.get("status") == "active"),
            "approved_principles": sum(len(profile.get("principles", [])) for profile in profiles),
        },
    }


@api.post("/training/references")
async def create_training_reference(body: TrainingReferenceBody):
    if body.source_url:
        parsed = urlparse(body.source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(400, "Reference link must be a valid http(s) URL")
    principles = _clean_training_principles(body.principles)
    record = {
        "id": f"ref_{uuid.uuid4().hex[:12]}", "user_id": USER_ID,
        "title": body.title.strip(), "source_url": body.source_url.strip() if body.source_url else None,
        "niche": body.niche.strip().lower(), "game": body.game.strip() if body.game else None,
        "rights_status": body.rights_status, "notes": body.notes.strip(), "principles": principles,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.training_references.insert_one(record)
    return record


@api.post("/training/profiles")
async def create_training_profile(body: TrainingProfileBody):
    principles = _clean_training_principles(body.principles)
    reference_ids = list(dict.fromkeys(body.reference_ids))
    valid_references = []
    for ref_id in reference_ids:
        reference = await db.training_references.find_one({"id": ref_id, "user_id": USER_ID}, {"_id": 0})
        if not reference:
            raise HTTPException(400, "One or more selected references no longer exist")
        valid_references.append(reference)
    # References contribute only the author's explicit, reviewable principles.
    # We never ingest video frames, copied timelines, or creator identity data.
    principles = _clean_training_principles([
        *principles,
        *(rule for reference in valid_references for rule in reference.get("principles", [])),
    ])
    if not principles:
        raise HTTPException(400, "Add at least one usable editorial principle")
    record = {
        "id": f"profile_{uuid.uuid4().hex[:12]}", "user_id": USER_ID,
        "name": body.name.strip(), "niche": body.niche.strip().lower(), "game": body.game.strip() if body.game else None,
        "base_profile": body.base_profile, "reference_ids": reference_ids, "principles": principles,
        "reference_count": len(valid_references), "status": "draft",
        "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.training_profiles.insert_one(record)
    return record


@api.post("/training/profiles/{profile_id}/activate")
async def activate_training_profile(profile_id: str):
    profile = await db.training_profiles.find_one({"id": profile_id, "user_id": USER_ID}, {"_id": 0})
    if not profile:
        raise HTTPException(404, "Training profile not found")
    if len(profile.get("principles", [])) < 3:
        raise HTTPException(400, "Add at least 3 approved principles before activation")
    await db.training_profiles.update_one({"id": profile_id, "user_id": USER_ID}, {"$set": {
        "status": "active", "activated_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }})
    return {"ok": True, "profile_id": profile_id, "status": "active"}


# ---------- ROUTES: PROJECTS ----------
@api.post("/projects/upload")
async def upload_project(file: UploadFile = File(...)):
    pid = str(uuid.uuid4())
    ext = os.path.splitext(file.filename or "video.mp4")[1] or ".mp4"
    ext = ext.lower()
    if ext not in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mpeg", ".mpg", ".qt"}:
        raise HTTPException(400, f"Unsupported video type: {ext}")
    dst = DATA_DIR / "videos" / f"{pid}{ext}"

    total = await _save_upload(file, dst, MAX_VIDEO_BYTES)

    # Probe
    try:
        meta = vp.probe_video(str(dst))
    except Exception as e:
        dst.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not read video file ({ext}): {str(e)[:200]}")

    project = {
        "id": pid,
        "user_id": USER_ID,
        "name": file.filename or f"Project-{pid[:8]}",
        "status": "uploaded",
        "status_message": "Uploaded, ready to analyze",
        "progress": 0,
        "original_path": str(dst),
        "size_bytes": total,
        "duration": meta.get("duration", 0),
        "width": meta.get("width", 0),
        "height": meta.get("height", 0),
        "fps": meta.get("fps", 30),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "transcript": None,
        "analysis": None,
        "output_path": None,
        "render_options": None,
    }
    await db.projects.insert_one(project)
    project.pop("_id", None)
    return project


@api.get("/projects")
async def list_projects():
    items = await db.projects.find(
        {"user_id": USER_ID},
        {"_id": 0, "transcript.words": 0, "transcript.segments": 0},
    ).sort("created_at", -1).to_list(100)
    return items


# ---------- CHUNKED UPLOAD (for large files bypassing ingress 413) ----------
UPLOAD_TMP = DATA_DIR / "uploads_tmp"
UPLOAD_TMP.mkdir(parents=True, exist_ok=True)
upload_locks: Dict[str, asyncio.Lock] = {}


def _upload_lock(upload_id: str) -> asyncio.Lock:
    """Serialize writes to the manifest for one resumable upload."""
    if upload_id not in upload_locks:
        upload_locks[upload_id] = asyncio.Lock()
    return upload_locks[upload_id]


class UploadInit(BaseModel):
    model_config = ConfigDict(extra="ignore")
    filename: str
    size: int
    total_chunks: int


@api.post("/uploads/init")
async def upload_init(body: UploadInit):
    ext = os.path.splitext(body.filename or "video.mp4")[1].lower() or ".mp4"
    if ext not in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mpeg", ".mpg", ".qt"}:
        raise HTTPException(400, f"Unsupported video type: {ext}")
    if body.size <= 0 or body.total_chunks <= 0:
        raise HTTPException(400, "Invalid size/chunks")
    if body.size > MAX_VIDEO_BYTES:
        raise HTTPException(413, f"Video exceeds the {MAX_VIDEO_BYTES // (1024 * 1024)} MB limit")
    expected_chunks = max(1, (body.size + CHUNK_BYTES - 1) // CHUNK_BYTES)
    if body.total_chunks != expected_chunks:
        raise HTTPException(400, "Invalid chunk manifest")
    upload_id = uuid.uuid4().hex
    session_dir = UPLOAD_TMP / upload_id
    session_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "upload_id": upload_id,
        "filename": body.filename,
        "size": body.size,
        "total_chunks": body.total_chunks,
        "ext": ext,
        "received_chunks": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    async with aiofiles.open(session_dir / "manifest.json", "w") as f:
        await f.write(json.dumps(manifest))
    return {"upload_id": upload_id}


@api.post("/uploads/chunk/{upload_id}")
async def upload_chunk(upload_id: str, index: int, file: UploadFile = File(...)):
    session_dir = UPLOAD_TMP / upload_id
    manifest_path = session_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(404, "Upload session not found")
    async with _upload_lock(upload_id):
        async with aiofiles.open(manifest_path, "r") as f:
            m = json.loads(await f.read())
        if index < 0 or index >= m["total_chunks"]:
            raise HTTPException(400, "Chunk index is out of range")
        expected_size = min(CHUNK_BYTES, m["size"] - index * CHUNK_BYTES)
        tmp_path = session_dir / f"chunk_{index:06d}.uploading"
        chunk_path = session_dir / f"chunk_{index:06d}"
        total = 0
        async with aiofiles.open(tmp_path, "wb") as out:
            while True:
                data = await file.read(1024 * 512)
                if not data:
                    break
                total += len(data)
                if total > expected_size:
                    tmp_path.unlink(missing_ok=True)
                    raise HTTPException(400, "Chunk is larger than expected")
                await out.write(data)
        if total != expected_size:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(400, f"Invalid chunk size: expected {expected_size}, got {total}")
        os.replace(tmp_path, chunk_path)
        if index not in m["received_chunks"]:
            m["received_chunks"].append(index)
        m["received_chunks"] = sorted(set(m["received_chunks"]))
        async with aiofiles.open(manifest_path, "w") as f:
            await f.write(json.dumps(m))
        return {"ok": True, "received": len(m["received_chunks"]), "total": m["total_chunks"]}


@api.get("/uploads/status/{upload_id}")
async def upload_status(upload_id: str):
    """Get upload session status — used for resume."""
    manifest_path = UPLOAD_TMP / upload_id / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(404, "Upload session not found")
    async with aiofiles.open(manifest_path, "r") as f:
        m = json.loads(await f.read())
    return {
        "upload_id": upload_id,
        "filename": m.get("filename"),
        "size": m.get("size"),
        "total_chunks": m.get("total_chunks"),
        "received_chunks": sorted(m.get("received_chunks", [])),
    }


@api.post("/uploads/finalize/{upload_id}")
async def upload_finalize(upload_id: str):
    session_dir = UPLOAD_TMP / upload_id
    manifest_path = session_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(404, "Upload session not found")
    async with _upload_lock(upload_id):
        async with aiofiles.open(manifest_path, "r") as f:
            m = json.loads(await f.read())
        expected_indices = list(range(m["total_chunks"]))
        if sorted(m["received_chunks"]) != expected_indices:
            raise HTTPException(400, f"Upload is incomplete: got {len(m['received_chunks'])}/{m['total_chunks']} chunks")

        pid = str(uuid.uuid4())
        ext = m["ext"]
        dst = DATA_DIR / "videos" / f"{pid}{ext}"
        total = 0
        async with aiofiles.open(dst, "wb") as out:
            for i in expected_indices:
                chunk_path = session_dir / f"chunk_{i:06d}"
                if not chunk_path.exists():
                    dst.unlink(missing_ok=True)
                    raise HTTPException(400, f"Chunk {i} is missing on disk")
                async with aiofiles.open(chunk_path, "rb") as inp:
                    while True:
                        data = await inp.read(1024 * 1024)
                        if not data:
                            break
                        await out.write(data)
                        total += len(data)
        if total != m["size"]:
            dst.unlink(missing_ok=True)
            raise HTTPException(400, f"Assembled upload size mismatch: expected {m['size']}, got {total}")

    try:
        meta = vp.probe_video(str(dst))
    except Exception as e:
        dst.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not read assembled video ({ext}): {str(e)[:200]}")
    shutil.rmtree(session_dir, ignore_errors=True)
    upload_locks.pop(upload_id, None)

    project = {
        "id": pid,
        "user_id": USER_ID,
        "name": m.get("filename") or f"Project-{pid[:8]}",
        "status": "uploaded",
        "status_message": "Uploaded, ready to analyze",
        "progress": 0,
        "original_path": str(dst),
        "size_bytes": total,
        "duration": meta.get("duration", 0),
        "width": meta.get("width", 0),
        "height": meta.get("height", 0),
        "fps": meta.get("fps", 30),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "transcript": None,
        "analysis": None,
        "output_path": None,
        "render_options": None,
    }
    await db.projects.insert_one(project)
    project.pop("_id", None)
    return project


@api.get("/projects/{pid}")
async def project_detail(pid: str):
    return await get_project(pid)


@api.delete("/projects/{pid}")
async def delete_project(pid: str):
    doc = await db.projects.find_one({"id": pid})
    if not doc:
        raise HTTPException(404)
    # Clean files
    for k in ("original_path", "audio_path", "output_path"):
        p = doc.get(k)
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    await db.projects.delete_one({"id": pid})
    return {"ok": True}


# ---------- ANALYZE PIPELINE ----------
async def _run_analysis(pid: str):
    try:
        keys = await get_keys()
        if not keys.get("groq"):
            await update_project(pid, status="error",
                                 status_message="The AI service is not configured. Please try again later.")
            return

        proj = await get_project(pid)
        await update_project(pid, status="extracting_audio", progress=5,
                             status_message="Extracting audio...")

        audio_path = str(DATA_DIR / "audio" / f"{pid}.mp3")
        await asyncio.to_thread(vp.extract_audio, proj["original_path"], audio_path)
        await update_project(pid, audio_path=audio_path, progress=15,
                             status="transcribing",
                             status_message="Transcribing with Whisper (Groq)...")

        transcript = await ai.transcribe_audio(audio_path, keys["groq"])
        await update_project(pid, transcript=transcript, progress=55,
                             status="analyzing",
                             status_message="AI analyzing for fillers, emphasis, B-roll...")

        # A selected Training Lab profile is an explicit user choice. Otherwise
        # retain per-project niche inference instead of applying a global style.
        requested_profile = proj.get("requested_profile")
        training_profile, training_context = await _training_context(proj.get("training_profile_id"))
        if training_profile:
            requested_profile = training_profile.get("base_profile", requested_profile)
        analysis_options = {"profile": requested_profile}
        if training_context:
            analysis_options["training_context"] = training_context
        analysis = await ai.analyze_transcript(
            transcript.get("words", []), keys, **analysis_options,
        )
        if training_profile:
            analysis["training_profile"] = {
                "id": training_profile["id"], "name": training_profile["name"],
                "niche": training_profile["niche"], "game": training_profile.get("game"),
                "principle_count": len(training_profile.get("principles", [])),
            }
        quality_review = analysis.get("quality_review", {})
        if not quality_review.get("passed"):
            issue_codes = [
                str(issue.get("code")) for issue in quality_review.get("remaining_issues", [])
                if isinstance(issue, dict) and issue.get("code")
            ]
            summary = ", ".join(issue_codes[:3]) or "semantic quality threshold not met"
            await update_project(
                pid,
                analysis=analysis,
                progress=100,
                status="error",
                status_message=(
                    f"Edit plan needs review after {quality_review.get('llm_attempt_count', 1)} "
                    f"AI attempt(s): {summary}"
                ),
            )
            return
        await update_project(pid, progress=82,
                             status_message="Building the edit plan and missing graphics...")
        pack_resolution = await ASSET_ORCHESTRATOR.resolve(
            quality_review.get("profile", "general"),
            [
                str(moment.get("query", ""))
                for moment in analysis.get("broll_moments", [])
                if isinstance(moment, dict)
            ],
        )
        analysis["asset_pack_resolution"] = {
            "source": pack_resolution.get("source"),
            "pack_id": pack_resolution.get("pack_id"),
            "pack_ids": pack_resolution.get("pack_ids", []),
            "counts": pack_resolution.get("counts", {}),
            "asset_count": len(pack_resolution.get("assets", [])),
        }
        pack_assets = pack_resolution.get("assets", [])
        moments_by_index = {
            int(moment.get("word_index", 0)): moment
            for moment in analysis.get("broll_moments", [])
            if isinstance(moment, dict)
        }
        resolved_pack_assets = []
        matched_request_indices = set()
        for request in analysis.get("asset_requests", []):
            word_index = int(request.get("word_index", 0))
            moment = moments_by_index.get(word_index, {})
            # Retrieval terms come from the explicit asset query. Broader
            # visual-intent prose can contain incidental words (for example
            # "UI") that would create a false semantic match.
            intent = str(moment.get("query", "")).strip() or " ".join(filter(None, [
                str(request.get("text", "")), str(request.get("subtext", "")),
                str(request.get("kind", "")).replace("_", " "),
            ]))
            match = rank_pack_assets(pack_assets, intent, limit=1)
            if not match:
                continue
            record = match[0]
            path = ASSET_MANAGER.resolve_renderable(LIBRARY_DIR / record["name"])
            if not path:
                continue
            resolved_pack_assets.append({
                "id": f"pack_{record['sha256'][:16]}_{word_index}",
                "name": record.get("original_name") or record["name"],
                "word_index": word_index, "provider": record["source_id"],
                "url": f"/api/library/file/{record['name']}", "video_url": f"file://{path}",
                "local_path": str(path), "thumbnail": f"/api/library/thumb/{record['name']}",
                "is_custom": False, "generated": False, "pack_id": record.get("pack_id"),
                "sha256": record["sha256"], "matched_terms": record.get("matched_terms", []),
            })
            matched_request_indices.add(word_index)
        analysis["resolved_pack_assets"] = resolved_pack_assets
        generation_requests = [
            request for request in analysis.get("asset_requests", [])
            if int(request.get("word_index", 0)) not in matched_request_indices
        ]
        generated_assets = await asyncio.to_thread(
            asset_generator.generate_assets,
            generation_requests, pid, LIBRARY_DIR,
            quality_review.get("profile", "general"),
        )
        approved_generated_assets = []
        for asset in generated_assets:
            try:
                await asyncio.to_thread(
                    ASSET_MANAGER.register_generated,
                    Path(asset["local_path"]), asset,
                )
                approved_generated_assets.append(asset)
            except AssetPolicyError as exc:
                Path(asset.get("local_path", "")).unlink(missing_ok=True)
                logger.error("Generated asset failed rights/byte registration: %s", exc)
        generated_assets = approved_generated_assets
        analysis["generated_assets"] = generated_assets
        # A generated graphic must always have a visible picker moment even if
        # the model returned slightly mismatched arrays.
        existing_indices = {m.get("word_index") for m in analysis.get("broll_moments", [])}
        for asset in generated_assets:
            if asset["word_index"] not in existing_indices:
                analysis.setdefault("broll_moments", []).append({
                    "word_index": asset["word_index"],
                    "query": asset["asset_kind"].replace("_", " "),
                    "reason": asset.get("reason") or "Klipped created a graphic for this beat.",
                    "visual_intent": "Clarify the idea without unrelated stock footage.",
                })
                existing_indices.add(asset["word_index"])
        await update_project(pid, analysis=analysis, progress=100, status="ready",
                             status_message="Ready to edit & render")
    except Exception as e:
        logger.exception("Analysis failed")
        await update_project(pid, status="error", status_message=f"Analysis failed: {e}")


@api.post("/projects/{pid}/analyze")
async def analyze(pid: str, bg: BackgroundTasks, body: Optional[AnalyzeBody] = None):
    proj = await get_project(pid)
    if proj["status"] in ("transcribing", "analyzing", "extracting_audio", "rendering"):
        return {"ok": True, "status": proj["status"], "already_running": True}
    body = body or AnalyzeBody()
    if body.training_profile_id:
        await _training_context(body.training_profile_id)
    await update_project(
        pid, status="queued", status_message="Queued for analysis...", progress=1,
        requested_profile=body.requested_profile, training_profile_id=body.training_profile_id,
    )
    bg.add_task(_run_analysis, pid)
    return {"ok": True, "status": "queued"}


# ---------- ASSET LIBRARY (user's own vault) ----------
LIBRARY_EXTS_VIDEO = {".mp4", ".mov", ".webm"}
LIBRARY_EXTS_IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
LIBRARY_EXTS_ALL = LIBRARY_EXTS_VIDEO | LIBRARY_EXTS_IMAGE


def _asset_kind(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in LIBRARY_EXTS_VIDEO: return "video"
    if ext in LIBRARY_EXTS_IMAGE: return "image"
    return "other"


@api.get("/library")
async def library_list():
    """List all assets in the user's personal library."""
    items = []
    for record in ASSET_MANAGER.published_records():
        p = LIBRARY_DIR / record["name"]
        aid = p.stem
        items.append({
            "id": f"lib_{aid}",
            "name": p.name,
            "kind": _asset_kind(p.name),
            "size": record["size"],
            "url": f"/api/library/file/{p.name}",
            "video_url": f"file://{p}",
            "local_path": str(p),
            "thumbnail": f"/api/library/thumb/{p.name}" if _asset_kind(p.name) == "image" else None,
            "is_custom": True,
            "provider": "library",
            "sha256": record["sha256"],
            "rights_status": record["rights_status"],
            "license_id": record["license_id"],
            "provenance": record["provenance"],
            "is_evidence": record.get("is_evidence", False),
        })
    return {"items": items}


@api.post("/library/upload")
async def library_upload(
    file: UploadFile = File(...),
    rights_status: str = Form("unknown"),
    rights_attestation: str = Form(""),
    license_id: str = Form(""),
):
    """Upload a single asset to the personal library."""
    fname = file.filename or "asset"
    ext = os.path.splitext(fname)[1].lower()
    if ext not in LIBRARY_EXTS_ALL:
        raise HTTPException(400, f"Unsupported asset type: {ext}")
    # Sanitize name
    stem = re.sub(r"[^\w.-]+", "_", os.path.splitext(fname)[0])[:60] or "asset"
    incoming = ASSET_QUARANTINE_DIR / "incoming" / uuid.uuid4().hex
    incoming.mkdir(parents=True, exist_ok=True)
    candidate = incoming / f"{stem}{ext}"
    total = await _save_upload(file, candidate, MAX_ASSET_BYTES)
    attested = rights_attestation.strip() == "I own or have commercial rights to this asset"
    effective_rights = "user_owned_attested" if rights_status == "user_owned_attested" and attested else "unknown"
    mime_type = (file.content_type or mimetypes.guess_type(candidate.name)[0] or "application/octet-stream").lower()
    entry = {
        "asset_id": uuid.uuid4().hex, "source_id": "user_owned_gaming",
        "relative_path": candidate.name, "sha256": sha256_file(candidate),
        "mime_type": mime_type, "rights_status": effective_rights,
        "license_id": license_id.strip() or ("user-attestation" if attested else "unknown"),
        "provenance": "direct_user_upload", "attribution": "User supplied",
        "niche": "gaming", "is_evidence": attested,
        "tags": [token.lower() for token in re.findall(r"[A-Za-z0-9]+", stem)[:12]],
    }
    try:
        record = await asyncio.to_thread(
            ASSET_MANAGER.ingest_file, incoming, entry,
            {"max_files": 250, "max_file_bytes": MAX_ASSET_BYTES, "max_total_bytes": 1024 * 1024 * 1024},
            audit_sample_rate=0,
        )
    except AssetPolicyError as exc:
        raise HTTPException(400, str(exc)) from exc
    if record.get("status") != "published":
        return {"ok": False, "status": "quarantined", "reason": record.get("reason"), "rights_proof_required": True}
    return {"ok": True, "name": record["name"], "size": record["size"], "kind": _asset_kind(record["name"]), "sha256": record["sha256"]}


@api.delete("/library/{name}")
async def library_delete(name: str):
    """Delete an asset from the library (safe against path traversal)."""
    safe = os.path.basename(name)
    if not ASSET_MANAGER.delete(safe):
        raise HTTPException(404)
    return {"ok": True}


@api.get("/library/file/{name}")
async def library_file(name: str):
    """Serve a library file for preview."""
    safe = os.path.basename(name)
    p = ASSET_MANAGER.resolve_renderable(LIBRARY_DIR / safe)
    if not p:
        raise HTTPException(404)
    kind = _asset_kind(safe)
    media_type = "video/mp4" if kind == "video" else "image/jpeg"
    if safe.lower().endswith(".mov"): media_type = "video/quicktime"
    elif safe.lower().endswith(".webm"): media_type = "video/webm"
    elif safe.lower().endswith(".png"): media_type = "image/png"
    elif safe.lower().endswith(".webp"): media_type = "image/webp"
    elif safe.lower().endswith(".gif"): media_type = "image/gif"
    elif safe.lower().endswith(".svg"): media_type = "image/svg+xml"
    return FileResponse(p, media_type=media_type)


@api.get("/library/thumb/{name}")
async def library_thumb(name: str):
    """Serve image asset as thumbnail (same as file for now)."""
    return await library_file(name)


@api.get("/asset-packs/status")
async def asset_pack_status():
    return ASSET_ORCHESTRATOR.status()


@api.post("/asset-packs/resolve")
async def asset_pack_resolve(niche: str = "gaming", tags: str = ""):
    selected_tags = [item.strip() for item in tags.split(",") if item.strip()]
    return await ASSET_ORCHESTRATOR.resolve(niche, selected_tags)


# ---------- B-ROLL SEARCH ----------
@api.get("/projects/{pid}/broll_search")
async def broll_search(pid: str, query: str, per_page: int = 6, orientation: str = "landscape"):
    """Resolve rights-approved niche-pack assets; arbitrary stock is disabled."""
    await get_project(pid)
    query = re.sub(r"\s+", " ", query).strip()[:80]
    if not query:
        raise HTTPException(400, "Search query is required")
    resolution = await ASSET_ORCHESTRATOR.resolve("gaming", query.split())
    results = []
    ranked_assets = rank_pack_assets(resolution.get("assets", []), query, limit=max(1, min(per_page, 20)))
    for record in ranked_assets:
        path = ASSET_MANAGER.resolve_renderable(LIBRARY_DIR / record["name"])
        if not path or _asset_kind(record["name"]) not in {"image", "video"}:
            continue
        results.append({
            "id": f"pack_{record['sha256'][:16]}", "provider": record["source_id"],
            "name": record["name"], "video_url": f"file://{path}", "local_path": str(path),
            "thumbnail": f"/api/library/thumb/{record['name']}" if _asset_kind(record["name"]) == "image" else None,
            "is_custom": False, "pack_id": record.get("pack_id"), "sha256": record["sha256"],
        })
    return {"query": query, "results": results, "counts": {"approved_pack": len(results)}, "resolution_source": resolution["source"]}


@api.post("/projects/{pid}/broll_upload")
async def broll_upload(
    pid: str,
    file: UploadFile = File(...),
    rights_status: str = Form("unknown"),
    rights_attestation: str = Form(""),
    license_id: str = Form(""),
):
    """Accept a user-uploaded B-roll clip for use in a render."""
    await get_project(pid)  # validate exists
    ext = os.path.splitext(file.filename or "clip.mp4")[1].lower() or ".mp4"
    if ext not in LIBRARY_EXTS_VIDEO:
        raise HTTPException(400, f"Unsupported B-roll type: {ext}")
    clip_id = f"user_{uuid.uuid4().hex[:8]}"
    incoming = ASSET_QUARANTINE_DIR / "incoming" / uuid.uuid4().hex
    incoming.mkdir(parents=True, exist_ok=True)
    dst = incoming / f"{pid}_{clip_id}{ext}"

    total = await _save_upload(file, dst, MAX_ASSET_BYTES)

    try:
        meta = vp.probe_video(str(dst))
    except Exception as e:
        dst.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not read B-roll: {str(e)[:200]}")

    attested = (
        rights_status == "user_owned_attested"
        and rights_attestation.strip() == "I own or have commercial rights to this asset"
    )
    entry = {
        "asset_id": clip_id, "source_id": "user_owned_gaming", "relative_path": dst.name,
        "sha256": sha256_file(dst),
        "mime_type": (file.content_type or mimetypes.guess_type(dst.name)[0] or "application/octet-stream").lower(),
        "rights_status": "user_owned_attested" if attested else "unknown",
        "license_id": license_id.strip() or ("user-attestation" if attested else "unknown"),
        "provenance": "direct_user_upload", "attribution": "User supplied",
        "niche": "gaming", "is_evidence": attested,
        "tags": [token.lower() for token in re.findall(r"[A-Za-z0-9]+", file.filename or "")[:12]],
    }
    try:
        record = await asyncio.to_thread(
            ASSET_MANAGER.ingest_file, incoming, entry,
            {"max_files": 250, "max_file_bytes": MAX_ASSET_BYTES, "max_total_bytes": 1024 * 1024 * 1024},
            audit_sample_rate=0,
        )
    except AssetPolicyError as exc:
        raise HTTPException(400, str(exc)) from exc
    if record.get("status") != "published":
        return {"ok": False, "status": "quarantined", "reason": record.get("reason"), "rights_proof_required": True}
    published = ASSET_MANAGER.resolve_renderable(LIBRARY_DIR / record["name"])
    if not published:
        raise HTTPException(400, "Asset registration failed closed")
    video_url = f"file://{published}"
    return {
        "ok": True,
        "id": clip_id,
        "duration": meta.get("duration", 0),
        "thumbnail": None,
        "video_url": video_url,
        "local_path": str(published),
        "width": meta.get("width", 0),
        "height": meta.get("height", 0),
        "user": "You",
        "is_custom": True,
        "sha256": record["sha256"],
        "rights_status": record["rights_status"],
        "license_id": record["license_id"],
    }


# ---------- VIRAL CLIPS ----------
@api.post("/projects/{pid}/viral_clips")
async def viral_clips(pid: str):
    """Ask LLM to find the 3-5 best viral-worthy short clip moments in this project."""
    proj = await get_project(pid)
    transcript = proj.get("transcript") or {}
    words = transcript.get("words", [])
    if not words:
        raise HTTPException(400, "Project not analyzed yet")
    keys = await get_keys()
    duration = float(proj.get("duration", 0))
    clips = await ai.extract_viral_clips(words, keys, duration)
    await update_project(pid, viral_clips=clips)
    return {"clips": clips}


# ---------- RENDER PIPELINE ----------
async def _run_render(pid: str, opts: RenderOptions):
    try:
        proj = await get_project(pid)
        await update_project(pid, status="rendering", progress=5,
                             status_message="Preparing render...", render_options=opts.model_dump())

        transcript = proj.get("transcript") or {}
        words = transcript.get("words", [])
        analysis = proj.get("analysis") or {}
        duration = float(proj.get("duration", 0))
        src_w = int(proj.get("width") or 1920) or 1920
        src_h = int(proj.get("height") or 1080) or 1080

        # Determine output canvas from aspect
        out_w, out_h = vp.aspect_target_size(opts.aspect, src_w, src_h)

        # Reconcile filler indices
        auto_fillers = set(analysis.get("filler_indices", []))
        auto_fillers -= set(opts.excluded_filler_indices)
        auto_fillers |= set(opts.added_filler_indices)
        filler_indices = list(auto_fillers) if opts.remove_fillers else []

        # Compute keep segments (may be trimmed to viral clip window below)
        keep = vp.build_keep_segments(words, filler_indices, duration)

        # If viral-clip mode: restrict keep to [clip_start, clip_end] range
        clip_start = opts.clip_start
        clip_end = opts.clip_end
        if clip_start is not None and clip_end is not None:
            trimmed = []
            for seg in keep:
                s = max(seg["start"], clip_start)
                e = min(seg["end"], clip_end)
                if e - s > 0.08:
                    trimmed.append({"start": s, "end": e})
            keep = trimmed or [{"start": clip_start, "end": clip_end}]

        await update_project(pid, progress=15, status_message="Cutting segments...")

        cut_path = str(DATA_DIR / "output" / f"{pid}_cut.mp4")
        await asyncio.to_thread(vp.cut_and_concat, proj["original_path"], keep, cut_path,
                                out_w, out_h, src_w, src_h, None, None)
        await update_project(pid, progress=45, status_message="Generating animated captions...")

        # Build ASS captions
        ass_path = None
        if opts.captions and words:
            ass_path = str(DATA_DIR / "subtitles" / f"{pid}.ass")
            emphasis_set = set(analysis.get("emphasis_indices", [])) if opts.zoom_ins else set()
            await asyncio.to_thread(vp.generate_ass, words, ass_path, opts.style, out_w, out_h,
                                    emphasis_set, keep)

        # SFX events (whoosh at each cut boundary in output timeline)
        sfx_events = []
        if opts.sfx:
            t = 0.0
            for seg in keep[:-1]:
                t += (seg["end"] - seg["start"])
                sfx_events.append(t)

        # B-roll events: user-selected
        broll_events = []
        if opts.broll and opts.selected_broll:
            await update_project(pid, progress=55, status_message="Downloading B-roll clips...")
            for i, sel in enumerate(opts.selected_broll):
                url = sel.get("video_url") or ""
                moment_word_idx = int(sel.get("word_index", 0))
                if not url:
                    continue
                # Never trust client flags or arbitrary DATA_DIR paths. Every
                # selected asset must resolve through the published registry.
                requested = sel.get("local_path") or (url[7:] if url.startswith("file://") else "")
                registered = ASSET_MANAGER.resolve_renderable(requested) if requested else None
                if not registered:
                    logger.warning("Rejected unregistered, quarantined, remote, or forged asset for project %s", pid)
                    continue
                local = str(registered)
                # Compute output time from word index remap
                if moment_word_idx < len(words):
                    orig_t = float(words[moment_word_idx].get("start", 0))
                else:
                    orig_t = 0
                # Simple remap
                offset = 0.0
                out_t = None
                for seg in keep:
                    s, e = seg["start"], seg["end"]
                    if orig_t < s:
                        out_t = offset
                        break
                    if orig_t <= e:
                        out_t = offset + (orig_t - s)
                        break
                    offset += (e - s)
                if out_t is None:
                    out_t = offset
                broll_events.append({
                    "local_path": local,
                    "out_start": max(0, out_t),
                    "out_duration": 3.5,
                    "fit": (
                        "full" if bool(ASSET_MANAGER._index()["assets"].get(Path(local).name, {}).get("generator")) else
                        "pip" if Path(local).suffix.lower() in LIBRARY_EXTS_IMAGE else
                        "cover"
                    ),
                })

        await update_project(pid, progress=70, status_message="Rendering final video...")

        # Choose output filename — separate for viral clips so main render is preserved
        if opts.clip_label:
            safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", opts.clip_label)[:40]
            output_path = str(DATA_DIR / "output" / f"{pid}_clip_{safe_label}.mp4")
        else:
            output_path = str(DATA_DIR / "output" / f"{pid}_final.mp4")

        await asyncio.to_thread(vp.render_final, cut_path, ass_path, sfx_events,
                                broll_events, SFX_DIR, output_path)

        # Never mark a broken or empty render as complete. This is the first
        # quality gate in the editing loop; later reference scoring can build
        # on top of a technically valid output.
        output_meta = await asyncio.to_thread(vp.probe_video, output_path)
        if output_meta.get("duration", 0) <= 0 or output_meta.get("width", 0) <= 0 or output_meta.get("size", 0) < 1024:
            raise RuntimeError("Render validation failed: output has no playable video stream")

        # Cleanup intermediate
        try:
            os.remove(cut_path)
        except Exception:
            pass

        try:
            render_review = await asyncio.to_thread(
                post_render_qa.review_render,
                Path(output_path), Path(ass_path) if ass_path else None,
                niche=analysis.get("quality_review", {}).get("profile", "general"),
            )
        except Exception as exc:
            render_review = {"schema_version": "klippd.post_render_qa.v1", "passed": False, "hard_fail": True, "issues": [{"code": "review_failed", "detail": str(exc)[:200]}]}
        update_fields = {"status": "done", "progress": 100,
                         "status_message": "Render complete!", "output_meta": output_meta,
                         "post_render_qa": render_review,
                         "render_review_required": not bool(render_review.get("passed"))}
        if opts.clip_label:
            # Track in viral_renders dict on project
            proj_now = await get_project(pid)
            vr = proj_now.get("viral_renders") or {}
            vr[opts.clip_label] = output_path
            update_fields["viral_renders"] = vr
            update_fields["last_clip_label"] = opts.clip_label
        else:
            update_fields["output_path"] = output_path
        await update_project(pid, **update_fields)
    except Exception as e:
        logger.exception("Render failed")
        await update_project(pid, status="error", status_message=f"Render failed: {e}")


@api.post("/projects/{pid}/render")
async def render(pid: str, opts: RenderOptions, bg: BackgroundTasks):
    proj = await get_project(pid)
    if not proj.get("transcript"):
        raise HTTPException(400, "Project not analyzed yet")
    if proj.get("status") in ("queued_render", "rendering"):
        return {"ok": True, "status": proj["status"], "already_running": True}
    if opts.clip_end is not None and opts.clip_end > float(proj.get("duration") or 0) + 0.1:
        raise HTTPException(400, "Clip end exceeds the source duration")
    await update_project(pid, status="queued_render",
                         status_message="Render queued...", progress=1)
    bg.add_task(_run_render, pid, opts)
    return {"ok": True, "status": "queued_render"}


# ---------- MEDIA ----------
def _clean_filename(name: str) -> str:
    """Strip original extension and unsafe chars so downloads are always .mp4"""
    stem = os.path.splitext(name or "video")[0]
    stem = re.sub(r"[^\w\s.-]+", "_", stem).strip() or "video"
    return stem[:80]


@api.get("/projects/{pid}/download")
async def download_final(pid: str, clip: Optional[str] = None):
    """Download the main render, or a viral clip if ?clip=<label> is given."""
    proj = await get_project(pid)
    base = _clean_filename(proj.get("name") or "video")
    if clip:
        out = (proj.get("viral_renders") or {}).get(clip)
        fname = f"{base}_{clip}.mp4"
    else:
        out = proj.get("output_path")
        fname = f"{base}_edited.mp4"
    if not out or not os.path.exists(out):
        raise HTTPException(404, "Output not ready")
    return FileResponse(out, media_type="video/mp4", filename=fname)


@api.get("/media/clip/{pid}/{clip_label}")
async def media_clip(pid: str, clip_label: str):
    """Stream a specific viral clip output."""
    proj = await get_project(pid)
    vr = proj.get("viral_renders") or {}
    out = vr.get(clip_label)
    if not out or not os.path.exists(out):
        raise HTTPException(404)
    return FileResponse(out, media_type="video/mp4")


@api.get("/media/original/{pid}")
async def media_original(pid: str):
    proj = await get_project(pid)
    p = proj.get("original_path")
    if not p or not os.path.exists(p):
        raise HTTPException(404)
    return FileResponse(p, media_type=mimetypes.guess_type(p)[0] or "application/octet-stream")


@api.get("/media/output/{pid}")
async def media_output(pid: str):
    proj = await get_project(pid)
    p = proj.get("output_path")
    if not p or not os.path.exists(p):
        raise HTTPException(404)
    return FileResponse(p, media_type="video/mp4")


# ---------- APP WIRING ----------
register_premium_routes(api, db, USER_ID, get_project, update_project)
app.include_router(api)
cors_origins = [origin.strip() for origin in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_credentials="*" not in cors_origins,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    if client is not None:
        client.close()
