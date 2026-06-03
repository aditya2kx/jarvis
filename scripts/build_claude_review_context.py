#!/usr/bin/env python3
"""Build a bounded file bundle for the Claude PR review bot.

Materializes only:
  * files changed in the PR (base..head), and
  * paired test modules for changed ``.py`` files (heuristic, same directory),

into ``review-context/`` plus a ``MANIFEST.md`` the bot must follow. This gives
cross-file context (tests, full file bodies) without repo-wide grep/find.

Usage (CI, after checkout with fetch-depth: 0):
    python3 scripts/build_claude_review_context.py \\
        --base "$BASE_SHA" --head "$HEAD_SHA" --out-dir review-context
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Skip materializing huge files (token/cost guard).
MAX_FILE_BYTES = 200_000

# Always include the review rubric in the bundle.
RUBRIC_SOURCE = Path(".github/claude-review-guidelines.md")


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def _path_exists_at(ref: str, path: str) -> bool:
    try:
        subprocess.run(
            ["git", "cat-file", "-e", f"{ref}:{path}"],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def changed_paths(base: str, head: str) -> list[str]:
    out = _git("diff", "--name-only", f"{base}...{head}")
    return [p for p in out.splitlines() if p.strip()]


def _ref_resolves(ref: str) -> bool:
    """True if ``ref`` is a real, resolvable commit (not absent / all-zero)."""
    if not ref or set(ref) <= {"0"}:
        return False
    try:
        subprocess.run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
                       check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError:
        return False


def delta_paths_since(prev_head: str | None, head: str) -> list[str]:
    """Files changed since the previously-reviewed head (the latest push).

    Returns [] when ``prev_head`` is missing/unresolvable (e.g. first review),
    signalling "review the whole PR" to callers.
    """
    if not _ref_resolves(prev_head or ""):
        return []
    out = _git("diff", "--name-only", f"{prev_head}...{head}")
    return [p for p in out.splitlines() if p.strip()]


def paired_test_candidates(py_path: str) -> list[str]:
    """Heuristic test paths for a changed Python module."""
    p = Path(py_path)
    if p.suffix != ".py" or p.name.startswith("test_"):
        return []
    stem = p.stem
    parent = p.parent
    names = [
        parent / f"test_{stem}.py",
        parent / f"tests/test_{stem}.py",
    ]
    # agents/bhaga/scripts/foo.py -> test_foo.py alongside
    if parent.as_posix().endswith("/scripts"):
        names.append(parent / f"test_{stem}.py")
    return [n.as_posix() for n in names]


def expand_paths(paths: list[str], head: str) -> list[tuple[str, str]]:
    """Return (path, reason) pairs to materialize, deduped in stable order."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []

    def add(path: str, reason: str) -> None:
        if path in seen or not path:
            return
        if not _path_exists_at(head, path):
            return
        seen.add(path)
        out.append((path, reason))

    for path in paths:
        add(path, "changed in PR")
        for candidate in paired_test_candidates(path):
            add(candidate, f"paired test for `{path}`")

    rubric = RUBRIC_SOURCE.as_posix()
    if _path_exists_at(head, rubric) or RUBRIC_SOURCE.is_file():
        add(rubric, "review rubric (always)")

    return out


def materialize(path: str, head: str, out_dir: Path) -> tuple[bool, str | None]:
    """Write ``head:path`` (or working-tree copy) into out_dir."""
    dest = out_dir / path
    src = Path(path)
    if _path_exists_at(head, path):
        try:
            size = int(_git("cat-file", "-s", f"{head}:{path}"))
        except subprocess.CalledProcessError:
            return False, "missing at head"
        if size > MAX_FILE_BYTES:
            return False, f"skipped (>{MAX_FILE_BYTES} bytes)"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(subprocess.check_output(["git", "show", f"{head}:{path}"]))
        return True, None
    if src.is_file():
        if src.stat().st_size > MAX_FILE_BYTES:
            return False, f"skipped (>{MAX_FILE_BYTES} bytes)"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return True, None
    return False, "missing at head"


def write_manifest(
    out_dir: Path,
    entries: list[tuple[str, str, bool, str | None]],
    *,
    base: str,
    head: str,
    delta_paths: list[str] | None = None,
) -> None:
    lines = [
        "# Claude review context (bounded)",
        "",
        f"Base: `{base[:12]}…` → Head: `{head[:12]}…`",
        "",
        "**Scope rule for the reviewer:** read `gh pr view` / `gh pr diff` for the "
        "change summary, then read **only** files under `review-context/` listed below. "
        "Do **not** grep, find, or read any other path in the repo.",
    ]
    if delta_paths:
        lines += [
            "",
            "**This is a RE-REVIEW.** You have already reviewed earlier commits on "
            "this PR. Only the files below changed since your last review — focus "
            "your attention there and do **not** re-raise feedback from prior rounds:",
            "",
            *[f"- `{p}`" for p in delta_paths],
        ]
    lines += [
        "",
        "| File | Why included | Materialized |",
        "| --- | --- | --- |",
    ]
    delta_set = set(delta_paths or [])
    for path, reason, ok, skip in entries:
        status = "yes" if ok else f"no ({skip})"
        tag = " **(changed since last review)**" if path in delta_set else ""
        lines.append(f"| `{path}`{tag} | {reason} | {status} |")
    lines.append("")
    (out_dir / "MANIFEST.md").write_text("\n".join(lines), encoding="utf-8")


def build(*, base: str, head: str, out_dir: Path, prev_head: str | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Clean prior bundle so cancelled runs don't leave stale files.
    for child in out_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    diff_paths = changed_paths(base, head)
    delta = delta_paths_since(prev_head, head)
    planned = expand_paths(diff_paths, head)
    entries: list[tuple[str, str, bool, str | None]] = []
    materialized = 0
    for path, reason in planned:
        ok, skip = materialize(path, head, out_dir)
        entries.append((path, reason, ok, skip))
        if ok:
            materialized += 1

    write_manifest(out_dir, entries, base=base, head=head, delta_paths=delta)
    return {
        "changed": len(diff_paths),
        "delta": len(delta),
        "planned": len(planned),
        "materialized": materialized,
        "out_dir": str(out_dir),
    }


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--base", required=True, help="PR base commit SHA")
    cli.add_argument("--head", required=True, help="PR head commit SHA")
    cli.add_argument("--prev-head", default=None,
                     help="Previously-reviewed head SHA (github.event.before); enables "
                          "re-review focus on what changed since the last review")
    cli.add_argument("--out-dir", default="review-context")
    args = cli.parse_args(argv)

    summary = build(base=args.base, head=args.head, out_dir=Path(args.out_dir),
                    prev_head=args.prev_head)
    delta_note = f", {summary['delta']} since last review" if summary["delta"] else ""
    print(
        f"# review-context: {summary['materialized']}/{summary['planned']} files "
        f"({summary['changed']} changed in PR{delta_note})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
