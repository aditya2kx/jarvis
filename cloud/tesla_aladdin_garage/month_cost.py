"""Tesla Fleet monthly spend vs the $10 developer discount.

Tesla has no Fleet API for billing. We count our own billable calls
(status < 500) plus ingested telemetry Location signals, then apply
published rates: Data 500/$1, Commands 1000/$1, Wakes 50/$1,
streaming 150000 signals/$1. Auth token URLs are not counted.
Fail-open: email still sends if Firestore is down.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from cloud.tesla_aladdin_garage import persist

log = logging.getLogger("tesla_aladdin_garage")

DEFAULT_BUDGET_USD = 10.0
# Tesla developer.tesla.com pay-per-use (2026).
RATE_DATA = 500.0
RATE_COMMANDS = 1000.0
RATE_WAKES = 50.0
RATE_SIGNALS = 150_000.0


def budget_usd() -> float:
    raw = os.environ.get("TESLA_MONTH_BUDGET_USD", str(DEFAULT_BUDGET_USD))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_BUDGET_USD


def utc_month(now: Optional[datetime] = None) -> str:
    n = now or datetime.now(timezone.utc)
    return n.strftime("%Y-%m")


def estimate_usd(counts: dict[str, Any]) -> float:
    data = float(counts.get("data") or 0)
    commands = float(counts.get("commands") or 0)
    wakes = float(counts.get("wakes") or 0)
    signals = float(counts.get("signals") or 0)
    return (
        data / RATE_DATA
        + commands / RATE_COMMANDS
        + wakes / RATE_WAKES
        + signals / RATE_SIGNALS
    )


def format_tesla_cost_lines(info: dict[str, Any]) -> list[str]:
    budget = float(info.get("budget_usd") or DEFAULT_BUDGET_USD)
    month = info.get("month") or "this month"
    if not info.get("ok"):
        err = f" ({info.get('error')})" if info.get("error") else ""
        return [
            f"Tesla Fleet ({month}) vs ${budget:.0f}/mo discount: unavailable{err}",
            "Tesla has no usage API; check developer.tesla.com → Billing and Usage.",
        ]
    usd = float(info["usd"])
    remaining = budget - usd
    flag = "OVER CAP" if remaining < 0 else "within cap"
    leftover = f"-${abs(remaining):.2f}" if remaining < 0 else f"${remaining:.2f}"
    data = int(info.get("data") or 0)
    signals = int(info.get("signals") or 0)
    commands = int(info.get("commands") or 0)
    wakes = int(info.get("wakes") or 0)
    return [
        f"Tesla Fleet ({month}): ${usd:.2f} / ${budget:.2f} credit ({flag})",
        f"Remaining vs $10 discount: {leftover}. Data {data} · streaming {signals} · commands {commands} · wakes {wakes}.",
        "Jarvis-counted calls (status<500) + Location ingest. Portal is authoritative; we never wake_up.",
    ]


def format_tesla_cost_subject(info: dict[str, Any]) -> str:
    if not info.get("ok"):
        return "Tesla Fleet n/a"
    usd = float(info["usd"])
    budget = float(info.get("budget_usd") or DEFAULT_BUDGET_USD)
    return f"Tesla Fleet ${usd:.2f}/${budget:.0f}"


def month_tesla_cost(*, snapshot_fn=None) -> dict[str, Any]:
    month = utc_month()
    budget = budget_usd()
    try:
        snap = (snapshot_fn or persist.load_tesla_usage)(month)
    except Exception as e:  # noqa: BLE001
        log.error("tesla-aladdin-garage fail reason=tesla_month_cost err=%s", e)
        return {
            "ok": False,
            "usd": None,
            "budget_usd": budget,
            "month": month,
            "error": type(e).__name__,
        }
    counts = {
        "data": int(snap.get("data") or 0),
        "commands": int(snap.get("commands") or 0),
        "wakes": int(snap.get("wakes") or 0),
        "signals": int(snap.get("signals") or 0),
    }
    return {
        "ok": True,
        "usd": estimate_usd(counts),
        "budget_usd": budget,
        "month": month,
        "error": "",
        **counts,
    }
