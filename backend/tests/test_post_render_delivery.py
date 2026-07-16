import asyncio

import pytest
from fastapi import HTTPException

from editorial_quality import post_render_qa_fingerprint
import server


def test_download_blocks_hard_post_render_failure_until_current_review_is_acknowledged(monkeypatch):
    review = {
        "passed": False,
        "hard_fail": True,
        "issues": [{"code": "render_corrupt", "severity": "critical"}],
    }
    project = {
        "id": "project-qa",
        "name": "QA render",
        "output_path": "/renders/final.mp4",
        "post_render_qa": review,
    }

    async def get_project(_pid):
        return project

    monkeypatch.setattr(server, "get_project", get_project)
    monkeypatch.setattr(server, "_safe_project_file", lambda *args: "download-response")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(server.download_final("project-qa"))
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "post_render_qa_hard_failure"
    assert exc_info.value.detail["issues"][0]["code"] == "render_corrupt"

    project["post_render_qa_acknowledgment"] = {
        "acknowledged": True,
        "qa_fingerprint": post_render_qa_fingerprint(review),
        "reason": "Reviewed and accepted for this delivery.",
    }
    assert asyncio.run(server.download_final("project-qa")) == "download-response"
