#!/usr/bin/env python3
"""Idempotently ensure the Slack listener AND inbox processor are running.

This is the canonical "I'm stepping away — keep listening" command. Runs
the two daemons that together turn Slack DMs into actionable input for the
AI agent the next time it wakes up:

  1. Socket Mode listener  (skills/slack/listener.py)
       - WebSocket push from Slack → /tmp/jarvis-slack-inbox.json
       - Required: SLACK_APP_TOKEN in Keychain (xapp-...)

  2. Inbox processor       (skills/slack/inbox_processor.py)
       - Polls inbox every N seconds → acknowledges on Slack →
         writes to /tmp/jarvis-pending-actions.json for the AI to consume
       - Default 8 hours, 30s interval (override via flags)

The function is idempotent: if either daemon is already up (PID file exists
and process is alive), it is left alone. If a PID file is stale (process is
dead), it is cleaned up and the daemon is restarted.

Usage:
    python skills/slack/ensure_listening.py            # 8h, 30s polls
    python skills/slack/ensure_listening.py --hours 12 # 12h
    python skills/slack/ensure_listening.py --status   # report only, do not start
"""

import argparse
import os
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
LISTENER_SCRIPT = PROJECT_ROOT / "skills" / "slack" / "listener.py"
PROCESSOR_SCRIPT = PROJECT_ROOT / "skills" / "slack" / "inbox_processor.py"

# Default (legacy / CHITRA) listener pid + log
LISTENER_PID = pathlib.Path("/tmp/jarvis-listener.pid")
LISTENER_LOG = pathlib.Path("/tmp/jarvis-listener.log")

PROCESSOR_PID = pathlib.Path("/tmp/jarvis-inbox-processor.pid")
PROCESSOR_LOG = pathlib.Path("/tmp/jarvis-inbox-processor.log")


def _agent_listener_pid(agent):
    return pathlib.Path(f"/tmp/jarvis-listener-{agent}.pid")


def _agent_listener_log(agent):
    return pathlib.Path(f"/tmp/jarvis-listener-{agent}.log")


def _registered_agents_with_real_identity():
    """Return list of agent names from config.yaml that have identity_mode=real.

    These are the agents that own a separate Slack app and need their own
    Socket Mode listener process (one listener per app, since Socket Mode
    tokens are app-scoped). Agents in 'transitional' mode share the default
    CHITRA listener via the default token.
    """
    sys.path.insert(0, str(PROJECT_ROOT))
    from core.config_loader import load_config
    cfg = load_config()
    agents = cfg.get("slack", {}).get("agents", {}) or {}
    return [
        name for name, entry in agents.items()
        if entry.get("identity_mode") == "real"
    ]


def _is_alive(pid):
    """Return True iff the given pid is a running process."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_pid(pid_file):
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None


def _check(pid_file, name):
    """Return ('alive', pid) | ('stale', pid) | ('absent', None)."""
    pid = _read_pid(pid_file)
    if pid is None:
        return ("absent", None)
    if _is_alive(pid):
        return ("alive", pid)
    return ("stale", pid)


def _start_listener(agent=None):
    """Spawn a listener as a detached background process. agent=None is the
    legacy default listener (CHITRA's app token); pass agent='bhaga' etc. to
    spawn a per-agent listener."""
    pid_file = _agent_listener_pid(agent) if agent else LISTENER_PID
    log_file = _agent_listener_log(agent) if agent else LISTENER_LOG
    pid_file.unlink(missing_ok=True)
    log = open(log_file, "ab", buffering=0)
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    cmd = [sys.executable, "-u", str(LISTENER_SCRIPT)]
    if agent:
        cmd.extend(["--agent", agent])
    proc = subprocess.Popen(
        cmd,
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    pid_file.write_text(str(proc.pid))
    return proc.pid


def _start_processor(hours, interval):
    """Spawn the inbox processor as a detached background process."""
    PROCESSOR_PID.unlink(missing_ok=True)
    log = open(PROCESSOR_LOG, "ab", buffering=0)
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [
            sys.executable, "-u",
            str(PROCESSOR_SCRIPT),
            "--hours", str(hours),
            "--interval", str(interval),
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    return proc.pid


def status():
    """Print a one-liner status for every Slack daemon (default listener +
    each agent listener + processor)."""
    states = {}

    listener_state, listener_pid = _check(LISTENER_PID, "listener")
    print(f"listener:default  {listener_state:7s} pid={listener_pid}  log={LISTENER_LOG}")
    states["listener:default"] = listener_state

    for agent in _registered_agents_with_real_identity():
        pid_file = _agent_listener_pid(agent)
        log_file = _agent_listener_log(agent)
        s, p = _check(pid_file, f"listener:{agent}")
        print(f"listener:{agent:9s} {s:7s} pid={p}  log={log_file}")
        states[f"listener:{agent}"] = s

    processor_state, processor_pid = _check(PROCESSOR_PID, "processor")
    print(f"processor          {processor_state:7s} pid={processor_pid}  log={PROCESSOR_LOG}")
    states["processor"] = processor_state

    return states


def ensure(hours=8, interval=30):
    """Start whichever daemons are not currently alive. Returns dict of actions taken.

    Starts:
      - The legacy default listener (CHITRA's SLACK_APP_TOKEN)
      - One listener per agent in config.yaml whose identity_mode == "real"
      - The shared inbox processor (handles every per-agent inbox file)
    """
    actions = {}

    # Default / legacy CHITRA listener
    listener_state, listener_pid = _check(LISTENER_PID, "listener")
    if listener_state == "alive":
        actions["listener:default"] = f"already running (pid {listener_pid})"
    else:
        if listener_state == "stale":
            print(f"[ensure] cleaning up stale default listener pid file (pid {listener_pid} dead)")
        new_pid = _start_listener()
        time.sleep(0.5)
        actions["listener:default"] = f"started (pid {new_pid})"

    # Per-agent listeners — one per real-identity agent in config
    for agent in _registered_agents_with_real_identity():
        pid_file = _agent_listener_pid(agent)
        agent_state, agent_pid = _check(pid_file, f"listener:{agent}")
        if agent_state == "alive":
            actions[f"listener:{agent}"] = f"already running (pid {agent_pid})"
            continue
        if agent_state == "stale":
            print(f"[ensure] cleaning up stale {agent} listener pid file (pid {agent_pid} dead)")
        new_pid = _start_listener(agent=agent)
        time.sleep(0.5)
        actions[f"listener:{agent}"] = f"started (pid {new_pid})"

    # Shared inbox processor (scans every /tmp/jarvis-slack-inbox*.json)
    processor_state, processor_pid = _check(PROCESSOR_PID, "processor")
    if processor_state == "alive":
        actions["processor"] = f"already running (pid {processor_pid})"
    else:
        if processor_state == "stale":
            print(f"[ensure] cleaning up stale processor pid file (pid {processor_pid} dead)")
        new_pid = _start_processor(hours, interval)
        time.sleep(0.5)
        actions["processor"] = f"started (pid {new_pid})"

    return actions


def main():
    parser = argparse.ArgumentParser(description="Ensure Slack listener + inbox processor are running")
    parser.add_argument("--hours", type=float, default=8, help="Processor runtime in hours (default 8)")
    parser.add_argument("--interval", type=int, default=30, help="Processor poll interval in seconds (default 30)")
    parser.add_argument("--status", action="store_true", help="Report only, do not start anything")
    args = parser.parse_args()

    if args.status:
        status()
        return

    print(f"[ensure] target: listener + processor ({args.hours}h, {args.interval}s polls)")
    actions = ensure(hours=args.hours, interval=args.interval)
    for name, what in actions.items():
        print(f"[ensure] {name:9s} {what}")

    print()
    print("[ensure] verification:")
    status()


if __name__ == "__main__":
    main()
