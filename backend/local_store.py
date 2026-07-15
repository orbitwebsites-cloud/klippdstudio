"""Tiny persistent document store for the single-user MVP.

It implements only the Motor collection operations used by server.py. The file
must live on a persistent volume in production, and the service must run with a
single web worker. MongoDB remains supported when MONGO_URL is configured.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def _matches(document: Dict[str, Any], query: Dict[str, Any]) -> bool:
    return all(document.get(key) == value for key, value in query.items())


def _exclude_path(document: Dict[str, Any], dotted: str) -> None:
    parts = dotted.split(".")
    cursor = document
    for part in parts[:-1]:
        value = cursor.get(part)
        if not isinstance(value, dict):
            return
        cursor = value
    cursor.pop(parts[-1], None)


class LocalCursor:
    def __init__(self, documents, projection=None):
        self.documents = copy.deepcopy(documents)
        self.projection = projection or {}

    def sort(self, field: str, direction: int):
        self.documents.sort(key=lambda item: item.get(field, ""), reverse=direction < 0)
        return self

    async def to_list(self, length: int):
        output = self.documents[:length]
        excluded = [key for key, value in self.projection.items() if value == 0]
        for document in output:
            for path in excluded:
                _exclude_path(document, path)
        return output


class LocalCollection:
    def __init__(self, database: "LocalDatabase", name: str):
        self.database = database
        self.name = name

    async def find_one(self, query: Dict[str, Any], projection: Optional[Dict[str, int]] = None):
        async with self.database.lock:
            for document in self.database.data.get(self.name, []):
                if _matches(document, query):
                    result = copy.deepcopy(document)
                    for path, value in (projection or {}).items():
                        if value == 0:
                            _exclude_path(result, path)
                    return result
        return None

    async def insert_one(self, document: Dict[str, Any]):
        async with self.database.lock:
            self.database.data.setdefault(self.name, []).append(copy.deepcopy(document))
            self.database._flush()
        return {"acknowledged": True}

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        async with self.database.lock:
            documents = self.database.data.setdefault(self.name, [])
            target = next((doc for doc in documents if _matches(doc, query)), None)
            if target is None and upsert:
                target = copy.deepcopy(query)
                documents.append(target)
            if target is not None:
                target.update(copy.deepcopy(update.get("$set", {})))
                self.database._flush()
        return {"acknowledged": True}

    async def delete_one(self, query: Dict[str, Any]):
        async with self.database.lock:
            documents = self.database.data.setdefault(self.name, [])
            for index, document in enumerate(documents):
                if _matches(document, query):
                    documents.pop(index)
                    self.database._flush()
                    break
        return {"acknowledged": True}

    def find(self, query: Dict[str, Any], projection: Optional[Dict[str, int]] = None):
        documents = [doc for doc in self.database.data.get(self.name, []) if _matches(doc, query)]
        return LocalCursor(documents, projection)


class LocalDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (OSError, ValueError):
            self.data = {}
        self.projects = LocalCollection(self, "projects")
        self.settings = LocalCollection(self, "settings")
        self.training_references = LocalCollection(self, "training_references")
        self.training_profiles = LocalCollection(self, "training_profiles")

    def _flush(self):
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.path)

    async def command(self, name: str):
        if name != "ping":
            raise ValueError("Unsupported local database command")
        return {"ok": 1}
