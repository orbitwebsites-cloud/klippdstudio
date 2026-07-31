#!/usr/bin/env python3
"""Purge pre-isolation "orphan" library assets.

Before per-user isolation landed, user-uploaded B-roll/clips were stored in the
shared asset library with no owner recorded. After the fix those un-owned uploads
no longer appear in anyone's library, but the files still sit on disk. This script
removes exactly those leaked uploads.

What it deletes:
    records where provenance == "direct_user_upload" AND owner is empty/missing.

What it NEVER touches:
    - shared stock-pack assets (provenance != "direct_user_upload"), and
    - uploads that already have an owner (post-fix uploads).

Usage (run on the server where DATA_DIR is mounted, from the backend/ dir):
    python tools/purge_orphan_library_assets.py            # dry run: just lists
    python tools/purge_orphan_library_assets.py --apply    # actually delete

DATA_DIR is read from the environment exactly like the server, so point it at the
same value your backend uses (e.g. DATA_DIR=/app/data).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow running from anywhere: make the backend package importable.
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from asset_pack_manager import AssetPackManager  # noqa: E402


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", str(BACKEND_DIR.parent / "data"))).resolve()


def _is_orphan_upload(record: dict) -> bool:
    return record.get("provenance") == "direct_user_upload" and not record.get("owner")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually delete (default is a dry run).")
    args = parser.parse_args()

    data_dir = _data_dir()
    library_dir = data_dir / "library"
    quarantine_dir = data_dir / "quarantine"
    print(f"DATA_DIR   = {data_dir}")
    print(f"LIBRARY_DIR= {library_dir}")
    if not library_dir.exists():
        print("No library directory found — nothing to purge.")
        return 0

    manager = AssetPackManager(library_dir, quarantine_dir)
    # Raw index access is intentional here: we want every record, including any
    # whose bytes are stale, not only the currently-published ones.
    index = manager._index()  # noqa: SLF001
    assets = index.get("assets", {})

    candidates = {name: rec for name, rec in assets.items() if _is_orphan_upload(rec)}
    total = len(assets)
    print(f"\n{total} asset record(s) total; {len(candidates)} orphan upload(s) to remove.\n")

    if not candidates:
        print("Nothing to clean. All uploads are owned or are shared stock assets.")
        return 0

    freed = 0
    for name, rec in sorted(candidates.items()):
        size = int(rec.get("size") or 0)
        freed += size
        print(f"  {'DELETE' if args.apply else 'would delete'}  {name}  ({size} bytes)")

    print(f"\n{'Freed' if args.apply else 'Would free'} ~{freed / (1024 * 1024):.1f} MB across {len(candidates)} file(s).")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to delete.")
        return 0

    removed = 0
    for name in list(candidates):
        try:
            manager.delete(name)
        except Exception as exc:  # keep going; report at the end
            print(f"  ! failed to delete {name}: {exc}")
            continue
        # delete() drops the index entry and unlinks the file when resolvable;
        # sweep any leftover bytes for stale records too.
        leftover = library_dir / os.path.basename(name)
        try:
            leftover.unlink(missing_ok=True)
        except OSError:
            pass
        removed += 1

    print(f"\nDone. Removed {removed}/{len(candidates)} orphan upload(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
