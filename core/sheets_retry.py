#!/usr/bin/env python3
"""Shared exponential-backoff retry for Google Sheets API writes.

Both the model-sheet writer (``agents/bhaga/scripts/update_model_sheet.py``)
and the raw-ledger writer (``skills/tip_ledger_writer/writer.py``) route every
Sheets request through here so that BOTH the cloud daily-refresh AND the
laptop/non-cloud prod path get identical resilience.

Why: the Sheets API enforces "Write requests per minute per user" = 60. A
single model-sheet rebuild (9 tabs × clear/write/format) can burst past that
and the API answers HTTP 429 ``RESOURCE_EXHAUSTED``. Those are transient — the
quota is a sliding 1-minute window, so backing off and retrying lets the write
succeed once the window drains. We also retry 500/502/503/504 (server-side
hiccups) which the Sheets API surfaces intermittently.

``request_with_backoff`` wraps the central request-execution thunk so EVERY
batchUpdate / values write benefits without scattering try/excepts. Backoff is
jittered exponential: ~1, 2, 4, 8, 16(, 32) seconds plus 0..base jitter, 6
attempts by default, then it gives up and raises ``RuntimeError`` carrying the
HTTP code + response body (same message shape the callers raised before).
"""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
from typing import Callable

# HTTP status codes that are safe to retry. 429 = rate limit; 5xx = transient
# server-side errors. 4xx other than 429 are caller bugs and must NOT retry.
RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})

DEFAULT_MAX_ATTEMPTS = 6
DEFAULT_BASE_DELAY_S = 1.0


def _is_resource_exhausted(body: str) -> bool:
    """True if the JSON error body reports RESOURCE_EXHAUSTED / a 429 quota hit.

    Google sometimes returns the quota error with the HTTP status already set
    to 429 (caught by code), but we also parse the structured body so we retry
    even if a proxy rewrote the status line. Defensive: a non-JSON body just
    means "not a recognized quota error".
    """
    try:
        obj = json.loads(body)
    except (ValueError, TypeError):
        return False
    if not isinstance(obj, dict):
        return False
    err = obj.get("error")
    if not isinstance(err, dict):
        return False
    return err.get("status") == "RESOURCE_EXHAUSTED" or err.get("code") == 429


def is_retryable(code: int, body: str) -> bool:
    return code in RETRYABLE_HTTP_CODES or _is_resource_exhausted(body)


def request_with_backoff(
    do_call: Callable[[], dict],
    *,
    method: str,
    url: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY_S,
    logger: logging.Logger | None = None,
) -> dict:
    """Run ``do_call`` with jittered exponential backoff on transient errors.

    ``do_call`` performs the actual ``urlopen`` and returns the parsed dict.
    It MUST let :class:`urllib.error.HTTPError` propagate WITHOUT reading the
    body (the body can only be consumed once, and this helper needs it to
    decide retryability and to build the final error message).

    On a non-retryable HTTP error, or after the last attempt, raises
    ``RuntimeError`` with ``"{method} {url} -> HTTP {code}\\n{body}"`` — the
    same shape both callers raised historically, so downstream string checks
    (e.g. the writer's ``"400" in str(exc)`` tab-missing probe) keep working.
    """
    for attempt in range(max_attempts):
        try:
            return do_call()
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if is_retryable(e.code, body) and attempt < max_attempts - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
                if logger is not None:
                    logger.warning(
                        "[sheets-api] HTTP %d on %s %s (attempt %d/%d); "
                        "retrying in %.1fs",
                        e.code, method, url, attempt + 1, max_attempts, delay,
                    )
                time.sleep(delay)
                continue
            raise RuntimeError(f"{method} {url} -> HTTP {e.code}\n{body}") from None
    # Unreachable: the loop either returns or raises on the final attempt.
    raise RuntimeError(f"{method} {url} -> exhausted {max_attempts} attempts")
