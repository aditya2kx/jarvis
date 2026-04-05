#!/usr/bin/env python3
"""Import portal credentials from a Chrome Password Manager CSV export into macOS Keychain.

Flow:
  1. User exports passwords from Chrome (Settings → Passwords → Export)
  2. User places the CSV anywhere (default: ~/Downloads or credentials/)
  3. This script reads the CSV, matches URLs to known Jarvis portals
  4. Shows matches for user confirmation
  5. Stores each in Keychain under jarvis-* service names
  6. Securely deletes the CSV

Usage:
  python credentials/import_from_chrome.py [path_to_csv]
  python credentials/import_from_chrome.py --auto   # searches common locations
"""

import csv
import os
import subprocess
import sys

PORTAL_URL_MAP = {
    "jarvis-schwab": {
        "name": "Charles Schwab",
        "patterns": ["schwab.com"],
    },
    "jarvis-etrade": {
        "name": "E*Trade",
        "patterns": ["etrade.com", "us.etrade.com"],
    },
    "jarvis-wellsfargo": {
        "name": "Wells Fargo",
        "patterns": ["wellsfargo.com"],
    },
    "jarvis-fidelity": {
        "name": "Fidelity",
        "patterns": ["fidelity.com", "nb.fidelity.com"],
    },
    "jarvis-robinhood": {
        "name": "Robinhood",
        "patterns": ["robinhood.com"],
    },
    "jarvis-homebase": {
        "name": "Homebase",
        "patterns": ["joinhomebase.com"],
    },
    "jarvis-hsa": {
        "name": "HSA Provider",
        "patterns": ["healthequity.com", "hsabank.com", "optum.com", "livelyme.com"],
    },
    "jarvis-chase": {
        "name": "JPMorgan Chase",
        "patterns": ["chase.com"],
    },
    "jarvis-obie": {
        "name": "Obie Insurance",
        "patterns": ["obieinsurance.com", "obie.com"],
    },
}


def find_csv():
    """Search common locations for Chrome password export CSV."""
    candidates = [
        os.path.expanduser("~/Downloads/passwords.csv"),
        os.path.expanduser("~/Downloads/Chrome Passwords.csv"),
        os.path.join(os.path.dirname(__file__), "passwords.csv"),
        os.path.join(os.path.dirname(__file__), "Chrome Passwords.csv"),
        os.path.expanduser("~/Desktop/passwords.csv"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def read_csv(path):
    """Read Chrome password export CSV. Returns list of dicts."""
    entries = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = row.get("url") or row.get("URL") or row.get("origin_url") or ""
            username = row.get("username") or row.get("Username") or ""
            password = row.get("password") or row.get("Password") or ""
            name = row.get("name") or row.get("Name") or ""
            if url and username and password:
                entries.append({
                    "url": url,
                    "username": username.strip(),
                    "password": password,
                    "name": name,
                })
    return entries


def match_portals(entries):
    """Match CSV entries to known Jarvis portals. Returns list of matches."""
    matches = []
    seen_services = set()

    for service, info in PORTAL_URL_MAP.items():
        for entry in entries:
            url_lower = entry["url"].lower()
            for pattern in info["patterns"]:
                if pattern in url_lower:
                    if service not in seen_services:
                        matches.append({
                            "service": service,
                            "portal_name": info["name"],
                            "url": entry["url"],
                            "username": entry["username"],
                            "password": entry["password"],
                        })
                        seen_services.add(service)
                    break
    return matches


def check_existing(service):
    """Check if a Keychain entry already exists for this service."""
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode == 0:
        for line in (result.stdout + result.stderr).split("\n"):
            if '"acct"' in line:
                parts = line.split('"')
                if len(parts) >= 4:
                    return parts[-2]
    return None


def store_in_keychain(service, username, password):
    """Store a credential in macOS Keychain."""
    subprocess.run(
        ["security", "delete-generic-password", "-s", service, "-a", username],
        capture_output=True,
    )
    result = subprocess.run(
        ["security", "add-generic-password", "-s", service, "-a", username, "-w", password],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def secure_delete(path):
    """Delete the CSV file."""
    try:
        os.remove(path)
        print(f"\n  CSV deleted: {path}")
    except OSError as e:
        print(f"\n  Warning: could not delete CSV: {e}")
        print(f"  Please delete manually: rm '{path}'")


def main():
    csv_path = None

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--auto":
            csv_path = find_csv()
            if not csv_path:
                print("No Chrome password CSV found in common locations.")
                print("Export from Chrome: Settings → Passwords → Export passwords")
                sys.exit(1)
        else:
            csv_path = arg

    if not csv_path:
        csv_path = find_csv()
        if not csv_path:
            print("Usage: python credentials/import_from_chrome.py [path_to_csv]")
            print("\nExport from Chrome: Settings → Passwords → Export passwords")
            sys.exit(1)

    if not os.path.exists(csv_path):
        print(f"File not found: {csv_path}")
        sys.exit(1)

    print(f"Reading: {csv_path}")
    entries = read_csv(csv_path)
    print(f"Found {len(entries)} total password entries in CSV\n")

    matches = match_portals(entries)

    if not matches:
        print("No matching portal credentials found in the CSV.")
        secure_delete(csv_path)
        sys.exit(0)

    print(f"Found {len(matches)} portal matches:\n")
    print(f"  {'#':<4} {'Portal':<20} {'Service':<22} {'Username':<25} {'Existing?'}")
    print(f"  {'─'*4} {'─'*20} {'─'*22} {'─'*25} {'─'*15}")

    for i, m in enumerate(matches, 1):
        existing = check_existing(m["service"])
        status = f"YES (user: {existing})" if existing else "no"
        print(f"  {i:<4} {m['portal_name']:<20} {m['service']:<22} {m['username']:<25} {status}")

    print(f"\nStore all {len(matches)} in Keychain? [y/n/select] ", end="", flush=True)
    choice = input().strip().lower()

    if choice == "n":
        print("Aborted.")
        secure_delete(csv_path)
        sys.exit(0)

    if choice == "select":
        print("Enter numbers to store (comma-separated, e.g. 1,3,5): ", end="", flush=True)
        nums = input().strip()
        indices = [int(n.strip()) - 1 for n in nums.split(",") if n.strip().isdigit()]
        selected = [matches[i] for i in indices if 0 <= i < len(matches)]
    else:
        selected = matches

    stored = 0
    for m in selected:
        ok = store_in_keychain(m["service"], m["username"], m["password"])
        status = "STORED" if ok else "FAILED"
        print(f"  {status}: {m['service']} → {m['username']}")
        if ok:
            stored += 1

    print(f"\n  {stored}/{len(selected)} credentials stored in Keychain.")

    secure_delete(csv_path)
    print("  Done.")


if __name__ == "__main__":
    main()
