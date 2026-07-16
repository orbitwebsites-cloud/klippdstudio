import asyncio

from local_store import LocalDatabase


def test_local_store_supports_conditional_status_updates(tmp_path):
    db = LocalDatabase(tmp_path / "local.json")
    asyncio.run(db.projects.insert_one({"id": "p1", "status": "ready"}))

    first = asyncio.run(db.projects.update_one(
        {"id": "p1", "status": {"$nin": ["queued_render", "rendering"]}},
        {"$set": {"status": "queued_render"}},
    ))
    second = asyncio.run(db.projects.update_one(
        {"id": "p1", "status": {"$nin": ["queued_render", "rendering"]}},
        {"$set": {"status": "queued_render"}},
    ))

    assert first["matched_count"] == 1
    assert second["matched_count"] == 0
    assert asyncio.run(db.projects.find_one({"id": "p1"}))["status"] == "queued_render"
