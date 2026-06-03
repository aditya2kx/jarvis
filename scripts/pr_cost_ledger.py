#!/usr/bin/env python3
"""Per-PR token / model / cost ledger — the BHAGA cost-monitoring framework.

Tracks the full cost of landing a change, from the moment a requirement is
picked up until the PR merges, across BOTH cost surfaces:

  * BUILD  — the Cursor agent sessions that wrote the code (model =
             claude-opus-*, etc.). Auto path: `capture-build` pulls exact
             per-request token+cost from the Cursor usage API via
             `cursor_usage.py` (local session token). Manual fallback:
             `record-build` from the dashboard UI — marked approximate.
             PR attribution: auto-window from `ai-code-tracking.db` (edit-
             anchored, capped at merge) or explicit `--start/--end`.
  * REVIEW — the Claude PR-review GitHub Action (model = claude-sonnet-*).
             Exact: sourced from each run's `execution_file` by
             `post_claude_review_cost.py`, which also calls
             `record_review_run()` here so the ledger fills automatically.

Data source (committed in-repo, the "some data source" the spec asks for):
    metrics/pr_cost/PR-<n>.json    — one record per PR.

CLI:
    # pre-merge gate (CI): fail if the PR has no cost record / no review data
    python3 scripts/pr_cost_ledger.py validate --pr 12

    # post-merge: top cost areas + efficiency recommendations
    python3 scripts/pr_cost_ledger.py analyze --pr 12
    python3 scripts/pr_cost_ledger.py analyze            # across all PRs

    # manual entry (build rows from the Cursor dashboard)
    python3 scripts/pr_cost_ledger.py record-build --pr 12 \\
        --ts 2026-06-02T12:56-05:00 --tokens 44000000 --cost 30.76 \\
        --model claude-opus-4-8-thinking-high

    python3 scripts/pr_cost_ledger.py set-meta --pr 12 --title "…" \\
        --requirement "…" --branch "…" --created 2026-06-02T18:27:22Z
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
from pathlib import Path
from typing import Any

LEDGER_DIR = Path(__file__).resolve().parent.parent / "metrics" / "pr_cost"

# A build session is considered the "dominant" cost hotspot when it alone is at
# least this fraction of total build cost — the single thing most worth fixing.
_DOMINANT_SESSION_FRACTION = 0.40
# Review is "expensive relative to its job" if it crosses this many dollars or
# re-ran this many times (each push re-runs the full bounded review).
_REVIEW_RUN_WARN = 3


# ── record I/O ──────────────────────────────────────────────────────

def _record_path(pr: int) -> Path:
    return LEDGER_DIR / f"PR-{pr}.json"


def _empty_record(pr: int) -> dict[str, Any]:
    return {
        "pr_number": pr,
        "title": None,
        "requirement": None,
        "branch": None,
        "created_at": None,
        "merged_at": None,
        "diff": {"files": None, "additions": None, "deletions": None},
        "build": {
            "source": None,
            "approximate": True,
            "sessions": [],
            "tokens_total": 0,
            "cost_usd_total": 0.0,
        },
        "review": {
            "source": "claude-code-action execution_file (exact)",
            "runs": [],
            "tokens_total": 0,
            "cost_usd_total": 0.0,
            "run_count": 0,
        },
        "totals": {"tokens": 0, "cost_usd": 0.0},
    }


def load_record(pr: int) -> dict[str, Any]:
    path = _record_path(pr)
    if path.is_file():
        return json.loads(path.read_text())
    return _empty_record(pr)


def save_record(rec: dict[str, Any]) -> Path:
    _recompute_totals(rec)
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    path = _record_path(rec["pr_number"])
    path.write_text(json.dumps(rec, indent=2, sort_keys=False) + "\n")
    return path


def _recompute_totals(rec: dict[str, Any]) -> None:
    b = rec["build"]
    b["tokens_total"] = sum(int(s.get("tokens") or 0) for s in b["sessions"])
    b["cost_usd_total"] = round(sum(float(s.get("cost_usd") or 0) for s in b["sessions"]), 4)
    r = rec["review"]
    r["run_count"] = len(r["runs"])
    r["tokens_total"] = sum(int(x.get("tokens") or 0) for x in r["runs"])
    r["cost_usd_total"] = round(sum(float(x.get("cost_usd") or 0) for x in r["runs"]), 4)
    rec["totals"] = {
        "tokens": b["tokens_total"] + r["tokens_total"],
        "cost_usd": round(b["cost_usd_total"] + r["cost_usd_total"], 4),
    }


# ── mutation helpers (used by CLI + post_claude_review_cost) ─────────

def set_meta(pr: int, **fields: Any) -> dict[str, Any]:
    rec = load_record(pr)
    for k in ("title", "requirement", "branch", "created_at", "merged_at"):
        if fields.get(k) is not None:
            rec[k] = fields[k]
    for dk in ("files", "additions", "deletions"):
        if fields.get(dk) is not None:
            rec["diff"][dk] = int(fields[dk])
    save_record(rec)
    return rec


def capture_build(
    pr: int, *, start: str | None = None, end: str | None = None,
    model_filter: str | None = None,
) -> dict[str, Any]:
    """Auto-fill build sessions from the Cursor usage API for a time window.

    Pulls exact per-request token+cost via scripts/cursor_usage.py (local session
    token) and records one build session per request. Cost is exact. If start/end
    are omitted, the window is derived from the PR's branch via Cursor's local
    ai-code-tracking.db (anchored to AI code edits, capped at merge) — so the
    request->PR mapping is automatic and edit-accurate.
    """
    import cursor_usage  # noqa: PLC0415

    rec0 = load_record(pr)
    if not (start and end):
        if start or end:
            raise SystemExit(
                "capture-build: --start and --end must be provided together, "
                "or both omitted for auto-derive."
            )
        branch = rec0.get("branch")
        if not branch:
            raise SystemExit(
                f"PR #{pr} has no branch recorded — run `set-meta --pr {pr} --branch <name>` "
                f"or pass --start/--end explicitly."
            )
        start_ms, end_ms = cursor_usage.derive_window_for_branch(
            branch, merged_at=rec0.get("merged_at")
        )
        start, end = str(start_ms), str(end_ms)
    events = cursor_usage.fetch_usage_events(start, end)
    if model_filter:
        events = [e for e in events if model_filter.lower() in (e.get("model") or "").lower()]
    rec = load_record(pr)
    rec["build"]["source"] = (
        "cursor dashboard usage API (local session token; exact cost, "
        "request->PR by time window)"
    )
    rec["build"]["approximate"] = False
    rec["build"]["window"] = {
        "start": datetime.datetime.fromtimestamp(cursor_usage.to_ms(start) / 1000, datetime.timezone.utc).isoformat(),
        "end": datetime.datetime.fromtimestamp(cursor_usage.to_ms(end) / 1000, datetime.timezone.utc).isoformat(),
    }
    # Full-window pull is authoritative: replace prior build rows so re-running is idempotent.
    rec["build"]["sessions"] = []
    for e in events:
        rec["build"]["sessions"].append({
            "ts": e["ts_iso"], "model": e["model"], "tokens": e["tokens"],
            "cost_usd": e["cost_usd"],
            "input_tokens": e["input_tokens"], "output_tokens": e["output_tokens"],
            "cache_read_input_tokens": e["cache_read"],
            "cache_creation_input_tokens": e["cache_write"],
            "note": "headless" if e["is_headless"] else None,
        })
    rec["build"]["sessions"].sort(key=lambda s: s.get("ts") or "")
    save_record(rec)
    return rec


def record_build_session(
    pr: int, *, ts: str, tokens: int, cost_usd: float,
    model: str, source: str | None = None, note: str | None = None,
) -> dict[str, Any]:
    rec = load_record(pr)
    if source:
        rec["build"]["source"] = source
    # De-dupe by timestamp so re-imports of the dashboard are idempotent.
    rec["build"]["sessions"] = [s for s in rec["build"]["sessions"] if s.get("ts") != ts]
    rec["build"]["sessions"].append({
        "ts": ts, "model": model, "tokens": int(tokens),
        "cost_usd": round(float(cost_usd), 4), "note": note,
    })
    rec["build"]["sessions"].sort(key=lambda s: s.get("ts") or "")
    save_record(rec)
    return rec


def record_review_run(
    pr: int, *, ts: str | None, model: str, turns: int | None,
    input_tokens: int, output_tokens: int, cache_read: int, cache_write: int,
    cost_usd: float | None, result: str | None, run_url: str | None = None,
) -> dict[str, Any]:
    """Append one Claude-review run. Called by post_claude_review_cost.py."""
    rec = load_record(pr)
    tokens = int(input_tokens) + int(output_tokens) + int(cache_read) + int(cache_write)
    entry = {
        "ts": ts, "model": model, "turns": turns,
        "input_tokens": int(input_tokens), "output_tokens": int(output_tokens),
        "cache_read_input_tokens": int(cache_read),
        "cache_creation_input_tokens": int(cache_write),
        "tokens": tokens,
        "cost_usd": round(float(cost_usd), 4) if cost_usd is not None else None,
        "result": result, "run_url": run_url,
    }
    # De-dupe by run_url (one cost row per workflow run), or by ts when run_url absent.
    if run_url:
        rec["review"]["runs"] = [x for x in rec["review"]["runs"] if x.get("run_url") != run_url]
    elif ts:
        rec["review"]["runs"] = [x for x in rec["review"]["runs"] if x.get("ts") != ts]
    rec["review"]["runs"].append(entry)
    rec["review"]["runs"].sort(key=lambda x: x.get("ts") or "")
    save_record(rec)
    return rec


# ── pre-merge gate ──────────────────────────────────────────────────

def validate(pr: int, *, require_build: bool = False) -> tuple[bool, list[str]]:
    """Pre-merge gate: a PR may not merge without an accounted cost record.

    Requires: the committed record exists, has a requirement/title, and has at
    least one cost surface recorded. With ``require_build`` (the hard CI gate),
    build sessions MUST be present — the author has to record the Cursor build
    cost (manually from the dashboard, or via the capture tooling) and commit
    ``metrics/pr_cost/PR-<n>.json`` before the PR can merge.
    """
    path = _record_path(pr)
    if not path.is_file():
        return False, [f"no cost record at metrics/pr_cost/PR-{pr}.json — "
                       f"run `pr_cost_ledger.py set-meta --pr {pr} …` + record build cost, "
                       f"then commit the file"]
    rec = json.loads(path.read_text())
    problems: list[str] = []
    if not rec.get("requirement") and not rec.get("title"):
        problems.append("record has no requirement/title")
    has_review = bool(rec["review"]["runs"])
    has_build = bool(rec["build"]["sessions"])
    if not has_review and not has_build:
        problems.append("no build sessions and no review runs recorded — cost is unaccounted")
    if require_build and not has_build:
        problems.append(
            "no build sessions recorded — the hard gate requires the Cursor build cost. "
            f"Add it: `pr_cost_ledger.py record-build --pr {pr} --ts <iso> --tokens <n> "
            f"--cost <usd> --model <m>` (rows from the Cursor usage dashboard), then commit."
        )
    return (not problems), problems


# ── post-merge analysis ─────────────────────────────────────────────

def _recommendations(rec: dict[str, Any]) -> list[str]:
    recs: list[str] = []
    b, r = rec["build"], rec["review"]
    total = rec["totals"]["cost_usd"] or 0.0
    build_share = (b["cost_usd_total"] / total * 100) if total else 0

    if build_share >= 70:
        recs.append(
            f"Build is {build_share:.0f}% of total cost — optimization leverage is in the "
            f"agent build loop, not the review bot. Review-side tuning is rounding error."
        )
    # Dominant build session
    if b["sessions"]:
        top = max(b["sessions"], key=lambda s: s.get("cost_usd") or 0)
        frac = (top["cost_usd"] / b["cost_usd_total"]) if b["cost_usd_total"] else 0
        if frac >= _DOMINANT_SESSION_FRACTION:
            recs.append(
                f"One build session ({top['ts']}, {top['tokens']:,} tokens, "
                f"${top['cost_usd']:.2f}) is {frac*100:.0f}% of build cost. Break long "
                f"marathon sessions into checkpointed sub-tasks; avoid re-reading large "
                f"files/transcripts/.venv into context; prefer Plan mode + targeted reads."
            )
        big_model = any("opus" in (s.get("model") or "").lower() for s in b["sessions"])
        if big_model:
            recs.append(
                "Build ran on an Opus-class model. Route mechanical work (renames, test "
                "scaffolding, doc edits, log spelunking) to a Sonnet/Haiku-class model and "
                "reserve Opus for genuinely hard reasoning — biggest $/token lever."
            )
    # Review cycles
    if r["run_count"] >= _REVIEW_RUN_WARN:
        recs.append(
            f"Review ran {r['run_count']}× (each push re-runs the full bounded review at "
            f"~${(r['cost_usd_total']/max(r['run_count'],1)):.2f}/run). Batch pushes, or keep "
            f"the PR in Draft until ready, so review fires fewer times."
        )
    maxturn = [x for x in r["runs"] if (x.get("result") or "") == "error_max_turns"]
    if maxturn:
        recs.append(
            f"{len(maxturn)} review run(s) hit the turn cap (error_max_turns) — they spent "
            f"tokens without finishing. Tighten the review context/rubric so it converges "
            f"inside the cap, or raise the cap only if reviews are genuinely incomplete."
        )
    if not recs:
        recs.append("No obvious inefficiency flags — costs look proportional to the change.")
    return recs


def _areas(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """Rank cost contributors: each build session + review as one aggregate."""
    areas: list[dict[str, Any]] = []
    for s in rec["build"]["sessions"]:
        areas.append({
            "area": f"build session {s.get('ts')}",
            "model": s.get("model"),
            "tokens": int(s.get("tokens") or 0),
            "cost_usd": float(s.get("cost_usd") or 0),
        })
    r = rec["review"]
    if r["runs"]:
        areas.append({
            "area": f"claude review (×{r['run_count']} runs)",
            "model": r["runs"][0].get("model"),
            "tokens": r["tokens_total"],
            "cost_usd": r["cost_usd_total"],
        })
    areas.sort(key=lambda a: a["cost_usd"], reverse=True)
    return areas


def analyze(prs: list[int], top_n: int = 5) -> dict[str, Any]:
    records = [load_record(p) for p in prs]
    out_lines: list[str] = []
    reports = []
    for rec in records:
        _recompute_totals(rec)
        areas = _areas(rec)
        report = {
            "pr_number": rec["pr_number"],
            "title": rec.get("title"),
            "totals": rec["totals"],
            "build_cost_usd": rec["build"]["cost_usd_total"],
            "review_cost_usd": rec["review"]["cost_usd_total"],
            "top_areas": areas[:top_n],
            "recommendations": _recommendations(rec),
        }
        reports.append(report)

        t = rec["totals"]
        out_lines.append(f"\n=== PR #{rec['pr_number']}: {rec.get('title') or '(untitled)'} ===")
        out_lines.append(
            f"  total: ${t['cost_usd']:.2f}  ({t['tokens']:,} tokens)   "
            f"build ${rec['build']['cost_usd_total']:.2f} | review ${rec['review']['cost_usd_total']:.2f}"
        )
        out_lines.append(f"  top {min(top_n, len(areas))} cost areas:")
        for i, a in enumerate(areas[:top_n], 1):
            share = (a["cost_usd"] / t["cost_usd"] * 100) if t["cost_usd"] else 0
            out_lines.append(
                f"    {i}. ${a['cost_usd']:>7.2f} ({share:4.1f}%)  {a['tokens']:>12,} tok  "
                f"{a['area']}  [{a['model']}]"
            )
        out_lines.append("  recommendations:")
        for rc in report["recommendations"]:
            out_lines.append(f"    - {rc}")
    return {"reports": reports, "text": "\n".join(out_lines)}


# ── CLI ─────────────────────────────────────────────────────────────

def _all_prs() -> list[int]:
    prs = []
    for p in glob.glob(str(LEDGER_DIR / "PR-*.json")):
        try:
            prs.append(int(Path(p).stem.split("-", 1)[1]))
        except (ValueError, IndexError):
            continue
    return sorted(prs)


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = cli.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("set-meta", help="Set PR metadata")
    m.add_argument("--pr", type=int, required=True)
    m.add_argument("--title"); m.add_argument("--requirement"); m.add_argument("--branch")
    m.add_argument("--created"); m.add_argument("--merged")
    m.add_argument("--files", type=int); m.add_argument("--additions", type=int); m.add_argument("--deletions", type=int)

    b = sub.add_parser("record-build", help="Add a Cursor build session row (manual)")
    b.add_argument("--pr", type=int, required=True)
    b.add_argument("--ts", required=True); b.add_argument("--tokens", type=int, required=True)
    b.add_argument("--cost", type=float, required=True); b.add_argument("--model", required=True)
    b.add_argument("--source", default="cursor_dashboard"); b.add_argument("--note")

    c = sub.add_parser("capture-build", help="Auto-fill build sessions from the Cursor usage API")
    c.add_argument("--pr", type=int, required=True)
    c.add_argument("--start", help="window start (ISO8601 or epoch-ms); omit to auto-derive from branch")
    c.add_argument("--end", help="window end (ISO8601 or epoch-ms); omit to auto-derive from branch")
    c.add_argument("--model-filter", help="only include events whose model contains this substring")

    v = sub.add_parser("validate", help="Pre-merge gate: ensure costs are accounted")
    v.add_argument("--pr", type=int, required=True)
    v.add_argument("--require-build", action="store_true",
                   help="Hard gate: also require build sessions to be recorded.")

    a = sub.add_parser("analyze", help="Post-merge: top cost areas + recommendations")
    a.add_argument("--pr", type=int)
    a.add_argument("--top", type=int, default=5)
    a.add_argument("--json", action="store_true")

    args = cli.parse_args(argv)

    if args.cmd == "set-meta":
        rec = set_meta(args.pr, title=args.title, requirement=args.requirement,
                       branch=args.branch, created_at=args.created, merged_at=args.merged,
                       files=args.files, additions=args.additions, deletions=args.deletions)
        print(f"updated {_record_path(args.pr)}")
        return 0
    if args.cmd == "record-build":
        record_build_session(args.pr, ts=args.ts, tokens=args.tokens, cost_usd=args.cost,
                             model=args.model, source=args.source, note=args.note)
        print(f"recorded build session {args.ts} on {_record_path(args.pr)}")
        return 0
    if args.cmd == "capture-build":
        rec = capture_build(args.pr, start=args.start, end=args.end, model_filter=args.model_filter)
        n = len(rec["build"]["sessions"])
        w = rec["build"].get("window") or {}
        mode = "manual window" if (args.start or args.end) else "auto window (branch→ai-code-tracking.db)"
        print(f"captured {n} build session(s) from Cursor usage API [{mode}]")
        print(f"  window: {w.get('start')} → {w.get('end')}")
        print(f"  build total ${rec['build']['cost_usd_total']:.2f} → {_record_path(args.pr)}")
        return 0
    if args.cmd == "validate":
        ok, problems = validate(args.pr, require_build=args.require_build)
        if ok:
            print(f"[OK] PR #{args.pr} cost record is present and accounted.")
            return 0
        print(f"[FAIL] PR #{args.pr} cost record incomplete:")
        for p in problems:
            print(f"  - {p}")
        return 1
    if args.cmd == "analyze":
        prs = [args.pr] if args.pr else _all_prs()
        if not prs:
            print("no PR cost records found in metrics/pr_cost/")
            return 0
        result = analyze(prs, top_n=args.top)
        if args.json:
            print(json.dumps(result["reports"], indent=2))
        else:
            print(result["text"])
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
