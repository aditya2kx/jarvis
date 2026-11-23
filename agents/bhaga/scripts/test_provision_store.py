"""provision_store: a Slack store gets per-store schedulers for its chat automations."""

from __future__ import annotations

import json
from unittest import mock

from agents.bhaga.scripts import provision_store as ps


def test_run_body_runs_the_module_with_the_store_env():
    profile = json.loads((ps.PROFILES / "houston.json").read_text())
    c = ps.run_request_body(profile, "agents.bhaga.scripts.team_pulse")["overrides"]["containerOverrides"][0]
    assert c["args"] == ["-m", "agents.bhaga.scripts.team_pulse", "--store", "houston"]
    assert {"name": "BHAGA_BQ_DATASET", "value": "bhaga_houston"} in c["env"]


def test_chat_only_creates_both_houston_automation_schedulers():
    calls = []
    with mock.patch.object(ps, "ensure_scheduler", lambda profile, **kw: calls.append(kw)):
        assert ps.main(["--store", "houston", "--chat-only", "--dry-run"]) == 0
    assert [(c["name"], c["module"], c["schedule"]) for c in calls] == [
        ("bhaga-team-pulse-houston", "agents.bhaga.scripts.team_pulse", "0 8 * * *"),
        ("bhaga-unavailability-reminder-houston", "agents.bhaga.scripts.unavailability_reminder", "5 8-20 * * *"),
    ]
