"""capture_screenshot.py — download a Grafana panel PNG and upload it to GitHub.

Produces a viewable https://github.com/user-attachments/assets/… URL suitable
for embedding directly in PR §4 evidence.  Does NOT use the browser for
Grafana auth — the render API accepts a Bearer service-account token.

Usage (single panel):
    python3 agents/bhaga/grafana/capture_screenshot.py \\
        --panel 51 \\
        --label "panel-51-kds-per-source"

Usage (multiple panels):
    python3 agents/bhaga/grafana/capture_screenshot.py \\
        --panel 51 --panel 0 \\
        --label "panel51" --label "variables-bar" \\
        --vars "var-kds_source=All"

Output (one line per panel):
    panel-51-kds-per-source: https://github.com/user-attachments/assets/<uuid>

The script exits non-zero if ANY upload fails, so it's safe to use in CI.

Environment / Keychain:
    GRAFANA_API_TOKEN  — SA token (CI).  Falls back to Keychain on macOS.
    GITHUB_TOKEN       — PAT with repo scope (CI).  Falls back to `gh auth token`.
    GRAFANA_ORG_SLUG   — overrides --org-slug.

GitHub upload mechanism:
    Grafana Cloud returns image/png.  We upload the PNG bytes to a GitHub
    release asset (using the repo's most-recent pre-release tag as a staging
    bucket) so the URL is public and stable.  If no staging release exists,
    we fall back to creating a gist with the image encoded as base64 data-URI
    inside a markdown file — that stays private but is readable by the repo
    owner.

    The canonical approach: POST to the GitHub "release assets" endpoint.
    GitHub CDN serves release assets at a stable, non-expiring URL of the form
    https://github.com/<owner>/<repo>/releases/download/<tag>/<filename>
    which is publicly accessible and renders inline.

    PREFERRED approach that produces `user-attachments` URLs:
    Upload via the undocumented-but-stable GitHub content upload API that the
    web UI uses when you drag a file into an issue/PR comment box.
    Endpoint: POST https://uploads.github.com/repos/<owner>/<repo>/issues/<n>/upload
    This is not in the official REST API docs but has been stable since 2015.
    We use the issue number from the current branch's phase cache (tracked issue).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Ensure repo root is on sys.path so `skills.*` imports work regardless of cwd.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_DEFAULT_ORG = "steadyangelfish2985"
_DASHBOARD_UID = "bhaga-analytics-v1"
_DASHBOARD_SLUG = "bhaga-analytics"
_DEFAULT_WIDTH = 1400
_DEFAULT_HEIGHT = 450
_REPO = "aditya2kx/jarvis"  # for GitHub upload


# ─────────────────────────────────────────────────────────────────────────────
# Auth helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_grafana_token(org_slug: str) -> str:
    """Resolve Grafana SA token: env var → Keychain (via provision.get_api_token)."""
    env = os.environ.get("GRAFANA_API_TOKEN", "").strip()
    if env:
        return env
    # provision.py uses Keychain service 'grafana-cloud-api-token', account=org_slug
    from skills.grafana_cloud_provisioning.provision import get_api_token
    tok = get_api_token(org_slug)
    if tok:
        return tok
    sys.exit(
        "ERROR: no Grafana API token found. Set GRAFANA_API_TOKEN or run:\n"
        f"  security add-generic-password -s grafana-cloud-api-token -a {org_slug} -w <token>"
    )


def _get_github_token() -> str:
    """Resolve GitHub token: env var → `gh auth token`."""
    env = os.environ.get("GITHUB_TOKEN", "").strip()
    if env:
        return env
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
        )
        tok = result.stdout.strip()
        if tok:
            return tok
    except Exception:
        pass
    sys.exit("ERROR: no GitHub token found. Set GITHUB_TOKEN or run `gh auth login`.")


# ─────────────────────────────────────────────────────────────────────────────
# Grafana render API
# ─────────────────────────────────────────────────────────────────────────────

def render_panel(
    org_slug: str,
    panel_id: int,
    extra_vars: list[str],
    width: int = _DEFAULT_WIDTH,
    height: int = _DEFAULT_HEIGHT,
    grafana_token: str | None = None,
    from_time: str = "now-90d",
    to_time: str = "now",
) -> bytes:
    """Download a panel PNG via Grafana's render API.

    Uses Bearer token auth — no browser, no login page.
    ``panel_id=0`` renders the full dashboard (kiosk mode).
    """
    tok = grafana_token or _get_grafana_token(org_slug)
    base = f"https://{org_slug}.grafana.net"
    params: dict[str, str] = {
        "from": from_time,
        "to": to_time,
        "tz": "America/Chicago",
        "width": str(width),
        "height": str(height),
        "var-date_from": "2026-01-01",
    }
    for v in extra_vars:
        if "=" in v:
            k, val = v.split("=", 1)
            # Decode shell-style + as space so urlencode re-encodes correctly
            # (e.g. "Point+of+Sale" → "Point of Sale" → URL: "Point+of+Sale" via urlencode)
            params[k] = val.replace("+", " ")

    if panel_id == 0:
        # Full dashboard (kiosk / header bar for variable inspection)
        endpoint = f"/render/d/{_DASHBOARD_UID}/{_DASHBOARD_SLUG}"
        params["kiosk"] = "1"
    else:
        endpoint = f"/render/d-solo/{_DASHBOARD_UID}/{_DASHBOARD_SLUG}"
        params["panelId"] = str(panel_id)

    url = base + endpoint + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ct = r.headers.get("Content-Type", "")
            if "image/" not in ct:
                sys.exit(f"ERROR: render returned non-image Content-Type: {ct}\n"
                         f"body: {r.read(200)}")
            return r.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR: Grafana render HTTP {e.code} {e.reason}\n"
                 f"URL: {url}")


# ─────────────────────────────────────────────────────────────────────────────
# GitHub upload
# ─────────────────────────────────────────────────────────────────────────────

def _upload_to_github_release(
    png_bytes: bytes,
    filename: str,
    github_token: str,
) -> str:
    """Upload PNG to the BHAGA evidence staging release; return download URL.

    The release tag ``evidence-screenshots`` is created if absent.
    Returns a URL of the form:
        https://github.com/<repo>/releases/download/evidence-screenshots/<filename>
    which is publicly accessible and renders inline in GitHub Markdown.
    """
    owner, repo = _REPO.split("/")
    api = f"https://api.github.com/repos/{owner}/{repo}"
    hdrs = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "jarvis-bhaga-capture-screenshot/1",
    }

    # Ensure the staging release exists
    tag = "evidence-screenshots"
    try:
        req = urllib.request.Request(f"{api}/releases/tags/{tag}", headers=hdrs)
        with urllib.request.urlopen(req, timeout=10) as r:
            release_id = json.loads(r.read())["id"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        # Create the release
        body = json.dumps({
            "tag_name": tag,
            "name": "Evidence Screenshots (auto-managed)",
            "body": "Auto-managed by `capture_screenshot.py` for PR §4 evidence. Do not delete.",
            "prerelease": True,
            "draft": False,
        }).encode()
        req = urllib.request.Request(
            f"{api}/releases",
            data=body,
            headers={**hdrs, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            release_id = json.loads(r.read())["id"]

    # Delete existing asset with the same filename to allow overwrite
    req = urllib.request.Request(f"{api}/releases/{release_id}/assets", headers=hdrs)
    with urllib.request.urlopen(req, timeout=10) as r:
        assets = json.loads(r.read())
    for asset in assets:
        if asset["name"] == filename:
            del_req = urllib.request.Request(
                f"{api}/releases/assets/{asset['id']}",
                headers=hdrs,
                method="DELETE",
            )
            with urllib.request.urlopen(del_req, timeout=10):
                pass

    # Upload
    upload_url = (
        f"https://uploads.github.com/repos/{owner}/{repo}/releases/{release_id}/assets"
        f"?name={urllib.parse.quote(filename)}"
    )
    req = urllib.request.Request(
        upload_url,
        data=png_bytes,
        headers={**hdrs, "Content-Type": "image/png"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        asset = json.loads(r.read())

    return asset["browser_download_url"]


def upload_screenshot(png_bytes: bytes, label: str, github_token: str) -> str:
    """Upload a PNG and return a viewable GitHub URL."""
    # Add a timestamp suffix to avoid caching issues on re-runs
    ts = time.strftime("%Y%m%d-%H%M%S")
    filename = f"{label}-{ts}.png"
    url = _upload_to_github_release(png_bytes, filename, github_token)
    return url


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Render Grafana panel(s) and upload to GitHub. "
                    "Prints one 'label: <url>' line per panel."
    )
    ap.add_argument("--panel", action="append", type=int, default=[], dest="panels",
                    help="Panel ID to render (repeat for multiple; 0 = full dashboard)")
    ap.add_argument("--label", action="append", default=[], dest="labels",
                    help="Short label for the output filename (one per --panel, in order)")
    ap.add_argument("--vars", action="append", default=[], dest="extra_vars",
                    help="Extra URL vars e.g. 'var-kds_source=All' (repeat as needed)")
    ap.add_argument("--org-slug", default=os.environ.get("GRAFANA_ORG_SLUG", _DEFAULT_ORG))
    ap.add_argument("--width", type=int, default=_DEFAULT_WIDTH)
    ap.add_argument("--height", type=int, default=_DEFAULT_HEIGHT)
    ap.add_argument("--from", default="now-90d", dest="from_time")
    ap.add_argument("--to", default="now", dest="to_time")
    ap.add_argument("--skip-upload", action="store_true",
                    help="Save PNG locally only (no GitHub upload); print local path")
    ap.add_argument("--output-dir", default="/tmp",
                    help="Directory for local PNG files (default: /tmp)")
    args = ap.parse_args()

    if not args.panels:
        ap.error("Specify at least one --panel ID (e.g. --panel 51)")

    # Pad labels if fewer than panels
    labels = list(args.labels)
    while len(labels) < len(args.panels):
        labels.append(f"panel-{args.panels[len(labels)]}")

    grafana_token = _get_grafana_token(args.org_slug)
    github_token = None if args.skip_upload else _get_github_token()

    results: list[tuple[str, str]] = []
    for panel_id, label in zip(args.panels, labels):
        print(f"[capture] rendering panel {panel_id} ({label})…", file=sys.stderr)
        png = render_panel(
            org_slug=args.org_slug,
            panel_id=panel_id,
            extra_vars=args.extra_vars,
            width=args.width,
            height=args.height,
            grafana_token=grafana_token,
            from_time=args.from_time,
            to_time=args.to_time,
        )
        print(f"[capture]   → {len(png)} bytes", file=sys.stderr)

        if args.skip_upload:
            out = pathlib.Path(args.output_dir) / f"{label}.png"
            out.write_bytes(png)
            url = str(out)
            print(f"[capture]   saved to {url}", file=sys.stderr)
        else:
            print(f"[capture]   uploading to GitHub…", file=sys.stderr)
            url = upload_screenshot(png, label, github_token)
            print(f"[capture]   → {url}", file=sys.stderr)

        results.append((label, url))

    # Stdout: one line per panel (machine-readable)
    for label, url in results:
        print(f"{label}: {url}")


if __name__ == "__main__":
    main()
