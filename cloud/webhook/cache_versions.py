"""Operator Console query-cache versions from BigQuery's audit log.

Every committed write to the `bhaga` dataset — nightly job, Slack command, console,
manual `bq` fix, a future Square/ClickUp webhook — produces an audit-log entry. A log
sink sends those to Pub/Sub, which pushes them to bhaga-webhook `/cache/bq-write`; this
module turns each entry into a version bump in Firestore `console_cache_versions/{table}`.
The console keys every cached result on those versions, so a bump retires the old
result for every viewer at once (docs/operator-console/ARCHITECTURE.md §15).

Pure parsing lives here so it is unit-testable without Flask or Firestore.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Iterable

VERSIONS_COLLECTION = "console_cache_versions"
SCHEMA_DOC = "__schema__"
META_DOC = ("console_cache_meta", "audit_feed")

_TABLE_EVENTS = ("tableDataChange", "tableCreation", "tableChange", "tableDeletion")
# View / table definition and routine changes alter what a query reads, not just data.
_SCHEMA_EVENTS = ("tableCreation", "tableChange", "tableDeletion")
_ROUTINE_EVENTS = ("routineCreation", "routineChange", "routineDeletion")

_RESOURCE = re.compile(r"^projects/([^/]+)/datasets/([^/]+)/(tables|routines)/([^/]+)$")


def decode_push(envelope: dict) -> dict | None:
    """Pub/Sub push envelope → the LogEntry it carries (None when malformed)."""
    data = ((envelope or {}).get("message") or {}).get("data")
    if not data:
        return None
    try:
        return json.loads(base64.b64decode(data))
    except (ValueError, TypeError):
        return None


def docs_to_bump(entry: dict, project: str, dataset: str) -> list[str]:
    """Version docs a BigQuery audit-log entry moves; [] for anything outside `dataset`."""
    proto = (entry or {}).get("protoPayload") or {}
    meta = proto.get("metadata") or {}
    m = _RESOURCE.match(proto.get("resourceName") or "")
    if not m or m.group(1) != project or m.group(2) != dataset:
        return []
    kind, name = m.group(3), m.group(4)
    # Partition decorators ("t$20261021") still write the base table.
    name = name.split("$", 1)[0]
    if kind == "routines":
        return [SCHEMA_DOC] if any(k in meta for k in _ROUTINE_EVENTS) else []
    out: list[str] = []
    if any(k in meta for k in _TABLE_EVENTS):
        out.append(name)
    if any(k in meta for k in _SCHEMA_EVENTS):
        out.append(SCHEMA_DOC)
    return out


def bump(db, docs: Iterable[str], *, source: str, firestore_module) -> None:
    """Atomic increments — concurrent bumps from overlapping pushes never lose one."""
    batch = db.batch()
    n = 0
    for name in dict.fromkeys(docs):
        ref = db.collection(VERSIONS_COLLECTION).document(name)
        batch.set(
            ref,
            {"v": firestore_module.Increment(1), "updated_at": firestore_module.SERVER_TIMESTAMP, "source": source},
            merge=True,
        )
        n += 1
    if n:
        batch.set(
            db.collection(META_DOC[0]).document(META_DOC[1]),
            {"last_event_at": firestore_module.SERVER_TIMESTAMP, "last_docs": list(dict.fromkeys(docs))},
            merge=True,
        )
        batch.commit()
