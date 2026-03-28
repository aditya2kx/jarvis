#!/usr/bin/env python3
"""Compare a benchmark Drive inventory against an autonomous shadow inventory.

The benchmark inventory represents the desired outcome (for example, the real
Drive `Taxes/2025` folder). The shadow inventory represents what CHITRA/Jarvis
was able to reproduce autonomously (for example, `Taxes/2025-test`).

This script compares folder/file paths only. It does not copy artifacts from
the benchmark. It produces a structured diff report that can be used to drive
the next backlog items in PROGRESS.md.
"""

import argparse
import json
import os


def load_inventory(path):
    with open(path) as f:
        return json.load(f)


def split_items(items):
    folders = {}
    files = {}
    for item in items:
        key = item["path"]
        if item["isFolder"]:
            folders[key] = item
        else:
            files[key] = item
    return folders, files


def top_level_bucket(path):
    return path.split("/", 1)[0] if path else ""


def bucket_counts(paths):
    counts = {}
    for path in paths:
        bucket = top_level_bucket(path)
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def compare_inventories(benchmark_items, shadow_items):
    benchmark_folders, benchmark_files = split_items(benchmark_items)
    shadow_folders, shadow_files = split_items(shadow_items)

    missing_folders = sorted(set(benchmark_folders) - set(shadow_folders))
    extra_folders = sorted(set(shadow_folders) - set(benchmark_folders))
    missing_files = sorted(set(benchmark_files) - set(shadow_files))
    extra_files = sorted(set(shadow_files) - set(benchmark_files))

    return {
        "summary": {
            "benchmarkFolders": len(benchmark_folders),
            "shadowFolders": len(shadow_folders),
            "benchmarkFiles": len(benchmark_files),
            "shadowFiles": len(shadow_files),
            "missingFolders": len(missing_folders),
            "extraFolders": len(extra_folders),
            "missingFiles": len(missing_files),
            "extraFiles": len(extra_files),
        },
        "byTopLevelBucket": {
            "missingFolders": bucket_counts(missing_folders),
            "extraFolders": bucket_counts(extra_folders),
            "missingFiles": bucket_counts(missing_files),
            "extraFiles": bucket_counts(extra_files),
        },
        "missingFolders": missing_folders,
        "extraFolders": extra_folders,
        "missingFiles": missing_files,
        "extraFiles": extra_files,
    }


def print_summary(report):
    summary = report["summary"]
    print("=== Drive Shadow Diff Summary ===")
    print(f"Benchmark folders: {summary['benchmarkFolders']}")
    print(f"Shadow folders:    {summary['shadowFolders']}")
    print(f"Benchmark files:   {summary['benchmarkFiles']}")
    print(f"Shadow files:      {summary['shadowFiles']}")
    print()
    print(f"Missing folders in shadow: {summary['missingFolders']}")
    print(f"Extra folders in shadow:   {summary['extraFolders']}")
    print(f"Missing files in shadow:   {summary['missingFiles']}")
    print(f"Extra files in shadow:     {summary['extraFiles']}")

    print("\n=== By top-level bucket ===")
    for label, counts in report["byTopLevelBucket"].items():
        print(f"{label}:")
        if not counts:
            print("  (none)")
            continue
        for bucket in sorted(counts):
            print(f"  {bucket}: {counts[bucket]}")


def main():
    parser = argparse.ArgumentParser(
        description="Compare benchmark and shadow Google Drive inventories.",
    )
    parser.add_argument("benchmark_inventory", help="Path to the benchmark inventory JSON")
    parser.add_argument("shadow_inventory", help="Path to the shadow inventory JSON")
    parser.add_argument(
        "--json-out",
        dest="json_out",
        default=None,
        help="Optional path to write the diff report as JSON",
    )
    args = parser.parse_args()

    benchmark = load_inventory(os.path.expanduser(args.benchmark_inventory))
    shadow = load_inventory(os.path.expanduser(args.shadow_inventory))
    report = compare_inventories(benchmark.get("items", []), shadow.get("items", []))

    print_summary(report)

    if args.json_out:
        out_path = os.path.expanduser(args.json_out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nSaved diff report to {out_path}")


if __name__ == "__main__":
    main()
