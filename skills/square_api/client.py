#!/usr/bin/env python3
"""skills/square_api/client — thin Square REST helper.

Bearer-auth GET/POST against the Connect API with cursor pagination and bounded
retry. All BHAGA calls are READS, so retrying on 429/5xx is safe (no risk of
double side effects). Kept dependency-free (urllib) so it runs in the slim
Cloud Run image without extra packages.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.square_api import auth as _auth


class SquareApiError(RuntimeError):
    """Raised on a non-retryable or exhausted Square API error."""


class SquareClient:
    """Authenticated Square REST client for one store.

    Lazily resolves (and refreshes) the access token via ``auth.get_access_token``
    on construction so a long pull uses one stable token.
    """

    def __init__(self, store: str = "palmetto", *, access_token: str | None = None,
                 max_retries: int = 3, timeout_s: int = 60):
        self.store = store
        self._token = access_token or _auth.get_access_token(store)
        self.base = _auth.api_base()
        self.max_retries = max_retries
        self.timeout_s = timeout_s

    # ── low-level request ────────────────────────────────────────────
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Square-Version": _auth.SQUARE_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 body: dict | None = None) -> dict:
        url = f"{self.base}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            req = urllib.request.Request(url, data=data, method=method)
            for k, v in self._headers().items():
                req.add_header(k, v)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")
                # Retry transient throttling / server errors only.
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    sleep_s = min(2 ** attempt, 10)
                    print(f"[square_api.client] {method} {path} -> {exc.code}; "
                          f"retry {attempt}/{self.max_retries} in {sleep_s}s")
                    time.sleep(sleep_s)
                    last_err = SquareApiError(f"{exc.code}: {detail[:300]}")
                    continue
                raise SquareApiError(
                    f"Square {method} {path} failed {exc.code}: {detail[:400]}"
                ) from exc
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 10))
                    last_err = exc
                    continue
                raise SquareApiError(f"Square {method} {path} network error: {exc}") from exc
        raise SquareApiError(f"Square {method} {path} exhausted retries: {last_err}")

    def get(self, path: str, *, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, *, body: dict | None = None) -> dict:
        return self._request("POST", path, body=body)

    # ── cursor pagination helpers ────────────────────────────────────
    def get_paginated(self, path: str, *, params: dict | None = None,
                      items_key: str) -> list:
        """GET a cursor-paginated list endpoint (Payments). ``cursor`` is a
        query param; the response carries ``cursor`` for the next page."""
        out: list = []
        params = dict(params or {})
        while True:
            resp = self.get(path, params=params)
            out.extend(resp.get(items_key, []) or [])
            cursor = resp.get("cursor")
            if not cursor:
                return out
            params["cursor"] = cursor

    def post_paginated(self, path: str, *, body: dict, items_key: str) -> list:
        """POST a cursor-paginated search endpoint (Orders search). ``cursor``
        is in the request/response body."""
        out: list = []
        body = dict(body)
        while True:
            resp = self.post(path, body=body)
            out.extend(resp.get(items_key, []) or [])
            cursor = resp.get("cursor")
            if not cursor:
                return out
            body["cursor"] = cursor
