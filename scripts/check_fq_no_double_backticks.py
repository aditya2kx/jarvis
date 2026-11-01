#!/usr/bin/env python3
"""CI/local gate: never wrap ``fq()`` in an extra pair of backticks (Issue #467).

``core.datastore.fq(table)`` already returns `` `project.dataset.table` ``. Writing
`` `{fq('t')}` `` produces nested backticks that BigQuery rejects with
``400 Syntax error: Invalid empty identifier`` — the #465 hotfix. This gate fails
the PR before that ships again.
"""

from __future__ import annotations

import pathlib
import re
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
_ROOTS = ("core", "cloud", "agents", "skills", "scripts")
_SUFFIXES = {".py"}
_SKIP_PARTS = {"node_modules", ".next", "__pycache__"}
_SELF = pathlib.Path(__file__).resolve()
_SELF_TEST = _SELF.with_name("test_check_fq_no_double_backticks.py")

# Source that double-wraps: `{fq(  or  `{fq"
DOUBLE_WRAP = re.compile(r"`\{fq\(")


def assert_no_double_wrapped_fq(sql_or_source: str, *, where: str = "<string>") -> None:
    """Raise AssertionError if ``sql_or_source`` contains a double-wrapped fq() call."""
    m = DOUBLE_WRAP.search(sql_or_source)
    if m:
        line = sql_or_source.count("\n", 0, m.start()) + 1
        raise AssertionError(
            f"{where}:{line}: double-wrapped fq() — fq() already returns backtick-quoted "
            f"identifiers; use {{fq(...)}} not `{{fq(...)}}`"
        )


def violations(path: pathlib.Path, text: str) -> list[str]:
    out = []
    for m in DOUBLE_WRAP.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        out.append(
            f"{path}:{line}: double-wrapped fq() — use {{fq(...)}} not `{{fq(...)}}` "
            f"(Issue #467 / #465)"
        )
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
    errors: list[str] = []
    for p in _files():
        errors += violations(p.relative_to(_REPO), p.read_text(errors="ignore"))
    if errors:
        print("double-wrapped fq() found (Issue #467 — fq() already backtick-quotes):")
        for e in errors:
            print(f"  {e}")
        return 1
    print("fq() backticks OK (no `{fq(` wraps)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
