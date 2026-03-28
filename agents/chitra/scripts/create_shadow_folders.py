#!/usr/bin/env python3
"""Create a shadow folder tree in Google Drive, derived from the document registry.

This script reads document-registry.json and derives the folder structure
from it — no hardcoded folder names. Works for any CHITRA user whose
registry has been populated (typically by analyzing their prior-year return).

Usage:
    python create_shadow_folders.py [--shadow-name 2025-test] [--dry-run]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
from core.config_loader import refresh_access_token, get_drive_id, project_dir, kb_path


def load_registry():
    path = kb_path("document-registry.json")
    with open(path) as f:
        return json.load(f)


def derive_folder_tree(registry):
    """Derive the set of folders needed from the registry's driveFolderStructure.

    Returns a dict of {relative_path: None} where relative_path is like
    '01 - W-2s & Employment/Aditya - DoorDash'. The top two levels
    ('Taxes' and 'Taxes/{year}') are excluded since the shadow root replaces them.
    """
    structure = registry.get("driveFolderStructure", {})
    tax_year = str(registry.get("taxYear", ""))

    skip_prefixes = {"Taxes", f"Taxes/{tax_year}"}
    folders = {}

    for key in structure:
        if key in skip_prefixes:
            continue
        folders[key] = None

    return folders


def build_parent_child_map(folder_paths):
    """Organize flat folder paths into a parent→children tree for creation order.

    Returns (top_level_list, children_map) where children_map maps
    a top-level folder name to its list of subfolder names.
    """
    top_level = []
    children = {}

    for path in sorted(folder_paths):
        parts = path.split("/", 1)
        if len(parts) == 1:
            if path not in top_level:
                top_level.append(path)
        else:
            parent, child = parts
            if parent not in top_level:
                top_level.append(parent)
            children.setdefault(parent, []).append(child)

    return top_level, children


def main():
    parser = argparse.ArgumentParser(
        description="Create a shadow Drive folder tree derived from the document registry.",
    )
    parser.add_argument(
        "--shadow-name",
        default=None,
        help="Name for the shadow folder (default: {taxYear}-test)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the derived folder tree without creating anything in Drive",
    )
    args = parser.parse_args()

    registry = load_registry()
    tax_year = str(registry.get("taxYear", "unknown"))
    shadow_name = args.shadow_name or f"{tax_year}-test"

    folder_paths = derive_folder_tree(registry)
    top_level, children = build_parent_child_map(folder_paths)

    print(f"Derived folder tree for '{shadow_name}/' from registry ({len(folder_paths)} folders):\n")
    for tl in top_level:
        print(f"  {tl}/")
        for child in children.get(tl, []):
            print(f"    {child}/")

    if args.dry_run:
        print("\n[dry-run] No folders created.")
        return

    from skills.google_drive.create_folder import create_folder

    taxes_root = get_drive_id("taxes_root_id")
    token = refresh_access_token()

    root = create_folder(token, shadow_name, taxes_root)
    root_id = root["id"]
    print(f"\nROOT: {shadow_name} [{root_id}]")

    mapping = {"root_id": root_id, "shadow_name": shadow_name, "folders": {}}

    for name in top_level:
        token = refresh_access_token()
        f = create_folder(token, name, root_id)
        fid = f["id"]
        mapping["folders"][name] = fid
        print(f"  Created: {name} [{fid}]")

        for sub in children.get(name, []):
            token = refresh_access_token()
            s = create_folder(token, sub, fid)
            sid = s["id"]
            mapping["folders"][f"{name}/{sub}"] = sid
            print(f"    Created: {sub} [{sid}]")

    out_path = os.path.join(project_dir(), "extracted", f"drive-{shadow_name}-folder-ids.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(mapping, f, indent=2)
    print(f"\nSaved folder ID mapping to {out_path}")
    print(f"Total folders created: {len(mapping['folders']) + 1}")


if __name__ == "__main__":
    main()
