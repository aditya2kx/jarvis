#!/usr/bin/env python3
"""BHAGA daily refresh wrapper — Central Time gate + idempotency marker.

INVOKED BY: launchd every 15 minutes during 19:00-23:30 LOCAL TIME.
This script is the safety layer between launchd (which only knows the
laptop's local clock) and `daily_refresh.py` (which must fire at 21:00 CT
regardless of where the laptop is).

LOGIC:
    1. Compute now_ct = datetime.now(ZoneInfo("America/Chicago"))
    2. If now_ct.hour != 21: exit 0 (not our window)
    3. Check marker file ~/.bhaga/state/last_run_ct_date.txt:
         - If contents == today_ct.isoformat(): exit 0 (already ran today)
    4. Run daily_refresh.py for refresh_date = today_ct
       (Shop closes 20:00 CT, nightly fires at 21:00 CT, so today is
       a complete business day by the time we run.)
    5. On success: write today_ct.isoformat() to marker file, exit 0
    6. On failure: notify.failure_alert was already called by orchestrator;
       re-raise so launchd records non-zero exit (for stderr log inspection).

The CT-anchoring means:
    - In Austin (CT): laptop local time == CT, fires at 21:00 local
    - In NYC (ET):    laptop local 22:00 == CT 21:00, fires then
    - In SF (PT):     laptop local 19:00 == CT 21:00, fires then
    - In HI (HT):     laptop local 16:00 == CT 21:00 (HT is HST = CT-5)
      -> launchd window 19:00-23:30 local misses this; see HI_NOTE below.

HI_NOTE: If the user travels to Hawaii or further west, this wrapper
won't fire (because CT 21:00 maps to laptop local 16:00 there). For
those edge cases, run the orchestrator manually that day.

LOG FILES:
    ~/.bhaga/state/wrapper.log   - one line per wakeup (gate decisions)
    ~/.bhaga/state/refresh.log   - full stdout/stderr from daily_refresh.py

Usage:
    python3 -m agents.bhaga.scripts.daily_refresh_wrapper
    python3 -m agents.bhaga.scripts.daily_refresh_wrapper --force
    python3 -m agents.bhaga.scripts.daily_refresh_wrapper --simulate-ct 21:15
"""

from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import subprocess
import sys
import traceback
from typing import Optional

# When launched by launchd without WorkingDirectory set, our cwd is /. Move
# off that immediately so any later relative-path operations are well-defined
# AND so subprocess.run inherits a sane cwd. (See plist comment for the
# Conda-Python getcwd-hang bug we're working around.)
os.chdir("/tmp")

from zoneinfo import ZoneInfo  # noqa: E402

CT = ZoneInfo("America/Chicago")
STATE_DIR = pathlib.Path.home() / ".bhaga" / "state"
MARKER_FILE = STATE_DIR / "last_run_ct_date.txt"
WRAPPER_LOG = STATE_DIR / "wrapper.log"
REFRESH_LOG = STATE_DIR / "refresh.log"

# Hour-of-day in Central Time when the refresh should fire.
TARGET_CT_HOUR = 21

# Project root (where agents/ and skills/ live).
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _log(line: str) -> None:
    """Append a timestamped line to the wrapper log."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with WRAPPER_LOG.open("a") as f:
        f.write(f"{ts}Z  {line}\n")
    print(f"{ts}Z  {line}")


def _now_ct(simulate_ct: str | None = None) -> datetime.datetime:
    """Return current time in CT, or a fake CT time for testing.

    simulate_ct format: 'HH:MM' (uses today's CT date)."""
    if simulate_ct is None:
        return datetime.datetime.now(CT)
    today_ct = datetime.datetime.now(CT).date()
    h, m = (int(x) for x in simulate_ct.split(":"))
    return datetime.datetime.combine(
        today_ct, datetime.time(hour=h, minute=m), tzinfo=CT,
    )


def gate(*, force: bool, simulate_ct: str | None) -> tuple[bool, str, datetime.date]:
    """Decide whether to fire the refresh now.

    Returns (should_run, reason, refresh_date_ct).
    """
    now_ct = _now_ct(simulate_ct)
    today_ct = now_ct.date()
    # refresh_date == today_ct: shop closes 20:00 CT, we run at 21:00 CT,
    # so today is a complete business day and incremental contract pulls it.
    refresh_date = today_ct

    if force:
        return True, f"--force flag (now_ct={now_ct.isoformat()})", refresh_date

    if now_ct.hour != TARGET_CT_HOUR:
        return False, (
            f"now_ct hour is {now_ct.hour:02d}, not the target hour "
            f"{TARGET_CT_HOUR:02d}; skipping (laptop local time may differ)."
        ), refresh_date

    if MARKER_FILE.exists():
        # Marker is multi-line: first line is the ISO date, subsequent lines
        # may contain status/attempted_at/rerun hint (see write_marker).
        body = MARKER_FILE.read_text()
        first_line = body.split("\n", 1)[0].strip()
        if first_line == today_ct.isoformat():
            # Surface the marker body's status (if present) so the wrapper
            # log shows whether the prior attempt succeeded or failed.
            status_line = next(
                (ln for ln in body.splitlines() if ln.lower().startswith("status:")),
                "",
            )
            return False, (
                f"marker file shows already ran today_ct={today_ct.isoformat()}"
                f"{' (' + status_line.strip() + ')' if status_line else ''}; skipping."
            ), refresh_date

    return True, f"GO: now_ct={now_ct.isoformat()}, target hour matched, no marker for today.", refresh_date


def write_marker(today_ct: datetime.date, *, status: str = "success",
                 attempted_at: Optional[datetime.datetime] = None,
                 rc: Optional[int] = None) -> None:
    """Write the day marker so subsequent cron wakes gate off.

    Strict-1-attempt: this is called on BOTH success and failure of the
    refresh. The marker body records the outcome so the wrapper log + a
    quick `cat` shows whether intervention is needed.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    when = (attempted_at or datetime.datetime.now(datetime.timezone.utc)).isoformat()
    lines = [today_ct.isoformat(), f"status: {status}", f"attempted_at: {when}"]
    if rc is not None:
        lines.append(f"rc: {rc}")
    if status != "success":
        lines.append(
            "rerun: python3 /Users/adityaparikh/Documents/current-workspace/Jarvis/"
            "agents/bhaga/scripts/daily_refresh_wrapper.py --force"
        )
        lines.append(
            "note: completed steps will be skipped via per-step markers in "
            "~/.bhaga/state/run-<date>/*.done"
        )
    MARKER_FILE.write_text("\n".join(lines) + "\n")


def run_refresh(refresh_date: datetime.date) -> int:
    """Spawn daily_refresh.py as a subprocess.

    Captures stdout+stderr into REFRESH_LOG so we have a record after the
    wrapper returns. Returns the subprocess exit code.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Wipe PYTHONPATH for the orchestrator child so it doesn't inherit a
    # potentially-hung user PYTHONPATH (e.g. /Users/.../ask-data-ai-service)
    # that would hang Python at startup. Use a clean env with just what's
    # needed for the refresh.
    child_env = {
        "PATH": os.environ.get("PATH", "/opt/miniconda3/bin:/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/Users/adityaparikh"),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "PYTHONPATH": "",
        "PYTHONUNBUFFERED": "1",
    }
    cmd = [
        sys.executable, "-m", "agents.bhaga.scripts.daily_refresh",
        "--date", refresh_date.isoformat(),
        "--store", "palmetto",
        # Full refresh: Square + ADP Timecard + ADP Earnings (Mon/Tue only,
        # gated by daily_refresh's --include-rates=auto default) + raw-sheet
        # mirroring + model rebuild + Google review bonuses.
        # No --skip-* flags — the orchestrator's own logic decides what to
        # run based on the day of week and gap detection.
    ]
    _log(f"running: {' '.join(cmd)}")
    with REFRESH_LOG.open("a") as logf:
        header = (
            f"\n{'='*70}\n"
            f"=== daily_refresh.py invoked {datetime.datetime.now(datetime.timezone.utc).isoformat()}Z\n"
            f"=== refresh_date={refresh_date.isoformat()}\n"
            f"{'='*70}\n"
        )
        logf.write(header)
        logf.flush()
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=logf,
            stderr=subprocess.STDOUT,
            env=child_env,
        )
    _log(f"daily_refresh.py exited rc={result.returncode}")
    return result.returncode


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "--force", action="store_true",
        help="Bypass CT-hour and marker checks. For manual reruns.",
    )
    cli.add_argument(
        "--simulate-ct", default=None,
        help="HH:MM in CT to simulate (test the gate without waiting). Does not actually run refresh.",
    )
    cli.add_argument(
        "--dry-gate", action="store_true",
        help="Only evaluate the gate, do not run refresh. Implied with --simulate-ct.",
    )
    args = cli.parse_args()

    try:
        should_run, reason, refresh_date = gate(
            force=args.force, simulate_ct=args.simulate_ct,
        )
        _log(f"GATE: should_run={should_run} reason={reason}")

        if args.simulate_ct or args.dry_gate:
            print(f"\nGate decision: {'GO' if should_run else 'SKIP'}")
            print(f"Reason: {reason}")
            print(f"Refresh date would be: {refresh_date.isoformat()}")
            return 0

        if not should_run:
            return 0

        # Best-effort cleanup of old per-run state dirs (>7 days). Failure
        # here is non-fatal — we just log and continue.
        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from agents.bhaga.scripts.daily_refresh import cleanup_old_run_dirs  # noqa: PLC0415
            cleanup_old_run_dirs(keep_days=7)
        except Exception as exc:  # noqa: BLE001
            _log(f"cleanup_old_run_dirs failed (non-fatal): {exc}")

        rc = run_refresh(refresh_date)
        today_ct = _now_ct().date()
        # STRICT 1-ATTEMPT: write the day marker on ANY exit (success or
        # failure). Subsequent cron wakes this night will see the marker and
        # gate off. Operator can manually re-trigger with --force; per-step
        # markers in ~/.bhaga/state/run-<date>/ ensure already-completed
        # steps are skipped on the rerun (no duplicate OTPs).
        if rc == 0:
            write_marker(today_ct, status="success", rc=rc)
            _log(f"marker written: {today_ct.isoformat()} (success)")
            return 0
        else:
            write_marker(today_ct, status="failed", rc=rc)
            _log(f"refresh failed (rc={rc}); marker written so no auto-retry. "
                 f"Manual rerun: python3 /Users/adityaparikh/Documents/current-workspace/"
                 f"Jarvis/agents/bhaga/scripts/daily_refresh_wrapper.py --force")
            # Wrapper-level summary alert (per-step alerts already fired
            # inside daily_refresh.py). This gives the operator a single
            # roll-up notification that today's run is parked.
            try:
                from agents.bhaga.notify import failure_alert  # noqa: PLC0415
                failure_alert(
                    step="daily_refresh (wrapper summary)",
                    exception=RuntimeError(f"refresh exited rc={rc}"),
                    date=refresh_date.isoformat(),
                    extra=(
                        "Strict 1-attempt: cron will NOT auto-retry tonight. "
                        "Per-step alerts above show which step failed. "
                        "Re-run when ready: python3 .../daily_refresh_wrapper.py --force "
                        "(completed steps will skip via per-step markers)."
                    ),
                )
            except Exception as alert_exc:  # noqa: BLE001
                _log(f"wrapper failure_alert failed (non-fatal): {alert_exc}")
            return rc

    except Exception as exc:  # noqa: BLE001
        _log(f"WRAPPER CRASH: {type(exc).__name__}: {exc}")
        _log(traceback.format_exc())
        # Try to alert via Slack even if everything else failed.
        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from agents.bhaga.notify import failure_alert  # noqa: PLC0415
            failure_alert(
                step="daily_refresh_wrapper",
                exception=exc,
                extra="Wrapper itself crashed (before refresh could run). Check ~/.bhaga/state/wrapper.log.",
            )
        except Exception:  # noqa: BLE001, S110
            pass
        return 99


if __name__ == "__main__":
    raise SystemExit(main())
