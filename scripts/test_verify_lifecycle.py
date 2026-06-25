#!/usr/bin/env python3
"""Tests for verify_lifecycle.py — each assertion uses fixtures so tests pass before M3/M5."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))

import verify_lifecycle as vl


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_lifecycle_module(tmp_dir: Path):
    """Create a minimal lifecycle.py in tmp_dir."""
    lifecycle_src = textwrap.dedent("""\
        from typing import NamedTuple

        class Substep(NamedTuple):
            name: str
            driver: str  # "operator" or "agent"
            exit_criterion: str

        class Stage(NamedTuple):
            name: str
            substeps: list

        STAGES = [
            Stage("align", [
                Substep("specify", "operator", "requirement stated"),
                Substep("setup", "agent", "worktree + brief exist"),
                Substep("jam", "operator", "requirements agreed"),
                Substep("define-evidence", "operator", "evidence approved"),
            ]),
            Stage("plan", [
                Substep("plan", "agent", "check_plan_readiness passes"),
            ]),
            Stage("build", [
                Substep("implement", "agent", "code written"),
                Substep("verify", "agent", "verify.py --full green"),
            ]),
            Stage("ship", [
                Substep("pr-evidence", "agent", "evidence assembled"),
                Substep("babysit", "agent", "CI green"),
                Substep("merge", "operator", "operator squash-merges"),
            ]),
            Stage("verify-learn", [
                Substep("post-merge-verify", "agent", "prod verified"),
                Substep("retrospective", "agent", "retro logged"),
            ]),
        ]

        def all_substeps():
            return [s for stage in STAGES for s in stage.substeps]

        def substep_index(name):
            for i, s in enumerate(all_substeps()):
                if s.name == name:
                    return i
            raise ValueError(f"Unknown substep: {name}")

        def stage_of(substep_name):
            for stage in STAGES:
                for s in stage.substeps:
                    if s.name == substep_name:
                        return stage
            raise ValueError(f"Unknown substep: {substep_name}")

        def overall_pct(done_set):
            total = len(all_substeps())
            return int(len(done_set) / total * 100) if total else 0

        def stage_pct(stage_name, done_set):
            for stage in STAGES:
                if stage.name == stage_name:
                    total = len(stage.substeps)
                    done = sum(1 for s in stage.substeps if s.name in done_set)
                    return int(done / total * 100) if total else 0
            return 0
    """)
    lc_path = tmp_dir / "lifecycle.py"
    lc_path.write_text(lifecycle_src)
    return lc_path


def _make_phase_state_module(tmp_dir: Path):
    """Create a minimal phase_state.py in tmp_dir."""
    ps_src = textwrap.dedent("""\
        #!/usr/bin/env python3
        import argparse, sys

        def main():
            parser = argparse.ArgumentParser()
            sub = parser.add_subparsers(dest="cmd")
            init_p = sub.add_parser("init")
            init_p.add_argument("--branch")
            init_p.add_argument("--dry-run", action="store_true")
            adv_p = sub.add_parser("advance")
            adv_p.add_argument("--branch")
            adv_p.add_argument("--to")
            sub.add_parser("ensure-labels")
            status_p = sub.add_parser("status")
            status_p.add_argument("--branch", default=None)
            sub.add_parser("report")
            args = parser.parse_args()

            if args.cmd == "init":
                if args.dry_run:
                    print("[dry-run] would create gh issue")
                    sys.exit(0)
            elif args.cmd == "advance":
                # Operator substeps require approval
                OPERATOR_SUBSTEPS = {"specify", "jam", "define-evidence", "merge"}
                if args.to in OPERATOR_SUBSTEPS:
                    print(f"ERROR: operator approval required for substep '{args.to}'", file=sys.stderr)
                    sys.exit(1)
            sys.exit(0)

        if __name__ == "__main__":
            main()
    """)
    ps_path = tmp_dir / "phase_state.py"
    ps_path.write_text(ps_src)
    return ps_path


# ---------------------------------------------------------------------------
# Assertion 1 — new_requirement --dry-run
# ---------------------------------------------------------------------------

class TestAssertion1(unittest.TestCase):
    def test_passes_when_dry_run_succeeds(self):
        def fake_run(cmd, **kwargs):
            return 0, "worktree /tmp/test-wt branch feat/test created", ""
        with patch.object(vl, "_run", side_effect=fake_run):
            passed, detail = vl.assert_1_new_requirement_dry_run()
        self.assertTrue(passed, f"Should pass: {detail}")

    def test_fails_when_dry_run_fails(self):
        def fake_run(cmd, **kwargs):
            return 1, "", "fatal: not a git repository"
        with patch.object(vl, "_run", side_effect=fake_run):
            passed, detail = vl.assert_1_new_requirement_dry_run()
        self.assertFalse(passed, f"Should fail: {detail}")


# ---------------------------------------------------------------------------
# Assertion 2 — brief contains 5-stage ladder
# ---------------------------------------------------------------------------

class TestAssertion2(unittest.TestCase):
    def test_passes_with_valid_lifecycle_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            lc_path = _make_lifecycle_module(Path(tmp))
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                # lifecycle.py lives at scripts/lifecycle.py
                scripts_dir = Path(tmp) / "scripts"
                scripts_dir.mkdir()
                import shutil
                shutil.copy(lc_path, scripts_dir / "lifecycle.py")
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_2_brief_contains_stage_ladder()
        self.assertTrue(passed, f"Should pass: {detail}")

    def test_fails_when_lifecycle_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_2_brief_contains_stage_ladder()
        self.assertFalse(passed)
        self.assertIn("M3", detail)


# ---------------------------------------------------------------------------
# Assertion 3 — self-drive Spine rule always-on
# ---------------------------------------------------------------------------

class TestAssertion3(unittest.TestCase):
    def test_passes_with_valid_self_drive_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".cursor" / "rules"
            rules_dir.mkdir(parents=True)
            rule = rules_dir / "self-drive.md"
            rule.write_text(textwrap.dedent("""\
                ---
                alwaysApply: true
                ---
                # Self-drive rule
                Drive the development phases yourself. Advance phases per the phase ladder.
                Use phase_state.py advance as each exit criterion is met.
            """))
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_3_self_drive_rule_always_on()
        self.assertTrue(passed, f"Should pass: {detail}")

    def test_fails_when_rule_has_globs(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".cursor" / "rules"
            rules_dir.mkdir(parents=True)
            rule = rules_dir / "self-drive.md"
            rule.write_text(textwrap.dedent("""\
                ---
                alwaysApply: true
                globs:
                  - "agents/bhaga/**"
                ---
                # Self-drive rule
                Drive the development phases yourself. Advance phases.
            """))
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_3_self_drive_rule_always_on()
        self.assertFalse(passed, "Rule with globs should not be always-on Spine rule")

    def test_fails_when_no_rule_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".cursor" / "rules"
            rules_dir.mkdir(parents=True)
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_3_self_drive_rule_always_on()
        self.assertFalse(passed)


# ---------------------------------------------------------------------------
# Assertion 4 — verify.py GATES
# ---------------------------------------------------------------------------

class TestAssertion4(unittest.TestCase):
    def test_passes_with_current_verify(self):
        # verify.py is already on disk — assertion should pass
        passed, detail = vl.assert_4_verify_gates_present()
        self.assertTrue(passed, f"Should pass with the verify.py we just built: {detail}")

    def test_fails_when_verify_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_4_verify_gates_present()
        self.assertFalse(passed)


# ---------------------------------------------------------------------------
# Assertion 5 — scripts exist and --help clean
# ---------------------------------------------------------------------------

class TestAssertion5(unittest.TestCase):
    def test_passes_when_both_scripts_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts_dir = Path(tmp) / "scripts"
            scripts_dir.mkdir()
            # Create minimal scripts
            (scripts_dir / "check_plan_readiness.py").write_text(
                '#!/usr/bin/env python3\nimport argparse\np=argparse.ArgumentParser()\np.parse_args()\n'
            )
            ps_path = _make_phase_state_module(scripts_dir)

            def fake_run(cmd, **kwargs):
                if "--help" in cmd:
                    return 0, "usage:", ""
                if "init" in cmd and "--dry-run" in cmd:
                    return 0, "[dry-run] would create gh issue", ""
                return 1, "", "unknown command"

            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)), \
                 patch.object(vl, "_run", side_effect=fake_run):
                passed, detail = vl.assert_5_scripts_exist_and_help()
        self.assertTrue(passed, f"Should pass: {detail}")

    def test_fails_when_phase_state_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts_dir = Path(tmp) / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "check_plan_readiness.py").write_text("pass\n")
            # No phase_state.py
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_5_scripts_exist_and_help()
        self.assertFalse(passed)
        self.assertIn("M3", detail)


# ---------------------------------------------------------------------------
# Assertion 6 — agent card dedup
# ---------------------------------------------------------------------------

class TestAssertion6(unittest.TestCase):
    def test_passes_when_cards_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".cursor" / "rules"
            rules_dir.mkdir(parents=True)
            # Agent cards without hoisted phrases
            for name in ("bhaga-principles.md", "chitra.md", "akshaya.md"):
                (rules_dir / name).write_text(
                    "# Domain-specific content only\n"
                    "## Allocation invariants\nInteger cents, pool-by-day.\n"
                )
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_6_agent_card_dedup()
        self.assertTrue(passed, f"Should pass: {detail}")

    def test_fails_when_hoisted_phrase_in_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".cursor" / "rules"
            rules_dir.mkdir(parents=True)
            (rules_dir / "bhaga-principles.md").write_text(
                "# BHAGA\n\nNever push to main directly. Always use a PR.\n"
            )
            (rules_dir / "chitra.md").write_text("# CHITRA\nDomain content.\n")
            (rules_dir / "akshaya.md").write_text("# AKSHAYA\nDomain content.\n")
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_6_agent_card_dedup()
        self.assertFalse(passed)
        self.assertIn("hoisted", detail)


# ---------------------------------------------------------------------------
# Assertion 7 — operator gate refused
# ---------------------------------------------------------------------------

class TestAssertion7(unittest.TestCase):
    def test_passes_when_operator_substep_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts_dir = Path(tmp) / "scripts"
            scripts_dir.mkdir()
            ps_path = _make_phase_state_module(scripts_dir)
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_7_operator_gate_refused()
        self.assertTrue(passed, f"Should pass: {detail}")

    def test_fails_when_operator_substep_allowed(self):
        def fake_run(cmd, **kwargs):
            # Simulate a broken phase_state that allows operator substep
            return 0, "advanced to jam", ""
        with patch.object(vl, "_run", side_effect=fake_run):
            passed, detail = vl.assert_7_operator_gate_refused()
        self.assertFalse(passed, f"Should fail: {detail}")


# ---------------------------------------------------------------------------
# Overall runner
# ---------------------------------------------------------------------------

class TestAssertion8(unittest.TestCase):
    def test_passes_against_real_repo(self):
        # new_requirement.py now wires init_phase_tracking → should pass on disk.
        passed, detail = vl.assert_8_new_requirement_wires_phase_state()
        self.assertTrue(passed, msg=detail)

    def test_detects_wiring_via_source(self):
        # The assertion keys off source content; confirm the marker token is present.
        src = (vl.REPO_ROOT / "scripts" / "new_requirement.py").read_text(encoding="utf-8")
        self.assertIn("init_phase_tracking", src)


# ---------------------------------------------------------------------------
# Assertion 9 — front door is interrogation-free
# ---------------------------------------------------------------------------

class TestAssertion9(unittest.TestCase):
    def test_passes_against_real_front_door(self):
        # The real new_requirement.py has no input() and accepts vague text → PASS.
        passed, detail = vl.assert_9_front_door_interrogation_free()
        self.assertTrue(passed, msg=detail)

    def test_fails_when_front_door_is_interactive(self):
        # A front door that prompts interactively must be rejected.
        with tempfile.TemporaryDirectory() as tmp:
            scripts_dir = Path(tmp) / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "new_requirement.py").write_text(
                "import sys\n"
                "answer = input('Tell me more about the requirement: ')\n"
                "print('branch fix/x')\n"
            )
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_9_front_door_interrogation_free()
        self.assertFalse(passed, f"Should fail on interactive front door: {detail}")
        self.assertIn("input()", detail)

    def test_fails_when_front_door_rejects_vague_text(self):
        # A front door whose --dry-run exits nonzero for vague text must be rejected.
        def fake_run(cmd, **kwargs):
            return 1, "", "error: requirement too vague, please clarify"
        with tempfile.TemporaryDirectory() as tmp:
            scripts_dir = Path(tmp) / "scripts"
            scripts_dir.mkdir()
            # Source is clean (no input()), but runtime rejects vague text.
            (scripts_dir / "new_requirement.py").write_text(
                "import sys\nprint('would reject')\n"
            )
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)), \
                 patch.object(vl, "_run", side_effect=fake_run):
                passed, detail = vl.assert_9_front_door_interrogation_free()
        self.assertFalse(passed, f"Should fail when vague text is rejected: {detail}")


class TestAssertion10(unittest.TestCase):
    def test_passes_against_real_front_door(self):
        passed, detail = vl.assert_10_jam_handoff_ask_mode_honest()
        self.assertTrue(passed, msg=detail)

    def _make_nr_stub(self, scripts_dir: Path) -> None:
        """Write a new_requirement.py stub that passes the wiring checks."""
        (scripts_dir / "new_requirement.py").write_text(
            "# stub\n"
            "seed_prompt_jam = True\n"
            "# make_deeplink(seed, mode=mode, model=model)\n"
        )

    def test_fails_when_seed_missing_model_selection_line(self):
        """seed_prompt_jam that lacks the operator model-selection instruction must fail."""
        with tempfile.TemporaryDirectory() as tmp:
            scripts_dir = Path(tmp) / "scripts"
            scripts_dir.mkdir()
            self._make_nr_stub(scripts_dir)
            # Ask mode + diagnosis line but missing model-selection line
            (scripts_dir / "start_pr_session.py").write_text(textwrap.dedent("""\
                DEFAULT_JAM_HANDOFF_MODE = "ask"
                DEFAULT_JAM_HANDOFF_MODEL = "claude-opus-4-8-thinking-high"
                def seed_prompt_jam(key, *, brief_rel, requirement=None):
                    return "jam\\nRead-only diagnosis is expected during jam."
                def make_deeplink(text, *, mode="agent", model=None):
                    return f"cursor://x?text={text}&mode={mode}"
            """))
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_10_jam_handoff_ask_mode_honest()
        self.assertFalse(passed, f"Should fail without model-selection line: {detail}")
        self.assertIn("model-selection", detail)

    def test_fails_when_seed_missing_diagnosis_line(self):
        """seed_prompt_jam that lacks the read-only-diagnosis line must fail."""
        with tempfile.TemporaryDirectory() as tmp:
            scripts_dir = Path(tmp) / "scripts"
            scripts_dir.mkdir()
            self._make_nr_stub(scripts_dir)
            # Has model-selection line but missing diagnosis line
            (scripts_dir / "start_pr_session.py").write_text(textwrap.dedent("""\
                DEFAULT_JAM_HANDOFF_MODE = "ask"
                DEFAULT_JAM_HANDOFF_MODEL = "claude-opus-4-8-thinking-high"
                def seed_prompt_jam(key, *, brief_rel, requirement=None):
                    return "jam\\ndeeplink cannot pre-select the model."
                def make_deeplink(text, *, mode="agent", model=None):
                    return f"cursor://x?text={text}&mode={mode}"
            """))
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_10_jam_handoff_ask_mode_honest()
        self.assertFalse(passed, f"Should fail without diagnosis line: {detail}")
        self.assertIn("diagnosis", detail)


# ---------------------------------------------------------------------------
# Assertion 11 — phase-gate registered hard; OBSERVABLE_FLOOR enforces ladder
# ---------------------------------------------------------------------------

class TestAssertion11(unittest.TestCase):
    def test_passes_against_real_repo(self):
        """Real verify.py and phase_state.py should satisfy assertion #11."""
        passed, detail = vl.assert_11_phase_gate_enforces_ladder()
        self.assertTrue(passed, msg=detail)

    def test_fails_when_phase_gate_missing_from_verify(self):
        """If verify.py has no phase-gate Gate, assertion must fail."""
        with tempfile.TemporaryDirectory() as tmp:
            scripts_dir = Path(tmp) / "scripts"
            scripts_dir.mkdir()

            # phase_state.py with gate + OBSERVABLE_FLOOR
            ps_src = textwrap.dedent("""\
                #!/usr/bin/env python3
                import sys
                def cmd_gate(args): pass
                OBSERVABLE_FLOOR = [("implement", lambda: False), ("pr-evidence", lambda: False)]
                if __name__ == "__main__":
                    sys.argv.append("gate")  # satisfy "gate" subcommand check
            """)
            (scripts_dir / "phase_state.py").write_text(ps_src)

            # verify.py without phase-gate
            verify_src = textwrap.dedent("""\
                from typing import NamedTuple
                class Gate(NamedTuple):
                    name: str
                    argv: list
                    hard: bool
                    modes: set
                GATES = [
                    Gate("secret-scan-staged", ["git", "diff"], True, {"fast"}),
                ]
            """)
            (scripts_dir / "verify.py").write_text(verify_src)

            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_11_phase_gate_enforces_ladder()
        self.assertFalse(passed, f"Should fail when phase-gate absent: {detail}")

    def test_fails_when_observable_floor_missing(self):
        """If phase_state.py lacks OBSERVABLE_FLOOR, assertion must fail."""
        with tempfile.TemporaryDirectory() as tmp:
            scripts_dir = Path(tmp) / "scripts"
            scripts_dir.mkdir()

            # phase_state.py without OBSERVABLE_FLOOR
            ps_src = textwrap.dedent("""\
                #!/usr/bin/env python3
                def cmd_gate(args): pass
            """)
            (scripts_dir / "phase_state.py").write_text(ps_src)

            # verify.py with phase-gate but hard=True, full mode
            verify_src = textwrap.dedent("""\
                from typing import NamedTuple
                class Gate(NamedTuple):
                    name: str
                    argv: list
                    hard: bool
                    modes: set
                GATES = [
                    Gate("phase-gate", ["python3", "scripts/phase_state.py", "gate"], True, {"full"}),
                ]
            """)
            (scripts_dir / "verify.py").write_text(verify_src)

            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_11_phase_gate_enforces_ladder()
        self.assertFalse(passed, f"Should fail when OBSERVABLE_FLOOR absent: {detail}")


# ---------------------------------------------------------------------------
# Updated overall runner — must include assertion 11
# ---------------------------------------------------------------------------

class TestRunFunction(unittest.TestCase):
    def test_run_returns_0_on_full_pass(self):
        with patch.object(vl, "assert_1_new_requirement_dry_run", return_value=(True, "ok")), \
             patch.object(vl, "assert_2_brief_contains_stage_ladder", return_value=(True, "ok")), \
             patch.object(vl, "assert_3_self_drive_rule_always_on", return_value=(True, "ok")), \
             patch.object(vl, "assert_4_verify_gates_present", return_value=(True, "ok")), \
             patch.object(vl, "assert_5_scripts_exist_and_help", return_value=(True, "ok")), \
             patch.object(vl, "assert_6_agent_card_dedup", return_value=(True, "ok")), \
             patch.object(vl, "assert_7_operator_gate_refused", return_value=(True, "ok")), \
             patch.object(vl, "assert_8_new_requirement_wires_phase_state", return_value=(True, "ok")), \
             patch.object(vl, "assert_9_front_door_interrogation_free", return_value=(True, "ok")), \
             patch.object(vl, "assert_10_jam_handoff_ask_mode_honest", return_value=(True, "ok")), \
             patch.object(vl, "assert_11_phase_gate_enforces_ladder", return_value=(True, "ok")):
            rc = vl.run()
        self.assertEqual(rc, 0)

    def test_run_returns_1_on_hard_fail(self):
        with patch.object(vl, "assert_1_new_requirement_dry_run", return_value=(False, "fail")), \
             patch.object(vl, "assert_2_brief_contains_stage_ladder", return_value=(True, "ok")), \
             patch.object(vl, "assert_3_self_drive_rule_always_on", return_value=(True, "ok")), \
             patch.object(vl, "assert_4_verify_gates_present", return_value=(False, "missing gates")), \
             patch.object(vl, "assert_5_scripts_exist_and_help", return_value=(True, "ok")), \
             patch.object(vl, "assert_6_agent_card_dedup", return_value=(True, "ok")), \
             patch.object(vl, "assert_7_operator_gate_refused", return_value=(True, "ok")), \
             patch.object(vl, "assert_8_new_requirement_wires_phase_state", return_value=(True, "ok")), \
             patch.object(vl, "assert_9_front_door_interrogation_free", return_value=(True, "ok")), \
             patch.object(vl, "assert_10_jam_handoff_ask_mode_honest", return_value=(True, "ok")), \
             patch.object(vl, "assert_11_phase_gate_enforces_ladder", return_value=(True, "ok")):
            rc = vl.run()
        self.assertEqual(rc, 1)

    def test_run_returns_0_with_pre_milestone_warns(self):
        with patch.object(vl, "assert_1_new_requirement_dry_run", return_value=(True, "ok")), \
             patch.object(vl, "assert_2_brief_contains_stage_ladder", return_value=(False, "needs M3")), \
             patch.object(vl, "assert_3_self_drive_rule_always_on", return_value=(False, "needs M5")), \
             patch.object(vl, "assert_4_verify_gates_present", return_value=(True, "ok")), \
             patch.object(vl, "assert_5_scripts_exist_and_help", return_value=(False, "needs M3")), \
             patch.object(vl, "assert_6_agent_card_dedup", return_value=(False, "needs M5")), \
             patch.object(vl, "assert_7_operator_gate_refused", return_value=(False, "needs M3")), \
             patch.object(vl, "assert_8_new_requirement_wires_phase_state", return_value=(True, "ok")), \
             patch.object(vl, "assert_9_front_door_interrogation_free", return_value=(True, "ok")), \
             patch.object(vl, "assert_10_jam_handoff_ask_mode_honest", return_value=(True, "ok")), \
             patch.object(vl, "assert_11_phase_gate_enforces_ladder", return_value=(True, "ok")):
            rc = vl.run()
        self.assertEqual(rc, 0, "Pre-milestone WARNs should not cause exit 1")


if __name__ == "__main__":
    unittest.main()
