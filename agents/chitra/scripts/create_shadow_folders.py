#!/usr/bin/env python3
"""Create a shadow folder tree in Google Drive from a derived registry.

Reads driveFolderStructure from the specified registry (or the default
derived-registry-2025.json) and creates every folder path in Drive.
Supports arbitrary nesting depth, not just 2 levels.

Usage:
    python create_shadow_folders.py [--registry path.json] [--shadow-name 2025-test] [--dry-run]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
from core.config_loader import refresh_access_token, get_drive_id, project_dir, kb_path


def load_registry(path=None):
    path = path or kb_path("derived-registry-2025.json")
    if not os.path.exists(path):
        fallback = kb_path("document-registry.json")
        if os.path.exists(fallback):
            path = fallback
        else:
            raise FileNotFoundError(f"No registry found at {path} or fallback")
    with open(path) as f:
        return json.load(f)


def extract_folder_paths(registry):
    """Extract folder paths from registry's driveFolderStructure.

    Strips the Taxes/{year}/ prefix since the shadow root replaces it.
    Returns sorted list of relative paths.
    """
    structure = registry.get("driveFolderStructure", {})
    tax_year = str(registry.get("taxYear", ""))

    skip_prefixes = {"Taxes", f"Taxes/{tax_year}"}
    paths = []
    for key in structure:
        if key in skip_prefixes:
            continue
        paths.append(key)

    return sorted(paths)


def ensure_all_ancestors(paths):
    """Given a list of folder paths, ensure every ancestor path is also present."""
    all_paths = set()
    for p in paths:
        parts = p.split("/")
        for i in range(1, len(parts) + 1):
            all_paths.add("/".join(parts[:i]))
    return sorted(all_paths)


def create_folders_recursive(folder_paths, shadow_root_id, dry_run=False):
    """Create folders in Drive, respecting parent-child ordering.

    Uses a path->ID mapping to resolve parents at any nesting depth.
    """
    if dry_run:
        return {}

    from skills.google_drive.create_folder import create_folder

    path_to_id = {}

    for path in ensure_all_ancestors(folder_paths):
        parts = path.split("/")
        name = parts[-1]

        if len(parts) == 1:
            parent_id = shadow_root_id
        else:
            parent_path = "/".join(parts[:-1])
            parent_id = path_to_id.get(parent_path)
            if not parent_id:
                raise RuntimeError(f"Parent '{parent_path}' not yet created for '{path}'")

        token = refresh_access_token()
        result = create_folder(token, name, parent_id)
        fid = result["id"]
        path_to_id[path] = fid

        indent = "  " * len(parts)
        print(f"{indent}Created: {name} [{fid}]")

    return path_to_id


def main():
    parser = argparse.ArgumentParser(
        description="Create a shadow Drive folder tree from a derived registry.",
    )
    parser.add_argument(
        "--registry",
        default=None,
        help="Path to registry JSON with driveFolderStructure (default: derived-registry-2025.json)",
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

    registry = load_registry(args.registry)
    tax_year = str(registry.get("taxYear", "unknown"))
    shadow_name = args.shadow_name or f"{tax_year}-test"

    folder_paths = extract_folder_paths(registry)
    all_paths = ensure_all_ancestors(folder_paths)

    print(f"Folder tree for '{shadow_name}/' ({len(all_paths)} folders):\n")
    for p in all_paths:
        depth = p.count("/")
        name = p.split("/")[-1]
        indent = "  " * (depth + 1)
        print(f"{indent}{name}/")

    if args.dry_run:
        print("\n[dry-run] No folders created.")
        return

    from skills.google_drive.create_folder import create_folder

    taxes_root = get_drive_id("taxes_root_id")
    token = refresh_access_token()

    root = create_folder(token, shadow_name, taxes_root)
    root_id = root["id"]
    print(f"\nROOT: {shadow_name} [{root_id}]")

    path_to_id = create_folders_recursive(folder_paths, root_id)

    mapping = {
        "root_id": root_id,
        "shadow_name": shadow_name,
        "folders": path_to_id,
    }

    out_path = os.path.join(project_dir(), "extracted", f"drive-{shadow_name}-folder-ids.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(mapping, f, indent=2)
    print(f"\nSaved folder ID mapping to {out_path}")
    print(f"Total folders created: {len(path_to_id) + 1}")


if __name__ == "__main__":
    main()
