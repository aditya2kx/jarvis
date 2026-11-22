"""Storage behind the Tasks API. Nothing outside this package reads these collections.

Two implementations of one small document interface: ``MemoryStore`` (tests, local runs)
and ``FirestoreStore`` (prod, named database). Photos go through ``PhotoStore`` the same way.
"""

from __future__ import annotations

import copy
import os
import pathlib
from typing import Iterable, Protocol

from google.cloud import firestore, storage
from google.cloud.firestore_v1.base_query import FieldFilter

STORES = "stores"
STAFF = "staff"
ACCOUNTS = "accounts"  # Google accounts allowed in, keyed by email
SHOP_CODES = "shop_codes"  # per store: the shop code's hash and generation
GUARDS = "guards"
POSTS = "posts"
WEBHOOKS = "webhooks"
INBOX = "inbox"  # counters that slow down guessing
TEMPLATES = "templates"
TEMPLATE_HISTORY = "template_history"
TASKS = "tasks"
COUNTS = "counts"
SHIFTS = "shared_shifts"  # read-only shared data, written by shared_sync only
ALERTS = "shift_alerts"
JOBS = "jobs"
TRACKERS = "trackers"  # projects: private action-item lists, apart from any store, and who is on each
TRACKER_ITEMS = "tracker_items"
FEEDBACK = "feedback"  # problems and ideas from the crew, with what was on screen and their last steps

_BATCH = 400


class DocStore(Protocol):
    def get(self, coll: str, doc_id: str) -> dict | None: ...
    def set(self, coll: str, doc_id: str, doc: dict, merge: bool = False) -> None: ...
    def set_many(self, coll: str, docs: Iterable[dict], merge: bool = False) -> int: ...
    def create(self, coll: str, doc_id: str, doc: dict) -> bool: ...
    def delete(self, coll: str, doc_id: str) -> None: ...
    def query(self, coll: str, **eq) -> list[dict]: ...
    def query_fields(self, coll: str, fields: Iterable[str], **eq) -> list[dict]: ...
    def count(self, coll: str, **eq) -> int: ...


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, dict]] = {}

    def get(self, coll, doc_id):
        d = self.data.get(coll, {}).get(doc_id)
        return copy.deepcopy(d) if d is not None else None

    def set(self, coll, doc_id, doc, merge=False):
        c = self.data.setdefault(coll, {})
        if merge and doc_id in c:
            c[doc_id] = {**c[doc_id], **copy.deepcopy(doc)}
        else:
            c[doc_id] = copy.deepcopy(doc)

    def set_many(self, coll, docs, merge=False):
        n = 0
        for d in docs:
            self.set(coll, d["id"], d, merge=merge)
            n += 1
        return n

    def create(self, coll, doc_id, doc):
        c = self.data.setdefault(coll, {})
        if doc_id in c:
            return False
        c[doc_id] = copy.deepcopy(doc)
        return True

    def delete(self, coll, doc_id):
        self.data.get(coll, {}).pop(doc_id, None)

    def query(self, coll, **eq):
        return [copy.deepcopy(d) for d in self.data.get(coll, {}).values()
                if all(d.get(k) == v for k, v in eq.items())]

    def query_fields(self, coll, fields, **eq):
        keep = set(fields)
        return [{k: v for k, v in d.items() if k in keep} for d in self.query(coll, **eq)]

    def count(self, coll, **eq):
        return len(self.query(coll, **eq))


class FirestoreStore:
    """Collections live in a database of their own, so no other service's rules or data mix in."""

    def __init__(self, project: str | None = None, database: str | None = None) -> None:
        self.db = firestore.Client(
            project=project or os.environ.get("GOOGLE_CLOUD_PROJECT"),
            database=database or os.environ.get("STORE_TASKS_FIRESTORE_DB", "storetasks"),
        )

    def get(self, coll, doc_id):
        snap = self.db.collection(coll).document(doc_id).get()
        return snap.to_dict() if snap.exists else None

    def set(self, coll, doc_id, doc, merge=False):
        self.db.collection(coll).document(doc_id).set(doc, merge=merge)

    def set_many(self, coll, docs, merge=False):
        n = 0
        batch = self.db.batch()
        for d in docs:
            batch.set(self.db.collection(coll).document(d["id"]), d, merge=merge)
            n += 1
            if n % _BATCH == 0:
                batch.commit()
                batch = self.db.batch()
        if n % _BATCH:
            batch.commit()
        return n

    def create(self, coll, doc_id, doc):
        try:
            self.db.collection(coll).document(doc_id).create(doc)
            return True
        except Exception as e:  # google.api_core.exceptions.AlreadyExists / Conflict
            if e.__class__.__name__ in ("AlreadyExists", "Conflict"):
                return False
            raise

    def delete(self, coll, doc_id):
        self.db.collection(coll).document(doc_id).delete()

    def _q(self, coll, eq):
        q = self.db.collection(coll)
        for k, v in eq.items():
            q = q.where(filter=FieldFilter(k, "==", v))
        return q

    def query(self, coll, **eq):
        return [s.to_dict() for s in self._q(coll, eq).stream()]

    def query_fields(self, coll, fields, **eq):
        # Firestore sends and decodes only these fields; decoding is most of a list read's CPU.
        return [s.to_dict() for s in self._q(coll, eq).select(list(fields)).stream()]

    def count(self, coll, **eq):
        return int(self._q(coll, eq).count().get()[0][0].value)


class PhotoStore(Protocol):
    def put(self, path: str, data: bytes, content_type: str) -> None: ...
    def get(self, path: str) -> tuple[bytes, str] | None: ...
    def paths(self, prefix: str) -> list[str]: ...


class DirPhotoStore:
    def __init__(self, root: str) -> None:
        self.root = pathlib.Path(root)

    def put(self, path, data, content_type):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, path):
        p = (self.root / path).resolve()
        if self.root.resolve() not in p.parents or not p.is_file():
            return None
        return p.read_bytes(), "image/jpeg"

    def paths(self, prefix):
        base = self.root / prefix
        return [str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()] if base.exists() else []


class GcsPhotoStore:
    def __init__(self, bucket: str) -> None:
        self.bucket = storage.Client().bucket(bucket)

    def put(self, path, data, content_type):
        self.bucket.blob(path).upload_from_string(data, content_type=content_type)

    def get(self, path):
        blob = self.bucket.get_blob(path)
        if blob is None:
            return None
        return blob.download_as_bytes(), blob.content_type or "image/jpeg"

    def paths(self, prefix):
        return [b.name for b in self.bucket.list_blobs(prefix=prefix)]
