#!/usr/bin/env python3
"""Incremental validation: compare shadow folder files against benchmark after each upload.

Usage:
    python validate_upload.py                    # Full diff summary
    python validate_upload.py --folder "02 - Brokerage 1099s/Schwab"  # Specific subfolder
    python validate_upload.py --slack             # Send results to Slack
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))
from core.config_loader import refresh_access_token, project_dir
from skills.google_drive.list_folder import inventory_folder, save_inventory

BENCHMARK_PATH = os.path.join(project_dir(), "extracted", "drive-2025-inventory.json")
SHADOW_PATH = os.path.join(project_dir(), "extracted", "drive-2025-test-inventory.json")
FOLDER_IDS_PATH = os.path.join(project_dir(), "extracted", "drive-2025-test-folder-ids.json")


def load_json(path):
    with open(path) as f:
        return json.load(f)


def refresh_shadow_inventory():
    folder_ids = load_json(FOLDER_IDS_PATH)
    root_id = folder_ids["root_id"]
    token = refresh_access_token()
    items = inventory_folder(token, root_id)
    save_inventory(SHADOW_PATH, root_id, items)
    return items


def files_by_folder(items):
    """Group non-folder items by their parent folder path."""
    result = {}
    for item in items:
        if item["isFolder"]:
            continue
        parts = item["path"].rsplit("/", 1)
        folder = parts[0] if len(parts) > 1 else "(root)"
        result.setdefault(folder, []).append(item["name"])
    return result


def validate(folder_filter=None):
    """Run validation and return structured report."""
    benchmark = load_json(BENCHMARK_PATH)
    shadow_items = refresh_shadow_inventory()
    shadow = {"items": shadow_items}

    bench_files = files_by_folder(benchmark["items"])
    shadow_files = files_by_folder(shadow["items"])

    all_folders = sorted(set(list(bench_files.keys()) + list(shadow_files.keys())))

    if folder_filter:
        all_folders = [f for f in all_folders if folder_filter in f]

    report_lines = []
    total_matched = 0
    total_benchmark = 0
    total_shadow = 0
    total_missing = 0
    total_extra = 0

    for folder in all_folders:
        bench = set(bench_files.get(folder, []))
        shad = set(shadow_files.get(folder, []))
        matched = bench & shad
        missing = bench - shad
        extra = shad - bench

        total_matched += len(matched)
        total_benchmark += len(bench)
        total_shadow += len(shad)
        total_missing += len(missing)
        total_extra += len(extra)

        if not bench and not shad:
            continue

        status = "COMPLETE" if not missing else f"{len(matched)}/{len(bench)}"
        report_lines.append(f"\n{folder}  [{status}]")

        for f in sorted(matched):
            report_lines.append(f"  ✓ {f}")
        for f in sorted(missing):
            report_lines.append(f"  ✗ MISSING: {f}")
        for f in sorted(extra):
            report_lines.append(f"  + EXTRA: {f}")

    summary = (
        f"FILE MATCH: {total_matched}/{total_benchmark} "
        f"({total_matched*100//max(total_benchmark,1)}%) | "
        f"{total_missing} missing | {total_extra} extra"
    )

    return {
        "summary": summary,
        "matched": total_matched,
        "benchmark_total": total_benchmark,
        "missing": total_missing,
        "extra": total_extra,
        "details": "\n".join(report_lines),
    }


def main():
    parser = argparse.ArgumentParser(description="Incremental validation after uploads.")
    parser.add_argument("--folder", default=None, help="Filter to a specific subfolder path")
    parser.add_argument("--slack", action="store_true", help="Send results to Slack")
    args = parser.parse_args()

    report = validate(args.folder)

    print(f"\n=== {report['summary']} ===")
    print(report["details"])

    if args.slack:
        from skills.slack.adapter import send_progress
        msg = f"Validation: {report['summary']}"
        if report["details"]:
            details = report["details"]
            if len(details) > 2000:
                details = details[:2000] + "\n... (truncated)"
            msg += f"\n```{details}```"
        send_progress(msg)


if __name__ == "__main__":
    main()
