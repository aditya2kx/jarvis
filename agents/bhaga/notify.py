"""BHAGA Slack notification helpers.

Thin wrapper over skills/slack/adapter that knows BHAGA's identity (bot token,
DM channel) and formats messages consistently for the daily refresh pipeline.

All helpers are no-ops when SLACK_DISABLED env var is set (useful in unit
tests and dry-runs).

Usage:
    from agents.bhaga.notify import success_heartbeat, failure_alert, info_ping

    success_heartbeat(date="2026-05-15", tabs_written=5, runtime_s=42)

    try:
        run_scrape(...)
    except Exception as e:
        failure_alert(step="square_login", exception=e)
        raise
"""

from __future__ import annotations

import os
import socket
import sys
import traceback as _tb
from typing import Optional

# Ensure project root is on sys.path so skills.* and core.* resolve.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from skills.slack.adapter import send_message  # noqa: E402

AGENT_NAME = "bhaga"
DM_CHANNEL = "D0ATWHSA14J"  # mirrored from config.yaml > slack.agents.bhaga.dm_channel
SLACK_DISABLED_ENV = "BHAGA_SLACK_DISABLED"


def _silenced() -> bool:
    return bool(os.environ.get(SLACK_DISABLED_ENV))


def _safe_send(text: str) -> Optional[dict]:
    """Send to BHAGA's DM with full error swallowing.

    NEVER raise from a notification helper; the orchestrator must always
    surface the underlying scrape failure, not a Slack-API failure.
    """
    if _silenced():
        print(f"[BHAGA_SLACK_DISABLED] would send: {text[:200]}")
        return None
    try:
        return send_message(DM_CHANNEL, text, agent=AGENT_NAME)
    except Exception as e:  # noqa: BLE001
        print(f"[bhaga.notify] Slack send failed (swallowed): {e}", file=sys.stderr)
        return None


def _host_tag() -> str:
    """Short hostname tag so DMs are identifiable when multiple Macs run BHAGA."""
    h = socket.gethostname().split(".")[0]
    return f"`{h}`"


def success_heartbeat(
    *,
    date: str,
    tabs_written: int,
    runtime_s: float,
    extra: Optional[str] = None,
) -> Optional[dict]:
    """Post a one-line success message after a clean daily refresh."""
    extra_line = f"\n{extra}" if extra else ""
    text = (
        f"✅ BHAGA daily refresh OK for *{date}* "
        f"({tabs_written} tabs, {runtime_s:.1f}s) on {_host_tag()}{extra_line}"
    )
    return _safe_send(text)


def failure_alert(
    *,
    step: str,
    exception: BaseException,
    date: Optional[str] = None,
    extra: Optional[str] = None,
) -> Optional[dict]:
    """Post a multi-line failure DM with step name, exception, and short traceback.

    The traceback is truncated to the last ~12 frames to keep DMs readable.
    """
    tb = "".join(_tb.format_exception(type(exception), exception, exception.__traceback__))
    tb_lines = tb.strip().split("\n")
    # Slack DMs render best at <4kb. Keep the last 40 lines of traceback.
    if len(tb_lines) > 40:
        tb_lines = ["... (truncated) ..."] + tb_lines[-40:]
    tb_short = "\n".join(tb_lines)

    date_str = f" for *{date}*" if date else ""
    extra_str = f"\n*Note:* {extra}" if extra else ""

    text = (
        f"🚨 BHAGA daily refresh FAILED{date_str} on {_host_tag()}\n"
        f"*Step:* `{step}`\n"
        f"*Error:* `{type(exception).__name__}: {exception}`{extra_str}\n"
        f"```\n{tb_short}\n```"
    )
    # Slack hard-limits text at 40k; defensive truncation.
    if len(text) > 38000:
        text = text[:37900] + "\n...(truncated)\n```"
    return _safe_send(text)


def info_ping(text: str) -> Optional[dict]:
    """Generic info DM (e.g. 'starting refresh', 'OTP requested', etc)."""
    return _safe_send(f"ℹ️ BHAGA: {text} on {_host_tag()}")


def new_employee_alert(
    new_pairs: list[tuple[str, str]],
    *,
    profile_path: str = "agents/bhaga/knowledge-base/store-profiles/palmetto.json",
) -> Optional[dict]:
    """DM the operator whenever an ADP scrape introduces a never-before-seen
    employee. The aliases have already been auto-added to the profile JSON
    using the "one-token-then-comma" rule; this message is the human
    confirmation step. Operator should eyeball each derived canonical and
    correct any compound last names (e.g. "Van Der Berg") via a quick edit.

    Args:
        new_pairs: list of (raw_name_as_seen_in_xlsx, derived_canonical)
        profile_path: shown in message so the operator knows where to edit
    """
    if not new_pairs:
        return None
    lines = [f"• `{raw}` → `{canon}`" for raw, canon in new_pairs]
    body = "\n".join(lines)
    text = (
        f"👋 BHAGA detected *{len(new_pairs)} new employee(s)* in today's ADP scrape on {_host_tag()}.\n"
        f"Auto-added to `{profile_path}` (both raw + canonical forms).\n\n"
        f"*New aliases:*\n{body}\n\n"
        f"_If any canonical above is wrong (e.g. compound last name), edit the profile JSON. "
        f"Don't forget to also add the employee to `excluded_from_tip_pool_and_labor_pct` if they're a manager, "
        f"or to `training_excluded:<name>` rows in the model sheet's `config` tab if they're in training._"
    )
    return _safe_send(text)


def review_anomaly_alert(
    anomalies: list[str],
    *,
    max_shown: int = 25,
) -> Optional[dict]:
    """DM the operator when process_reviews flags suspicious reviews.

    Anomalies include:
      - unparseable review posts (regex didn't match the expected template)
      - 5-star reviews with no shift assigned (no punch covers the post time)
      - low-rating (<= 3-star) reviews (so the operator can read the comment)
      - excluded employees being named (handled silently elsewhere, listed here)

    Args:
        anomalies: short human-readable strings, one per anomaly.
        max_shown: cap how many are inlined in the message so Slack doesn't
            truncate. Operator can pull the full list from the
            `unparseable` tab or grep the review_logs/ directory.
    """
    if not anomalies:
        return None
    inline = "\n".join(f"  • {a}" for a in anomalies[:max_shown])
    overflow = ""
    if len(anomalies) > max_shown:
        overflow = f"\n_…and {len(anomalies) - max_shown} more (see `BHAGA Review Raw > unparseable` tab)._"
    body = (
        f"🔍 BHAGA review anomalies ({len(anomalies)}) on {_host_tag()}:\n"
        f"{inline}{overflow}"
    )
    return _safe_send(body)


# ── Smoke test ────────────────────────────────────────────────────


def _smoke_test() -> int:
    """python3 -m agents.bhaga.notify --smoke

    Sends one of each message type to the BHAGA DM so you can eyeball
    formatting in Slack."""
    print("Sending heartbeat ...")
    success_heartbeat(date="2026-05-15", tabs_written=5, runtime_s=42.3,
                      extra="(smoke test from notify.py)")
    print("Sending info_ping ...")
    info_ping("smoke test — info_ping channel works")
    print("Sending failure_alert ...")
    try:
        raise ValueError("smoke test — this is a fake error, ignore")
    except Exception as e:  # noqa: BLE001
        failure_alert(step="smoke_test", exception=e, date="2026-05-15",
                      extra="If you see this in Slack, the failure alert path is wired.")
    print("Done. Check the BHAGA DM in Slack.")
    return 0


if __name__ == "__main__":
    import argparse
    cli = argparse.ArgumentParser()
    cli.add_argument("--smoke", action="store_true",
                     help="Send heartbeat + info + failure smoke messages to BHAGA DM.")
    args = cli.parse_args()
    if args.smoke:
        raise SystemExit(_smoke_test())
    cli.print_help()
