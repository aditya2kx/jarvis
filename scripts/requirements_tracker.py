#!/usr/bin/env python3
"""CLI helper to update requirement status in Playground/REQUIREMENTS.md.

Used by:
  - start_pr_session.py  (→ 🔄 In Progress when a session starts)
  - pr-cost-finalize.yml (→ ✅ Done when a PR merges)
  - agents / humans directly for ad-hoc updates

Usage:
    python3 scripts/requirements_tracker.py mark-done   --req 15 --pr 22
    python3 scripts/requirements_tracker.py mark-progress --req 15 --pr 22
    python3 scripts/requirements_tracker.py add          --req "Short title" [--priority p0]
    python3 scripts/requirements_tracker.py list
    python3 scripts/requirements_tracker.py report
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

_REQUIREMENTS_MD = Path(__file__).parent.parent / "Playground" / "REQUIREMENTS.md"

_STATUS_PENDING     = "🔲 Pending"
_STATUS_IN_PROGRESS = "🔄 In Progress"
_STATUS_DONE        = "✅ Done"
_STATUS_P0          = "🔴 P0"

_ALL_STATUSES = [_STATUS_PENDING, _STATUS_IN_PROGRESS, _STATUS_DONE, _STATUS_P0]


def _row_pattern(req_id: str) -> re.Pattern:
    escaped = "|".join(re.escape(s) for s in _ALL_STATUSES)
    return re.compile(
        r"^(\| *(?:" + escaped + r") *\| *" + re.escape(str(req_id)) + r" *\|.*)",
        re.MULTILINE,
    )


def _update_status(req_id: str, new_status: str, pr: int | None = None) -> bool:
    if not _REQUIREMENTS_MD.exists():
        print(f"error: {_REQUIREMENTS_MD} not found", file=sys.stderr)
        return False
    text = _REQUIREMENTS_MD.read_text(encoding="utf-8")
    pattern = _row_pattern(req_id)
    match = pattern.search(text)
    if not match:
        return False
    old_row = match.group(1)
    escaped = "|".join(re.escape(s) for s in _ALL_STATUSES)
    new_row = re.sub(
        r"^(\| *)(?:" + escaped + r")( *\|)",
        rf"\g<1>{new_status}\g<2>",
        old_row,
    )
    if pr is not None:
        cols = new_row.split("|")
        if len(cols) > 4:
            pr_cell = cols[4].strip()
            pr_ref = f"#{pr}"
            if pr_ref not in pr_cell:
                cols[4] = f" {pr_cell + ', ' if pr_cell not in ('', '—') else ''}{pr_ref} "
                new_row = "|".join(cols)
    _REQUIREMENTS_MD.write_text(
        text[: match.start()] + new_row + text[match.end():],
        encoding="utf-8",
    )
    return True


def _next_id() -> int:
    if not _REQUIREMENTS_MD.exists():
        return 1
    text = _REQUIREMENTS_MD.read_text(encoding="utf-8")
    escaped = "|".join(re.escape(s) for s in _ALL_STATUSES)
    ids = re.findall(r"^\| *(?:" + escaped + r") *\| *(\d+) *\|", text, re.MULTILINE)
    return max((int(i) for i in ids), default=0) + 1


def cmd_mark_done(req_id: str, pr: int | None) -> int:
    ok = _update_status(req_id, _STATUS_DONE, pr=pr)
    if ok:
        print(f"✅ Requirement #{req_id} marked Done" + (f" (PR #{pr})" if pr else ""))
    else:
        print(f"⚠️  Requirement #{req_id} not found in {_REQUIREMENTS_MD}")
        return 1
    return 0


def cmd_mark_progress(req_id: str, pr: int | None) -> int:
    ok = _update_status(req_id, _STATUS_IN_PROGRESS, pr=pr)
    if ok:
        print(f"🔄 Requirement #{req_id} marked In Progress" + (f" (PR #{pr})" if pr else ""))
    else:
        print(f"⚠️  Requirement #{req_id} not found in {_REQUIREMENTS_MD}")
        return 1
    return 0


def cmd_add(title: str, priority: str | None) -> int:
    nid = _next_id()
    status = _STATUS_P0 if priority == "p0" else _STATUS_PENDING
    row = f"| {status} | {nid} | {title} | — | |\n"
    text = _REQUIREMENTS_MD.read_text(encoding="utf-8") if _REQUIREMENTS_MD.exists() else ""
    # Insert before the Archive section (or at end)
    archive_idx = text.find("\n---\n")
    if archive_idx != -1:
        text = text[: archive_idx] + "\n" + row + text[archive_idx:]
    else:
        text += row
    _REQUIREMENTS_MD.write_text(text, encoding="utf-8")
    print(f"Added requirement #{nid}: {title}")
    return 0


def cmd_list() -> int:
    if not _REQUIREMENTS_MD.exists():
        print("REQUIREMENTS.md not found")
        return 1
    text = _REQUIREMENTS_MD.read_text(encoding="utf-8")
    escaped = "|".join(re.escape(s) for s in _ALL_STATUSES)
    rows = re.findall(
        r"^\| *((?:" + escaped + r")) *\| *(\d+) *\| *([^|]+)\|",
        text, re.MULTILINE,
    )
    for status, rid, title in rows:
        print(f"  [{status.strip()}] #{rid}: {title.strip()}")
    return 0


_STATUS_COLOURS = {
    _STATUS_DONE:        ("#1a4a2e", "#2ea043", "#2ea043"),   # bg, border, badge
    _STATUS_IN_PROGRESS: ("#3d2e0a", "#bb8009", "#d29922"),
    _STATUS_P0:          ("#4a1a1a", "#da3633", "#f85149"),
    _STATUS_PENDING:     ("#161b22", "#30363d", "#768390"),
}

_STATUS_LABELS = {
    _STATUS_DONE:        "Done",
    _STATUS_IN_PROGRESS: "In Progress",
    _STATUS_P0:          "P0",
    _STATUS_PENDING:     "Pending",
}


def _parse_rows() -> list[dict]:
    """Return list of dicts with keys: status, id, title, added, pr."""
    if not _REQUIREMENTS_MD.exists():
        return []
    text = _REQUIREMENTS_MD.read_text(encoding="utf-8")
    escaped = "|".join(re.escape(s) for s in _ALL_STATUSES)
    rows = []
    for m in re.finditer(
        r"^\| *((?:" + escaped + r")) *\| *(\d+) *\| *([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|",
        text,
        re.MULTILINE,
    ):
        status, rid, title, added, pr = (g.strip() for g in m.groups())
        rows.append({"status": status, "id": int(rid), "title": title, "added": added, "pr": pr})
    return rows


def _html_badge(status: str) -> str:
    _, _, badge_color = _STATUS_COLOURS.get(status, ("#161b22", "#30363d", "#768390"))
    label = _STATUS_LABELS.get(status, status)
    return (
        f'<span class="badge" style="background:{badge_color}20;color:{badge_color};'
        f'border:1px solid {badge_color}40">{label}</span>'
    )


def _build_html_report() -> str:
    rows = _parse_rows()
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total = len(rows)
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in _ALL_STATUSES}

    status_order = [_STATUS_P0, _STATUS_IN_PROGRESS, _STATUS_PENDING, _STATUS_DONE]
    rows_sorted = sorted(rows, key=lambda r: (status_order.index(r["status"]) if r["status"] in status_order else 99, r["id"]))

    filter_buttons = ""
    for s in status_order:
        n = counts[s]
        _, border, badge_c = _STATUS_COLOURS[s]
        slug = _STATUS_LABELS[s].lower().replace(" ", "-")
        filter_buttons += (
            f'<button class="filter-btn" data-status="{slug}" '
            f'style="--acc:{border}" onclick="toggleFilter(this)">'
            f'{_STATUS_LABELS[s]} <span class="cnt">{n}</span></button>\n'
        )

    table_rows = ""
    for r in rows_sorted:
        s = r["status"]
        bg, border, _ = _STATUS_COLOURS.get(s, ("#161b22", "#30363d", "#768390"))
        slug = _STATUS_LABELS.get(s, "pending").lower().replace(" ", "-")
        pr_cell = ""
        if r["pr"] and r["pr"] != "—":
            prs = [p.strip() for p in r["pr"].split(",") if p.strip()]
            pr_cell = " ".join(
                f'<a href="../../.." class="pr-link">#{p.lstrip("#")}</a>' for p in prs
            )
        table_rows += (
            f'<tr class="req-row" data-status="{slug}" '
            f'style="--row-bg:{bg};--row-border:{border}">'
            f'<td class="id-cell">#{r["id"]}</td>'
            f'<td>{_html_badge(s)}</td>'
            f'<td class="title-cell">{r["title"]}</td>'
            f'<td class="muted">{r["added"] or "—"}</td>'
            f'<td class="pr-cell">{pr_cell or "—"}</td>'
            f'</tr>\n'
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Requirements tracker — Jarvis</title>
<style>
:root {{ color-scheme: light dark; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: #0d1117; color: #e6edf3; }}
.wrap {{ max-width: 960px; margin: 0 auto; padding: 32px 24px 64px; }}
h1 {{ font-size: 24px; margin: 0 0 4px; }}
.muted {{ color: #768390; font-size: 12px; }}
a {{ color: #539bf5; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

/* stat pills */
.stats {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 20px 0 16px; }}
.stat {{ border: 1px solid #21262d; border-radius: 8px; padding: 10px 16px; min-width: 90px; }}
.stat .v {{ font-size: 22px; font-weight: 600; }}
.stat .l {{ color: #768390; font-size: 11px; margin-top: 1px; }}

/* filter bar */
.filters {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 0 0 20px; }}
.filter-btn {{ border: 1px solid #30363d; border-radius: 20px; padding: 5px 14px;
  background: #161b22; color: #e6edf3; font-size: 13px; cursor: pointer;
  transition: background 0.15s, border-color 0.15s; }}
.filter-btn:hover {{ border-color: var(--acc); }}
.filter-btn.active {{ background: color-mix(in srgb, var(--acc) 20%, transparent);
  border-color: var(--acc); color: #fff; }}
.filter-btn .cnt {{ background: #21262d; border-radius: 10px; padding: 0 6px;
  font-size: 11px; margin-left: 4px; }}

/* table */
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ text-align: left; padding: 8px 10px; border-bottom: 2px solid #21262d;
  color: #768390; font-weight: 600; white-space: nowrap; }}
td {{ padding: 9px 10px; border-bottom: 1px solid #21262d; vertical-align: top; }}
.req-row {{ background: var(--row-bg); }}
.req-row td:first-child {{ border-left: 3px solid var(--row-border); }}
.req-row.hidden {{ display: none; }}
.id-cell {{ color: #768390; font-variant-numeric: tabular-nums; white-space: nowrap; width: 40px; }}
.title-cell {{ min-width: 280px; }}
.pr-cell {{ white-space: nowrap; }}
.pr-link {{ background: #21262d; border-radius: 4px; padding: 1px 6px;
  font-size: 12px; color: #adbac7; border: 1px solid #30363d; }}
.pr-link:hover {{ border-color: #539bf5; color: #539bf5; text-decoration: none; }}

/* badge */
.badge {{ border-radius: 12px; padding: 2px 8px; font-size: 11px;
  font-weight: 600; white-space: nowrap; }}

/* no-results */
#no-results {{ display: none; color: #768390; padding: 24px 0; text-align: center; }}
</style></head>
<body><div class="wrap">
<h1>Requirements tracker</h1>
<p class="muted">Source: <code>Playground/REQUIREMENTS.md</code> · {total} requirement(s) · generated {now}</p>

<div class="stats">
  <div class="stat"><div class="v">{total}</div><div class="l">Total</div></div>
  <div class="stat"><div class="v" style="color:#f85149">{counts[_STATUS_P0]}</div><div class="l">P0</div></div>
  <div class="stat"><div class="v" style="color:#d29922">{counts[_STATUS_IN_PROGRESS]}</div><div class="l">In Progress</div></div>
  <div class="stat"><div class="v" style="color:#768390">{counts[_STATUS_PENDING]}</div><div class="l">Pending</div></div>
  <div class="stat"><div class="v" style="color:#2ea043">{counts[_STATUS_DONE]}</div><div class="l">Done</div></div>
</div>

<div class="filters">
  <button class="filter-btn active" data-status="all" style="--acc:#539bf5"
    onclick="toggleFilter(this)">All <span class="cnt">{total}</span></button>
{filter_buttons}</div>

<table>
<thead><tr>
  <th>#</th><th>Status</th><th>Title</th><th>Added</th><th>PR</th>
</tr></thead>
<tbody id="req-tbody">
{table_rows}</tbody>
</table>
<div id="no-results">No requirements match the current filter.</div>

<script>
var activeFilters = new Set(['all']);
function toggleFilter(btn) {{
  var status = btn.dataset.status;
  if (status === 'all') {{
    activeFilters = new Set(['all']);
  }} else {{
    activeFilters.delete('all');
    if (activeFilters.has(status)) {{
      activeFilters.delete(status);
      if (activeFilters.size === 0) activeFilters.add('all');
    }} else {{
      activeFilters.add(status);
    }}
  }}
  document.querySelectorAll('.filter-btn').forEach(function(b) {{
    b.classList.toggle('active',
      activeFilters.has(b.dataset.status) ||
      (activeFilters.has('all') && b.dataset.status === 'all'));
  }});
  var rows = document.querySelectorAll('.req-row');
  var visible = 0;
  rows.forEach(function(r) {{
    var show = activeFilters.has('all') || activeFilters.has(r.dataset.status);
    r.classList.toggle('hidden', !show);
    if (show) visible++;
  }});
  document.getElementById('no-results').style.display = visible === 0 ? 'block' : 'none';
}}
</script>
</div></body></html>
"""


def cmd_report() -> int:
    out = _REQUIREMENTS_MD.parent / "requirements_report.html"
    html = _build_html_report()
    out.write_text(html, encoding="utf-8")
    print(f"Report written → {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = cli.add_subparsers(dest="cmd", required=True)

    p_done = sub.add_parser("mark-done", help="Mark a requirement ✅ Done")
    p_done.add_argument("--req", required=True, help="Requirement ID")
    p_done.add_argument("--pr", type=int, help="PR number to record")

    p_prog = sub.add_parser("mark-progress", help="Mark a requirement 🔄 In Progress")
    p_prog.add_argument("--req", required=True, help="Requirement ID")
    p_prog.add_argument("--pr", type=int, help="PR number to record")

    p_add = sub.add_parser("add", help="Add a new requirement")
    p_add.add_argument("--req", required=True, dest="title", help="Short title")
    p_add.add_argument("--priority", choices=["p0"], help="Set P0 status")

    sub.add_parser("list", help="List all requirements with status")
    sub.add_parser("report", help="Generate Playground/requirements_report.html")

    args = cli.parse_args(argv)
    if args.cmd == "mark-done":
        return cmd_mark_done(args.req, args.pr)
    if args.cmd == "mark-progress":
        return cmd_mark_progress(args.req, args.pr)
    if args.cmd == "add":
        return cmd_add(args.title, getattr(args, "priority", None))
    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "report":
        return cmd_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
