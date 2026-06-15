#!/usr/bin/env python3
"""BHAGA Slack DM command handler.

Parses operator commands ("retry", "refresh <date>", "status") received via
DM and triggers the appropriate recovery flow.  Called by the Socket Mode
listener (listener.py) and by the polling fallback (poll_commands.py).

Command vs OTP disambiguation: commands only activate when no OTP request
is pending (/tmp/jarvis-otp/ has no pending files). During an active OTP
wait, all DMs are treated as OTP codes by the listener.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
from typing import Optional
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

CT = ZoneInfo("America/Chicago")
STATE_DIR = pathlib.Path.home() / ".bhaga" / "state"
MARKER_FILE = STATE_DIR / "last_run_ct_date.txt"
OTP_DIR = pathlib.Path("/tmp/jarvis-otp")

EXPECTED_STEPS = {
    "square_transactions",
    "consolidate_csv",
    "adp_reports",
    "write_raw_sheets",
    "update_model_sheet",
    "process_reviews",
}

AGENT_NAME = "bhaga"
DM_CHANNEL = "D0ATWHSA14J"

_RETRY_RE = re.compile(r"^retry(?:\s+now)?$", re.IGNORECASE)
_REFRESH_RE = re.compile(r"^refresh\s+(\d{4}-\d{2}-\d{2})$", re.IGNORECASE)
_STATUS_RE = re.compile(r"^status$", re.IGNORECASE)


def has_pending_otp() -> bool:
    """Return True if any OTP request is currently pending."""
    if not OTP_DIR.exists():
        return False
    for f in OTP_DIR.iterdir():
        if f.suffix == ".json":
            try:
                data = json.loads(f.read_text())
                if data.get("status") == "pending":
                    return True
            except (json.JSONDecodeError, OSError, KeyError):
                pass
    return False


def is_command(text: str) -> bool:
    """Return True if the text looks like a BHAGA command (not an OTP code)."""
    t = text.strip()
    return bool(_RETRY_RE.match(t) or _REFRESH_RE.match(t) or _STATUS_RE.match(t))


def _send_dm(text: str) -> None:
    """Send a DM to the operator via BHAGA's bot."""
    try:
        from skills.slack.adapter import send_message
        send_message(DM_CHANNEL, text, agent=AGENT_NAME)
    except Exception as exc:
        print(f"[command_handler] DM send failed: {exc}", file=sys.stderr)


def _get_latest_failed_date() -> Optional[datetime.date]:
    """Find the most recent failed refresh date.

    Strategy:
      1. Read the wrapper marker file — it explicitly records status.
      2. If that's 'failed', use the date on line 1.
      3. Otherwise scan run-* dirs for incomplete step markers.
    """
    if MARKER_FILE.exists():
        try:
            body = MARKER_FILE.read_text()
            lines = body.strip().splitlines()
            date_str = lines[0].strip()
            status_line = next(
                (ln for ln in lines if ln.lower().startswith("status:")), ""
            )
            if "failed" in status_line.lower():
                return datetime.date.fromisoformat(date_str)
        except (ValueError, IndexError):
            pass

    # Fallback: scan run-* dirs for missing step markers (newest first)
    if not STATE_DIR.is_dir():
        return None
    run_dirs = sorted(
        (d for d in STATE_DIR.iterdir() if d.is_dir() and d.name.startswith("run-")),
        key=lambda d: d.name,
        reverse=True,
    )
    for d in run_dirs:
        try:
            date_str = d.name[len("run-"):]
            completed = {p.stem for p in d.glob("*.done")}
            if not EXPECTED_STEPS.issubset(completed):
                return datetime.date.fromisoformat(date_str)
        except ValueError:
            continue
    return None


def _get_run_status(refresh_date: datetime.date) -> dict:
    """Return a status dict for a given run date."""
    run_dir = STATE_DIR / f"run-{refresh_date.isoformat()}"
    completed = set()
    if run_dir.is_dir():
        completed = {p.stem for p in run_dir.glob("*.done")}
    expected_done = completed & EXPECTED_STEPS
    missing = EXPECTED_STEPS - completed
    return {
        "date": refresh_date.isoformat(),
        "completed": sorted(completed),
        "expected_done": len(expected_done),
        "expected_total": len(EXPECTED_STEPS),
        "missing": sorted(missing),
        "all_done": len(missing) == 0,
    }


def _read_data_window_end() -> Optional[str]:
    """Read data_window_end from BQ (store_config, then MAX square_transactions).

    Returns the ISO date string, or None on failure.
    """
    try:
        from core.store_config import get_config
        val = (get_config("palmetto", "data_window_end") or "").strip()
        if val:
            return val
        # Fall back to MAX(square_transactions.date_local).
        from core.datastore import read_query, fq
        rows = read_query(f"SELECT CAST(MAX(date_local) AS STRING) AS m FROM {fq('square_transactions')}")
        return (rows[0]["m"] if rows else None) or None
    except Exception as exc:
        print(f"[command_handler] _read_data_window_end failed: {exc}")
    return None


def handle_status() -> str:
    """Build a status response message."""
    import urllib.parse  # noqa: F811

    dwe = _read_data_window_end()
    dwe_str = dwe if dwe else "(unknown)"

    # Last run marker
    marker_status = "(no marker)"
    marker_date = "(unknown)"
    if MARKER_FILE.exists():
        try:
            body = MARKER_FILE.read_text().strip()
            lines = body.splitlines()
            marker_date = lines[0].strip()
            status_line = next(
                (ln for ln in lines if ln.lower().startswith("status:")), ""
            )
            marker_status = status_line.split(":", 1)[1].strip() if status_line else "(unknown)"
        except (IndexError, ValueError):
            pass

    # Check last few run dirs
    run_summaries = []
    if STATE_DIR.is_dir():
        run_dirs = sorted(
            (d for d in STATE_DIR.iterdir() if d.is_dir() and d.name.startswith("run-")),
            key=lambda d: d.name,
            reverse=True,
        )[:3]
        for d in run_dirs:
            try:
                date_str = d.name[len("run-"):]
                info = _get_run_status(datetime.date.fromisoformat(date_str))
                status_emoji = ":white_check_mark:" if info["all_done"] else ":warning:"
                missing_str = f" missing: {', '.join(info['missing'])}" if info["missing"] else ""
                run_summaries.append(f"  {status_emoji} `{date_str}`: {info['expected_done']}/{info['expected_total']} steps{missing_str}")
            except ValueError:
                continue

    runs_block = "\n".join(run_summaries) if run_summaries else "  (none found)"

    return (
        f":bar_chart: *BHAGA Status*\n\n"
        f"*data_window_end:* `{dwe_str}`\n"
        f"*Last run marker:* `{marker_date}` — {marker_status}\n\n"
        f"*Recent runs:*\n{runs_block}"
    )


def _trigger_recovery(refresh_date: datetime.date) -> None:
    """Run recovery in a background thread: clean state, fork daily_refresh, report result."""

    def _worker():
        date_iso = refresh_date.isoformat()
        try:
            # 1. DM operator
            _send_dm(
                f":arrows_counterclockwise: Starting recovery for *{date_iso}*. "
                f"Will request OTPs shortly — please reply when they arrive."
            )

            # 2. Clean partial run state
            run_dir = STATE_DIR / f"run-{date_iso}"
            if run_dir.is_dir():
                for f in run_dir.iterdir():
                    f.unlink(missing_ok=True)
                run_dir.rmdir()
                print(f"[command_handler] Cleaned partial state: {run_dir}")

            # 3. Remove stale Square lock
            lock_file = pathlib.Path("/tmp/bhaga-square-scrape.lock")
            if lock_file.exists():
                lock_file.unlink(missing_ok=True)
                print("[command_handler] Removed stale Square lock")

            # 4. Fork daily_refresh subprocess
            cmd = [
                "/opt/miniconda3/bin/python3",
                "-m", "agents.bhaga.scripts.daily_refresh",
                "--store", "palmetto",
                "--date", date_iso,
            ]
            child_env = {
                "PATH": os.environ.get("PATH", "/opt/miniconda3/bin:/usr/local/bin:/usr/bin:/bin"),
                "HOME": os.environ.get("HOME", "/Users/adityaparikh"),
                "LANG": os.environ.get("LANG", "en_US.UTF-8"),
                "PYTHONPATH": "",
                "PYTHONUNBUFFERED": "1",
            }
            print(f"[command_handler] Running: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd=_PROJECT_ROOT,
                capture_output=True,
                text=True,
                env=child_env,
            )

            # 5. Report result
            if result.returncode == 0:
                dwe = _read_data_window_end()
                dwe_str = dwe if dwe else "(could not read)"
                _send_dm(
                    f":white_check_mark: Recovery for *{date_iso}* complete. "
                    f"`data_window_end` is now `{dwe_str}`."
                )
                # Update the wrapper marker to reflect success
                try:
                    from agents.bhaga.scripts.daily_refresh_wrapper import write_marker
                    write_marker(refresh_date, status="success", rc=0)
                except Exception:
                    pass
            else:
                stderr_tail = (result.stderr or result.stdout or "")[-1500:]
                _send_dm(
                    f":x: Recovery for *{date_iso}* failed (rc={result.returncode}).\n"
                    f"```\n{stderr_tail}\n```"
                )

        except Exception as exc:
            _send_dm(
                f":x: Recovery for *{date_iso}* crashed: "
                f"`{type(exc).__name__}: {exc}`"
            )

    thread = threading.Thread(target=_worker, daemon=True, name=f"bhaga-recovery-{refresh_date}")
    thread.start()
    return thread


def find_pending_otp_date() -> Optional[datetime.date]:
    """Newest refresh_date with an outstanding (not-yet-READY) OTP checkpoint.

    Scans the local run-* state dirs for a ``pending_otp.json`` whose
    ``ready_received`` is False. Used by the non-cloud resumer: when the
    laptop is closed the daily run posts a READY request + this checkpoint and
    exits; the operator replies READY from their phone; the next poll wakeup
    detects the reply and resumes.
    """
    if not STATE_DIR.is_dir():
        return None
    run_dirs = sorted(
        (d for d in STATE_DIR.iterdir() if d.is_dir() and d.name.startswith("run-")),
        key=lambda d: d.name,
        reverse=True,
    )
    for d in run_dirs:
        pending_file = d / "pending_otp.json"
        if not pending_file.exists():
            continue
        try:
            data = json.loads(pending_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if data and not data.get("ready_received"):
            try:
                return datetime.date.fromisoformat(d.name[len("run-"):])
            except ValueError:
                continue
    return None


def _trigger_resume(refresh_date: datetime.date) -> "threading.Thread":
    """Fork daily_refresh for refresh_date WITHOUT wiping run state.

    Unlike _trigger_recovery (used by the `retry`/`refresh` commands), this
    PRESERVES completed-step markers AND the READY-marked pending checkpoint,
    so the resumed run skips already-done work and proceeds straight to the
    OTP portal(s) with a short bounded code wait.
    """

    def _worker():
        date_iso = refresh_date.isoformat()
        try:
            # Remove a stale Square lock if a prior process died mid-scrape;
            # do NOT touch the run-state dir or pending_otp checkpoint.
            lock_file = pathlib.Path("/tmp/bhaga-square-scrape.lock")
            if lock_file.exists():
                lock_file.unlink(missing_ok=True)

            cmd = [
                "/opt/miniconda3/bin/python3",
                "-m", "agents.bhaga.scripts.daily_refresh",
                "--store", "palmetto",
                "--date", date_iso,
            ]
            child_env = {
                "PATH": os.environ.get("PATH", "/opt/miniconda3/bin:/usr/local/bin:/usr/bin:/bin"),
                "HOME": os.environ.get("HOME", "/Users/adityaparikh"),
                "LANG": os.environ.get("LANG", "en_US.UTF-8"),
                "PYTHONPATH": "",
                "PYTHONUNBUFFERED": "1",
            }
            print(f"[command_handler] Resuming: {' '.join(cmd)}")
            result = subprocess.run(
                cmd, cwd=_PROJECT_ROOT, capture_output=True, text=True, env=child_env,
            )
            if result.returncode == 0:
                _send_dm(f":white_check_mark: Resume for *{date_iso}* complete.")
            else:
                stderr_tail = (result.stderr or result.stdout or "")[-1500:]
                _send_dm(
                    f":x: Resume for *{date_iso}* failed (rc={result.returncode}).\n"
                    f"```\n{stderr_tail}\n```"
                )
        except Exception as exc:
            _send_dm(
                f":x: Resume for *{date_iso}* crashed: `{type(exc).__name__}: {exc}`"
            )

    thread = threading.Thread(
        target=_worker, daemon=True, name=f"bhaga-resume-{refresh_date}"
    )
    thread.start()
    return thread


def handle_ready(text: str) -> Optional[str]:
    """If `text` is a READY reply and a run is awaiting availability, mark the
    checkpoint ready and resume it (non-destructively).

    Returns an acknowledgement string when a resume was triggered, else None
    (so the caller can fall through to normal command / OTP handling).
    """
    try:
        from agents.bhaga.scripts.otp_gate import is_ready_reply
    except ImportError:
        return None
    if not is_ready_reply(text):
        return None
    pending_date = find_pending_otp_date()
    if pending_date is None:
        return None
    try:
        from skills.bhaga_config.state_adapter import mark_otp_ready
        mark_otp_ready(pending_date)
    except Exception as exc:  # noqa: BLE001
        print(f"[command_handler] mark_otp_ready failed: {exc}", file=sys.stderr)
    _trigger_resume(pending_date)
    return (
        f":arrow_forward: Got it — resuming *{pending_date.isoformat()}* now. "
        f"I'll send the OTP code request(s) shortly; reply with each code."
    )


def handle_command(text: str, user_id: str) -> Optional[str]:
    """Parse and handle a BHAGA command. Returns response text, or None if not a command.

    If the command triggers an async recovery, returns an acknowledgement
    string immediately; the recovery thread will DM progress separately.
    """
    t = text.strip()

    # --- status ---
    if _STATUS_RE.match(t):
        return handle_status()

    # --- retry / retry now ---
    if _RETRY_RE.match(t):
        failed_date = _get_latest_failed_date()
        if failed_date is None:
            return ":white_check_mark: No failed runs found — nothing to retry."
        _trigger_recovery(failed_date)
        return (
            f":hourglass_flowing_sand: Retry triggered for *{failed_date.isoformat()}*. "
            f"I'll DM you progress and OTP requests."
        )

    # --- refresh YYYY-MM-DD ---
    m = _REFRESH_RE.match(t)
    if m:
        try:
            target_date = datetime.date.fromisoformat(m.group(1))
        except ValueError:
            return f":warning: Invalid date format: `{m.group(1)}`. Use YYYY-MM-DD."
        _trigger_recovery(target_date)
        return (
            f":hourglass_flowing_sand: Refresh triggered for *{target_date.isoformat()}*. "
            f"I'll DM you progress and OTP requests."
        )

    return None
