#!/usr/bin/env python3
"""CI/local gate: order-reco history tables are append-only (Issue #350).

`inventory_order_reco_history`, `inventory_order_reco_runs` and
`inventory_edit_log` (migration 080) are the audit trail for every order
recommendation generation and every operator edit to its inputs. The operator
requirement is "always have history, never delete records", so nothing in the
repo may DELETE / UPDATE / MERGE / TRUNCATE / DROP them, and the tables must not
carry a partition expiration (which would delete rows silently).

Scans SQL migrations, Python and the Operator Console TypeScript.
"""

from __future__ import annotations

import pathlib
import re
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
APPEND_ONLY = ("inventory_order_reco_history", "inventory_order_reco_runs", "inventory_edit_log")
_ROOTS = ("core", "cloud", "agents", "skills", "scripts", "apps/operator-console")
_SUFFIXES = {".sql", ".py", ".ts", ".tsx"}
_SKIP_PARTS = {"node_modules", ".next", "__pycache__"}
_SELF = pathlib.Path(__file__).resolve()
_SELF_TEST = _SELF.with_name("test_check_append_only_history.py")

_TABLES = "|".join(APPEND_ONLY)
MUTATION_RE = re.compile(
    r"\b(DELETE\s+FROM|UPDATE|MERGE(?:\s+INTO)?|TRUNCATE\s+TABLE|DROP\s+TABLE(?:\s+IF\s+EXISTS)?)"
    rf"\s+[^;\n]{{0,120}}?(?<![\w-])({_TABLES})\b"
)
EXPIRY_RE = re.compile(r"partition_expiration_days", re.IGNORECASE)


def violations(path: pathlib.Path, text: str) -> list[str]:
    out = []
    for m in MUTATION_RE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        out.append(f"{path}:{line}: {m.group(1).split()[0]} on append-only {m.group(2)}")
    if path.suffix == ".sql" and EXPIRY_RE.search(text) and any(t in text for t in APPEND_ONLY):
        out.append(f"{path}: partition_expiration_days alongside an append-only history table")
    return out


def _files() -> list[pathlib.Path]:
    files = []
    for root in _ROOTS:
        base = _REPO / root
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.suffix in _SUFFIXES and p.is_file() and not (_SKIP_PARTS & set(p.parts)):
                if p.resolve() not in (_SELF, _SELF_TEST):
                    files.append(p)
    return files


def main() -> int:
    errors = []
    for p in _files():
        errors += violations(p.relative_to(_REPO), p.read_text(errors="ignore"))
    if errors:
        print("append-only history violated (Issue #350 — never delete order-reco history):")
        for e in errors:
            print(f"  {e}")
        return 1
    print(f"append-only history OK ({', '.join(APPEND_ONLY)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
