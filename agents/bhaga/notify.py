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

from skills.slack.adapter import open_dm, send_message, _api_call  # noqa: E402

AGENT_NAME = "bhaga"
_OPERATOR_USER_ID = os.environ.get("BHAGA_OPERATOR_USER_ID",
                                   os.environ.get("BHAGA_SLACK_USER_ID", "U0APJRE5DC4"))
SLACK_DISABLED_ENV = "BHAGA_SLACK_DISABLED"
_dm_channel_cache: str | None = None


def _find_first_im_channel() -> str:
    """Last-resort: list the bot's IM conversations and return the first one.

    Works when conversations.open fails (e.g. user_not_found because the
    bot is installed in a different workspace than the target user).
    """
    result = _api_call("conversations.list",
                       params={"types": "im", "limit": "50"},
                       agent=AGENT_NAME)
    if not result.get("ok"):
        raise RuntimeError(f"conversations.list failed: {result.get('error', 'unknown')}")
    channels = result.get("channels", [])
    if not channels:
        raise RuntimeError("No IM channels found for the BHAGA bot")
    return channels[0]["id"]


def _resolve_dm_channel() -> str:
    """Resolve the DM channel for BHAGA → operator.

    Order:
      1. BHAGA_DM_CHANNEL env var (explicit override — required on Cloud Run
         where the bot token belongs to BHAGA-Cloud, not the local BHAGA app)
      2. Local config.yaml → slack.agents.bhaga.dm_channel
      3. Auto-discover via conversations.open (works when bot has im:write scope
         and the user ID is visible in the bot's workspace)
      4. conversations.list(types=im) — pick the bot's first DM
    """
    global _dm_channel_cache
    if _dm_channel_cache is not None:
        return _dm_channel_cache

    from_env = os.environ.get("BHAGA_DM_CHANNEL")
    if from_env:
        _dm_channel_cache = from_env
        return _dm_channel_cache

    try:
        from core.config_loader import load_config
        cfg = load_config()
        cfg_channel = (cfg.get("slack", {}).get("agents", {})
                       .get(AGENT_NAME, {}).get("dm_channel", ""))
        if cfg_channel:
            _dm_channel_cache = cfg_channel
            return _dm_channel_cache
    except Exception:
        pass

    try:
        _dm_channel_cache = open_dm(_OPERATOR_USER_ID, agent=AGENT_NAME)
        return _dm_channel_cache
    except Exception as exc:
        print(
            f"[bhaga.notify] conversations.open failed for {_OPERATOR_USER_ID}: {exc}, "
            f"falling back to conversations.list",
            file=sys.stderr,
        )

    _dm_channel_cache = _find_first_im_channel()
    return _dm_channel_cache


def _silenced() -> bool:
    return bool(os.environ.get(SLACK_DISABLED_ENV))


def _run_prefix() -> str:
    """Sandbox/PR label prepended to EVERY DM from a sandbox live run.

    So the operator never mistakes a sandbox OTP prompt (or any sandbox message)
    for prod — even if a prod run is messaging at the same time. Empty for prod
    runs, so prod DMs are byte-for-byte unchanged (backward compatible).
    Driven by BHAGA_RUN_ENV=sandbox + BHAGA_RUN_LABEL (set by sandbox_live_run).
    """
    if os.environ.get("BHAGA_RUN_ENV", "prod").lower() != "sandbox":
        return ""
    label = os.environ.get("BHAGA_RUN_LABEL", "").strip()
    suffix = f" · {label}" if label else ""
    return f":test_tube: *[SANDBOX{suffix}]* "


def _safe_send(text: str) -> Optional[dict]:
    """Send to BHAGA's DM with full error swallowing.

    NEVER raise from a notification helper; the orchestrator must always
    surface the underlying scrape failure, not a Slack-API failure.

    Fallback chain:
      1. Resolved DM channel (env var / config / conversations.open / hardcoded)
      2. Direct post to operator user ID (chat.postMessage auto-opens a DM
         and only needs chat:write scope — works even when conversations.open
         fails due to missing im:write or users:read scopes)
    """
    text = _run_prefix() + text
    if _silenced():
        print(f"[BHAGA_SLACK_DISABLED] would send: {text[:200]}")
        return None
    try:
        return send_message(_resolve_dm_channel(), text, agent=AGENT_NAME)
    except Exception as e1:  # noqa: BLE001
        print(f"[bhaga.notify] primary send failed: {e1}", file=sys.stderr)
        try:
            return send_message(_OPERATOR_USER_ID, text, agent=AGENT_NAME)
        except Exception as e2:  # noqa: BLE001
            print(
                f"[bhaga.notify] fallback send to user {_OPERATOR_USER_ID} also failed "
                f"(swallowed): {e2}",
                file=sys.stderr,
            )
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
    evidence_uri: Optional[str] = None,
) -> Optional[dict]:
    """Post a multi-line failure DM with step name, exception, and short traceback.

    The traceback is truncated to the last ~12 frames to keep DMs readable.
    ``evidence_uri`` (the durable ``gs://<cache>/<date>/evidence/`` prefix) is
    rendered as a clickable postmortem anchor so the operator/agent can pull the
    screenshot + DOM + meta straight from GCS without a rerun.
    """
    tb = "".join(_tb.format_exception(type(exception), exception, exception.__traceback__))
    tb_lines = tb.strip().split("\n")
    # Slack DMs render best at <4kb. Keep the last 40 lines of traceback.
    if len(tb_lines) > 40:
        tb_lines = ["... (truncated) ..."] + tb_lines[-40:]
    tb_short = "\n".join(tb_lines)

    date_str = f" for *{date}*" if date else ""
    extra_str = f"\n*Note:* {extra}" if extra else ""
    evidence_str = f"\n*Evidence:* `{evidence_uri}`" if evidence_uri else ""

    text = (
        f"🚨 BHAGA daily refresh FAILED{date_str} on {_host_tag()}\n"
        f"*Step:* `{step}`\n"
        f"*Error:* `{type(exception).__name__}: {exception}`{evidence_str}{extra_str}\n"
        f"```\n{tb_short}\n```"
    )
    # Slack hard-limits text at 40k; defensive truncation.
    if len(text) > 38000:
        text = text[:37900] + "\n...(truncated)\n```"
    return _safe_send(text)


def info_ping(text: str) -> Optional[dict]:
    """Generic info DM (e.g. 'starting refresh', 'OTP requested', etc)."""
    return _safe_send(f"ℹ️ BHAGA: {text} on {_host_tag()}")


def ready_request(*, date: str, portals: list) -> Optional[dict]:
    """Ask the operator if they're available to grab their phone for OTP(s).

    Step one of the two-step OTP handshake. We post this INSTEAD of triggering
    an OTP immediately (codes expire in minutes), checkpoint a pending state,
    and exit cleanly. The operator replies READY whenever they can; the run
    then resumes and triggers fresh code(s).

    ONE READY covers ALL OTP portals this run will need.
    """
    portal_list = list(portals)
    if len(portal_list) == 1:
        portal_str = portal_list[0]
    elif len(portal_list) == 2:
        portal_str = f"{portal_list[0]} and {portal_list[1]}"
    else:
        portal_str = ", ".join(portal_list[:-1]) + f", and {portal_list[-1]}"
    text = (
        f":wave: *BHAGA needs an OTP for {portal_str}* (refresh *{date}*) on {_host_tag()}\n"
        f"Reply *READY* (or `ok` / `go` / `yes`) when you can grab your phone — "
        f"I'll then send a fresh code for each and ask you to paste it.\n"
        f"_No rush: I'll wait up to 48h and you can reply anytime. "
        f"I'm not holding anything open in the meantime._"
    )
    return _safe_send(text)


def otp_skipped_alert(*, date: str, portals: list) -> Optional[dict]:
    """Alert that the OTP-gated step(s) were skipped after the 48h cap.

    Everything that does NOT need an OTP still ran; the next nightly refresh
    will retry the skipped portal(s).
    """
    portal_str = ", ".join(portals) if portals else "OTP step(s)"
    text = (
        f":hourglass: *BHAGA skipped {portal_str}* for refresh *{date}* on {_host_tag()}\n"
        f"No READY reply arrived within 48h, so I finished everything that "
        f"didn't need an OTP and parked {portal_str}. The next nightly run "
        f"will retry — or reply `retry` to run it now."
    )
    return _safe_send(text)


def square_device_blocked_alert(
    *,
    date: str,
    evidence_uri: Optional[str] = None,
) -> Optional[dict]:
    """Alert that Square anti-bot soft-blocked the headless device.

    Square fingerprinted the cloud container as an unrecognized device and served
    an undeliverable, blank-recipient "magic link" (no email is sent), and no SMS
    option was offered — so there is *nothing actionable for the operator to paste*.
    BHAGA already retried once in a fresh browser context. This DM tells the
    operator the truth and the recovery: the next nightly auto-retries (a different
    egress IP often clears the block), or they can re-run now — but NOT to look for
    a magic-link email (there isn't one).
    """
    evidence_str = f"\n*Evidence:* `{evidence_uri}`" if evidence_uri else ""
    text = (
        f":no_entry: *BHAGA: Square blocked the login* for refresh *{date}* on {_host_tag()}\n"
        f"Square's anti-bot flagged the cloud browser as an unrecognized device and "
        f"served an *undeliverable magic link* (blank recipient — no email is sent), "
        f"with no SMS option. I already retried once with a fresh session and it was "
        f"blocked again.{evidence_str}\n"
        f"*There is nothing to paste — don't look for a magic-link email.* "
        f"ADP and reviews still ran; only Square sales/tips/items are missing for this date.\n"
        f"_The next nightly will auto-retry (a fresh egress IP usually clears it). "
        f"To try sooner, reply `retry` or re-run the job for this date._"
    )
    return _safe_send(text)


def scrape_concurrency_alert(
    *,
    date: str,
    portal: str,
    held_by: str,
    lock_name: str,
    expires_at: str,
) -> Optional[dict]:
    """Alert that a scrape was refused because another execution holds the distributed lock.

    This is the correct alert for concurrent-execution failures — NOT the generic
    failure_alert (which implies the operator should chase a magic-link email) and
    NOT the device-blocked alert. This tells the operator: a second run tried to
    scrape while the first was still in flight; it was correctly refused; no duplicate
    SMS was fired; the data will land on the next run or when the first completes.
    """
    text = (
        f":no_entry_sign: *BHAGA: {portal} scrape skipped — concurrent execution* "
        f"for refresh *{date}* on {_host_tag()}\n"
        f"Another execution already holds the scrape lock (`{lock_name}`, "
        f"held by `{held_by}`, expires {expires_at}).\n"
        f"*No duplicate SMS was sent.* The scrape will run when the first execution "
        f"finishes (or the lock expires). If the first execution is stuck, clear the "
        f"lock: `python3 -c \"from skills.bhaga_config.state_adapter import release_lock; "
        f"release_lock('{lock_name}', holder='{held_by}')\"` and re-trigger."
    )
    return _safe_send(text)


def new_employee_alert(
    new_pairs: list[tuple[str, str]],
    *,
    profile_path: str = "bhaga_model > employees (sheet)",
) -> Optional[dict]:
    """DM the operator whenever an ADP scrape introduces a never-before-seen
    employee. The aliases have already been auto-added to bhaga_model >
    employees (canonical SOT) using the "one-token-then-comma" rule. This
    message is the human confirmation step. Operator should eyeball each
    derived canonical and correct any compound last names (e.g. "Van Der
    Berg") via a quick edit to the sheet.

    Args:
        new_pairs: list of (raw_name_as_seen_in_xlsx, derived_canonical)
        profile_path: shown in message so the operator knows where to edit
            (defaults to the sheet location; legacy callers may pass a path)
    """
    if not new_pairs:
        return None
    lines = [f"• `{raw}` → `{canon}`" for raw, canon in new_pairs]
    body = "\n".join(lines)
    text = (
        f"👋 BHAGA detected *{len(new_pairs)} new employee(s)* in today's ADP scrape on {_host_tag()}.\n"
        f"Auto-added to `{profile_path}` (both raw + canonical forms).\n\n"
        f"*New aliases:*\n{body}\n\n"
        f"_If any canonical above is wrong (e.g. compound last name), edit the row directly in "
        f"the `bhaga_model > employees` tab. Also add the employee to `excluded_from_tip_pool` "
        f"in `bhaga_model > config` if they're a manager, or add a `training_excluded:<name>` "
        f"row in the config tab if they're in training._"
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
