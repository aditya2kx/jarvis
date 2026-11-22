"""Which Google accounts may sign in to a Cloud Run service (its IAP accessor list).

Python twin of apps/operator-console/lib/access/iap.ts. The caller's identity needs
``roles/iap.admin`` on that one IAP resource.

    python3 -m skills.google_signin.access list  --service store-tasks-app
    python3 -m skills.google_signin.access grant --service store-tasks-app --email a@b.com
    python3 -m skills.google_signin.access copy  --service store-tasks-app --from operator-console
"""

from __future__ import annotations

import argparse
import sys

import google.auth
from google.auth.transport.requests import AuthorizedSession

from skills.google_signin.identity import PROJECT_NUMBER, REGION

ACCESSOR = "roles/iap.httpsResourceAccessor"


def resource(service: str) -> str:
    return (f"https://iap.googleapis.com/v1/projects/{PROJECT_NUMBER}/iap_web/"
            f"cloud_run-{REGION}/services/{service}")


class Access:
    def __init__(self, service: str, session=None) -> None:
        self.service = service
        self.session = session

    def _call(self, method: str, body: dict) -> dict:
        if self.session is None:
            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            self.session = AuthorizedSession(creds)
        r = self.session.post(f"{resource(self.service)}:{method}", json=body, timeout=20)
        if r.status_code == 403:
            raise PermissionError(f"needs roles/iap.admin on {self.service}'s Google sign-in")
        r.raise_for_status()
        return r.json()

    def members(self) -> list[str]:
        """Emails that can sign in (``user:`` members only, unconditional bindings)."""
        policy = self._call("getIamPolicy", {})
        return sorted(m.split(":", 1)[1].lower() for b in policy.get("bindings", [])
                      if b["role"] == ACCESSOR and "condition" not in b
                      for m in b.get("members", []) if m.startswith("user:"))

    def set(self, email: str, allowed: bool) -> str:
        """Add or remove one account. Read-modify-write on the etag, one retry on a conflict."""
        member = f"user:{email.strip().lower()}"
        for attempt in (1, 2):
            policy = self._call("getIamPolicy", {})
            bindings = policy.setdefault("bindings", [])
            binding = next((b for b in bindings if b["role"] == ACCESSOR and "condition" not in b), None)
            has = binding is not None and member in [m.lower() for m in binding.get("members", [])]
            if has == allowed:
                return "unchanged"
            if allowed:
                if binding is None:
                    binding = {"role": ACCESSOR, "members": []}
                    bindings.append(binding)
                binding["members"].append(member)
            else:
                binding["members"] = [m for m in binding["members"] if m.lower() != member]
            policy["bindings"] = [b for b in bindings if b.get("members")]
            try:
                self._call("setIamPolicy", {"policy": policy})
                return "granted" if allowed else "removed"
            except Exception as e:
                if attempt == 2 or "409" not in str(e):
                    raise
        return "unchanged"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["list", "grant", "revoke", "copy"])
    ap.add_argument("--service", required=True)
    ap.add_argument("--email", action="append", default=[])
    ap.add_argument("--from", dest="source", help="copy: service whose sign-in list to copy")
    ap.add_argument("--skip", action="append", default=[], help="copy: emails to leave out")
    args = ap.parse_args(argv)
    access = Access(args.service)
    if args.cmd == "list":
        print("\n".join(access.members()))
    elif args.cmd == "copy":
        skip = {e.lower() for e in args.skip}
        for email in Access(args.source).members():
            if email not in skip:
                print(email, access.set(email, True))
    else:
        for email in args.email:
            print(email, access.set(email, args.cmd == "grant"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
