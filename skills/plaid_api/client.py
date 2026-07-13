#!/usr/bin/env python3
"""skills/plaid_api/client — thin Plaid REST helper (urllib, no SDK)."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.plaid_api import auth as _auth


class PlaidApiError(RuntimeError):
    """Raised on a non-retryable or exhausted Plaid API error."""


class PlaidClient:
    def __init__(self, *, max_retries: int = 3, timeout_s: int = 60):
        self.base = _auth.api_base()
        self.client_id = _auth.client_id()
        self.secret = _auth.client_secret()
        self.max_retries = max_retries
        self.timeout_s = timeout_s

    def _post(self, path: str, body: dict) -> dict:
        url = f"{self.base}{path}"
        payload = {"client_id": self.client_id, "secret": self.secret, **body}
        data = json.dumps(payload).encode("utf-8")
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Plaid-Version", "2020-09-14")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    sleep_s = min(2**attempt, 10)
                    print(
                        f"[plaid_api.client] POST {path} -> {exc.code}; "
                        f"retry {attempt}/{self.max_retries} in {sleep_s}s"
                    )
                    time.sleep(sleep_s)
                    last_err = PlaidApiError(f"{exc.code}: {detail[:300]}")
                    continue
                raise PlaidApiError(
                    f"Plaid POST {path} failed {exc.code}: {detail[:400]}"
                ) from exc
            except urllib.error.URLError as exc:
                last_err = exc
                if attempt < self.max_retries:
                    time.sleep(min(2**attempt, 10))
                    continue
                raise PlaidApiError(f"Plaid POST {path} network error: {exc}") from exc
        raise PlaidApiError(f"Plaid POST {path} failed after retries: {last_err}")

    def link_token_create(
        self,
        *,
        client_user_id: str,
        products: list[str] | None = None,
        webhook: str | None = None,
        days_requested: int = 730,
    ) -> dict:
        body: dict = {
            "user": {"client_user_id": client_user_id},
            "client_name": "Palmetto Operator Console",
            "products": products or ["transactions"],
            "country_codes": ["US"],
            "language": "en",
            "transactions": {"days_requested": days_requested},
        }
        if webhook:
            body["webhook"] = webhook
        return self._post("/link/token/create", body)

    def item_public_token_exchange(self, public_token: str) -> dict:
        return self._post("/item/public_token/exchange", {"public_token": public_token})

    def transactions_sync(
        self,
        access_token: str,
        cursor: str | None = None,
        *,
        count: int = 500,
    ) -> dict:
        body: dict = {
            "access_token": access_token,
            "count": count,
            "options": {"include_personal_finance_category": True},
        }
        if cursor is not None:
            body["cursor"] = cursor
        return self._post("/transactions/sync", body)

    def item_get(self, access_token: str) -> dict:
        return self._post("/item/get", {"access_token": access_token})
