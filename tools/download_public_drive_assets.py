"""Denied-source notice for the former Google Drive downloader.

This command intentionally performs no network request and downloads nothing.
The referenced pack includes an uploader disclaimer stating that the uploader
does not own the files and supplies no licenses.
"""
from __future__ import annotations

import json
from pathlib import Path


SOURCE_ID = "drive_sslixmc_editing_pack"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    decision_path = root / "training" / "assets" / "sources" / f"{SOURCE_ID}.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    print(json.dumps({
        "ok": False,
        "source_id": SOURCE_ID,
        "status": decision["status"],
        "network_requests": 0,
        "downloaded": 0,
        "allowed_actions": decision["allowed_actions"],
        "next_step": "Request per-asset rights proof or use an approved replacement source.",
    }, indent=2))


if __name__ == "__main__":
    main()
