"""E2E backend tests for AI Video Editor. Tests health, projects, analyze, render, download."""
import os
import time
import requests
import pytest

if os.environ.get("RUN_BACKEND_E2E") != "1":
    pytest.skip(
        "Set RUN_BACKEND_E2E=1 with a running API, FFmpeg test video, and provider keys.",
        allow_module_level=True,
    )

BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
API = f"{BASE_URL}/api"
TEST_VIDEO = "/tmp/test.mp4"


@pytest.fixture(scope="session")
def project_id():
    """Upload once, reuse for downstream tests."""
    with open(TEST_VIDEO, "rb") as f:
        r = requests.post(f"{API}/projects/upload", files={"file": ("test.mp4", f, "video/mp4")}, timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("id")
    assert data.get("duration", 0) > 0
    assert data.get("width") == 640
    assert data.get("height") == 360
    return data["id"]


# ---------- Root / Health ----------
class TestRootAndHealth:
    def test_root(self):
        r = requests.get(f"{API}/", timeout=10)
        assert r.status_code == 200
        j = r.json()
        assert j["ok"] is True
        assert j["app"] == "Klipped Studio"

    def test_health(self):
        r = requests.get(f"{API}/health", timeout=10)
        assert r.status_code == 200
        j = r.json()
        assert j["ok"] is True
        assert all(j["checks"].values())


# ---------- Projects CRUD ----------
class TestProjects:
    def test_upload_and_list(self, project_id):
        r = requests.get(f"{API}/projects", timeout=10)
        assert r.status_code == 200
        items = r.json()
        assert any(p["id"] == project_id for p in items)

    def test_project_detail(self, project_id):
        r = requests.get(f"{API}/projects/{project_id}", timeout=10)
        assert r.status_code == 200
        j = r.json()
        assert j["id"] == project_id
        assert j["status"] in ("uploaded", "queued", "ready", "extracting_audio", "transcribing", "analyzing")

    def test_media_original(self, project_id):
        r = requests.get(f"{API}/media/original/{project_id}", timeout=15, stream=True)
        assert r.status_code == 200
        assert "video" in r.headers.get("content-type", "").lower()
        r.close()


# ---------- Analyze + Render pipeline (merged so loadscope keeps them on same worker) ----------
class TestAnalyzeAndRender:
    def test_analyze_and_wait_ready(self, project_id):
        r = requests.post(f"{API}/projects/{project_id}/analyze", timeout=15)
        assert r.status_code == 200
        # Poll
        deadline = time.time() + 90
        last_status = None
        last_msg = None
        while time.time() < deadline:
            d = requests.get(f"{API}/projects/{project_id}", timeout=10).json()
            last_status = d.get("status")
            last_msg = d.get("status_message")
            if last_status in ("ready", "error"):
                break
            time.sleep(2)
        assert last_status == "ready", f"status={last_status} msg={last_msg}"
        # transcript exists
        d = requests.get(f"{API}/projects/{project_id}", timeout=10).json()
        assert d.get("transcript") is not None
        assert isinstance(d["transcript"].get("words", []), list)
        assert d.get("analysis") is not None
        for k in ("filler_indices", "emphasis_indices", "broll_moments", "title", "summary"):
            assert k in d["analysis"], f"missing {k}"

    def test_render_and_download(self, project_id):
        # Ensure ready
        d = requests.get(f"{API}/projects/{project_id}", timeout=10).json()
        assert d.get("status") == "ready", f"prereq failed: {d.get('status')}"
        opts = {
            "style": "tiktok",
            "remove_fillers": True,
            "captions": True,
            "sfx": False,
            "zoom_ins": True,
            "broll": False,
        }
        r = requests.post(f"{API}/projects/{project_id}/render", json=opts, timeout=15)
        assert r.status_code == 200
        deadline = time.time() + 120
        last = None
        while time.time() < deadline:
            d = requests.get(f"{API}/projects/{project_id}", timeout=10).json()
            last = d.get("status")
            if last in ("done", "error"):
                break
            time.sleep(2)
        assert last == "done", f"render final status={last} msg={d.get('status_message')}"
        assert d.get("output_path")
        r2 = requests.get(f"{API}/projects/{project_id}/download", timeout=30, stream=True)
        assert r2.status_code == 200
        assert r2.headers.get("content-type") == "video/mp4"
        r2.close()
        r3 = requests.get(f"{API}/media/output/{project_id}", timeout=30, stream=True)
        assert r3.status_code == 200
        r3.close()


# ---------- B-roll search ----------
class TestBroll:
    def test_broll_search(self, project_id):
        r = requests.get(f"{API}/projects/{project_id}/broll_search", params={"query": "city"}, timeout=30)
        assert r.status_code == 200
        j = r.json()
        assert j["query"] == "city"
        assert isinstance(j["results"], list)
        assert len(j["results"]) >= 1
        first = j["results"][0]
        for k in ("id", "thumbnail", "video_url"):
            assert k in first


# ---------- Render pipeline ----------
class _TestRenderDisabledSplit:
    def test_render_and_download(self, project_id):
        # Ensure ready
        d = requests.get(f"{API}/projects/{project_id}", timeout=10).json()
        assert d.get("status") == "ready", f"prereq failed: {d.get('status')}"
        opts = {
            "style": "tiktok",
            "remove_fillers": True,
            "captions": True,
            "sfx": False,
            "zoom_ins": True,
            "broll": False,
        }
        r = requests.post(f"{API}/projects/{project_id}/render", json=opts, timeout=15)
        assert r.status_code == 200
        # Poll
        deadline = time.time() + 120
        last = None
        while time.time() < deadline:
            d = requests.get(f"{API}/projects/{project_id}", timeout=10).json()
            last = d.get("status")
            if last in ("done", "error"):
                break
            time.sleep(2)
        assert last == "done", f"render final status={last} msg={d.get('status_message')}"
        assert d.get("output_path")

        # Download
        r2 = requests.get(f"{API}/projects/{project_id}/download", timeout=30, stream=True)
        assert r2.status_code == 200
        assert r2.headers.get("content-type") == "video/mp4"
        r2.close()

        # Media output
        r3 = requests.get(f"{API}/media/output/{project_id}", timeout=30, stream=True)
        assert r3.status_code == 200
        r3.close()


# ---------- Delete ----------
class TestDelete:
    def test_delete_project(self, project_id):
        r = requests.delete(f"{API}/projects/{project_id}", timeout=10)
        assert r.status_code == 200
        r2 = requests.get(f"{API}/projects/{project_id}", timeout=10)
        assert r2.status_code == 404
