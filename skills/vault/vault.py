"""Per-site credentials for agents from a 1Password vault, without echoing secrets.

    python3 -m skills.vault list                                  # item titles + sites
    python3 -m skills.vault get accounts.google.com               # username + site (never secrets)
    python3 -m skills.vault type accounts.google.com --field password --enter   # into the focused field
    python3 -m skills.vault type accounts.google.com --field otp --enter
    python3 -m skills.vault run adp --field password --as ADP_PASSWORD -- python3 login.py

The operator keeps one Login item per site (username, password, optional one-time password) in
the vault `Jarvis Agents` (override: JARVIS_VAULT). Agents read it through a 1Password service
account limited to that vault (`OP_SERVICE_ACCOUNT_TOKEN`, a Cursor Runtime Secret on Cloud
Agents). Passwords and codes only ever go to the keyboard (`type`, via xdotool stdin) or a child
process environment (`run`) — never stdout, so they stay out of transcripts and logs.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from urllib.parse import urlparse

SECRET_FIELDS = {"password", "otp"}


class VaultError(RuntimeError):
    pass


def vault_name() -> str:
    return os.environ.get("JARVIS_VAULT") or "Jarvis Agents"


def _op(*args: str) -> str:
    if not os.environ.get("OP_SERVICE_ACCOUNT_TOKEN"):
        raise VaultError("OP_SERVICE_ACCOUNT_TOKEN is not set (docs/contributing/dev-anywhere.md § Credentials vault)")
    r = subprocess.run(["op", *args, "--vault", vault_name()], capture_output=True, text=True)
    if r.returncode:
        raise VaultError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else f"op exit {r.returncode}")
    return r.stdout


def _host(url: str) -> str:
    return (urlparse(url if "://" in url else f"https://{url}").hostname or "").removeprefix("www.")


def list_items() -> list[dict]:
    items = json.loads(_op("item", "list", "--categories", "Login", "--format", "json") or "[]")
    return [{"id": i["id"], "title": i.get("title", ""),
             "sites": sorted({_host(u.get("href", "")) for u in i.get("urls", []) if u.get("href")})}
            for i in items]


def resolve(site: str, items: list[dict] | None = None) -> str:
    """Item id for a title or a site host (exact host, then parent domain)."""
    items = list_items() if items is None else items
    want = site.lower()
    for i in items:
        if i["title"].lower() == want:
            return i["id"]
    host = _host(site)
    for match in (lambda s: s == host, lambda s: host.endswith("." + s) or s.endswith("." + host)):
        hits = [i["id"] for i in items if any(match(s) for s in i["sites"])]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise VaultError(f"{site}: {len(hits)} items match — use the item title")
    raise VaultError(f"{site}: no Login item in vault '{vault_name()}'")


def field(item_id: str, name: str) -> str:
    if name == "otp":
        return _op("item", "get", item_id, "--otp").strip()
    return _op("item", "get", item_id, "--fields", f"label={name}", "--reveal").rstrip("\n")


def type_text(value: str, enter: bool = False) -> None:
    subprocess.run(["xdotool", "type", "--clearmodifiers", "--delay", "40", "--file", "-"],
                   input=value, text=True, check=True)
    if enter:
        subprocess.run(["xdotool", "key", "Return"], check=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m skills.vault", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("get").add_argument("site")
    t = sub.add_parser("type")
    t.add_argument("site")
    t.add_argument("--field", default="password", choices=["username", "password", "otp"])
    t.add_argument("--enter", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("site")
    r.add_argument("--field", default="password", choices=["username", "password", "otp"])
    r.add_argument("--as", dest="var", required=True)
    r.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            for i in list_items():
                print(f"{i['title']}\t{', '.join(i['sites'])}")
            return 0
        item = resolve(a.site)
        if a.cmd == "get":
            print(f"username={field(item, 'username')}")
            return 0
        value = field(item, a.field)
        if not value:
            raise VaultError(f"{a.site}: field '{a.field}' is empty")
        if a.cmd == "type":
            type_text(value, a.enter)
            print(f"vault: typed {a.field} for {a.site}")
            return 0
        cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
        if not cmd:
            raise VaultError("run: missing command after --")
        return subprocess.run(cmd, env={**os.environ, a.var: value}).returncode
    except VaultError as e:
        print(f"vault: {e}", file=sys.stderr)
        return 1
