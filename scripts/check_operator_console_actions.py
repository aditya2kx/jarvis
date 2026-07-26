#!/usr/bin/env python3
"""Gate: every operator-console mutating *Action is registered and UX-wired.

1. Every ``export async function <Name>Action`` under
   ``apps/operator-console/app/**/actions.ts`` must appear in
   ``apps/operator-console/lib/actions/registry.ts``.
2. Every client file under ``app/`` or ``components/`` that calls ``*Action(``
   must import ``useConsoleAction`` (Issue #175 shared feedback shell).

Allowlist: tests, the hook itself, ActionToast, registry, types, server-only
actions modules (they define actions, they don't call the client hook).
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
_APP = _REPO / "apps" / "operator-console"
_REGISTRY = _APP / "lib" / "actions" / "registry.ts"
_ACTIONS_GLOB = "app/**/actions.ts"
_CLIENT_ROOTS = (_APP / "app", _APP / "components")

_EXPORT_RE = re.compile(
    r"^export\s+async\s+function\s+(\w+Action)\s*\(",
    re.MULTILINE,
)
_REGISTRY_NAME_RE = re.compile(r'name:\s*"(\w+Action)"')
_CALL_RE = re.compile(r"\b(\w+Action)\s*\(")
_IMPORT_HOOK_RE = re.compile(
    r"""from\s+["']@/lib/actions/useConsoleAction["']"""
)

_CLIENT_ALLOWLIST = {
    "lib/actions/useConsoleAction.ts",
    "lib/actions/ActionToast.tsx",
    "lib/actions/types.ts",
    "lib/actions/registry.ts",
}


def _rel(path: pathlib.Path) -> str:
    return str(path.relative_to(_APP))


def exported_actions() -> dict[str, pathlib.Path]:
    found: dict[str, pathlib.Path] = {}
    for path in (_APP).glob(_ACTIONS_GLOB):
        text = path.read_text(encoding="utf-8")
        for name in _EXPORT_RE.findall(text):
            found[name] = path
    return found


def registry_names() -> set[str]:
    text = _REGISTRY.read_text(encoding="utf-8")
    return set(_REGISTRY_NAME_RE.findall(text))


def client_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for root in _CLIENT_ROOTS:
        for path in root.rglob("*"):
            if path.suffix not in {".ts", ".tsx"}:
                continue
            rel = _rel(path)
            if rel in _CLIENT_ALLOWLIST:
                continue
            if rel.endswith("/actions.ts") or rel.endswith("\\actions.ts"):
                continue
            if "/__tests__/" in rel.replace("\\", "/") or rel.endswith(".test.ts") or rel.endswith(".test.tsx"):
                continue
            out.append(path)
    return out


def check() -> list[str]:
    errors: list[str] = []
    exported = exported_actions()
    registered = registry_names()

    missing = sorted(set(exported) - registered)
    if missing:
        errors.append(
            "actions missing from lib/actions/registry.ts: " + ", ".join(missing)
        )
    extra = sorted(registered - set(exported))
    if extra:
        errors.append(
            "registry names with no export in app/**/actions.ts: " + ", ".join(extra)
        )

    for path in client_files():
        text = path.read_text(encoding="utf-8")
        calls = sorted({m for m in _CALL_RE.findall(text) if m in exported})
        if not calls:
            continue
        if not _IMPORT_HOOK_RE.search(text):
            errors.append(
                f"{_rel(path)} calls {', '.join(calls)} but does not import "
                f"useConsoleAction from @/lib/actions/useConsoleAction"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if not _REGISTRY.is_file():
        print(f"ERROR: missing registry {_REGISTRY}", file=sys.stderr)
        return 1
    errs = check()
    if errs:
        print("check_operator_console_actions: FAIL", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(
        f"check_operator_console_actions: OK "
        f"({len(exported_actions())} actions registered)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
