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
            rule = rules_dir / "self-drive.mdc"
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
            rule = rules_dir / "self-drive.mdc"
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
            for name in ("bhaga-principles.mdc", "chitra.mdc", "akshaya.mdc"):
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
            (rules_dir / "bhaga-principles.mdc").write_text(
                "# BHAGA\n\nNever push to main directly. Always use a PR.\n"
            )
            (rules_dir / "chitra.mdc").write_text("# CHITRA\nDomain content.\n")
            (rules_dir / "akshaya.mdc").write_text("# AKSHAYA\nDomain content.\n")
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
# Assertions 12 + 13 smoke tests
# ---------------------------------------------------------------------------

class TestAssertion12(unittest.TestCase):
    def test_assertion_12_passes_with_real_guardrail(self):
        """Assertion 12 passes when guardrail.py and store.py are present and wired."""
        passed, detail = vl.assert_12_guardrail_enforced_at_store()
        self.assertTrue(passed, detail)

    def test_assertion_12_fails_if_guardrail_missing(self):
        """Assertion 12 fails when guardrail.py does not exist."""
        with patch.object(Path, "exists", return_value=False):
            passed, detail = vl.assert_12_guardrail_enforced_at_store()
        self.assertFalse(passed)


class TestAssertion13(unittest.TestCase):
    def test_assertion_13_passes_with_real_files(self):
        """Assertion 13 passes when all plan-gate artifacts are in place."""
        passed, detail = vl.assert_13_observable_floor_has_plan_entry()
        self.assertTrue(passed, detail)


# ---------------------------------------------------------------------------
# Updated overall runner — must include assertions 11, 12, 13
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Assertion 14 — intake rule wired
# ---------------------------------------------------------------------------

class TestAssertion14(unittest.TestCase):
    def _make_intake_rule(self, rules_dir: Path, *, always_on: bool = True,
                          has_marker: bool = True, has_xrefs: bool = True) -> None:
        body = "---\n"
        body += f"alwaysApply: {'true' if always_on else 'false'}\n"
        body += "---\n\n"
        if has_marker:
            body += "<!-- canonical:intake -->\n"
            body += "When the operator signals a new requirement, run new_requirement.py.\n\n"
        if has_xrefs:
            body += "See self-drive.mdc and jarvis-hard-lessons.mdc for context.\n"
        (rules_dir / "new-requirement-intake.mdc").write_text(body)

    def test_passes_with_valid_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".cursor" / "rules"
            rules_dir.mkdir(parents=True)
            self._make_intake_rule(rules_dir)
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_14_intake_rule_wired()
        self.assertTrue(passed, detail)

    def test_fails_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".cursor" / "rules"
            rules_dir.mkdir(parents=True)
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_14_intake_rule_wired()
        self.assertFalse(passed)
        self.assertIn("not found", detail)

    def test_fails_when_md_version_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".cursor" / "rules"
            rules_dir.mkdir(parents=True)
            self._make_intake_rule(rules_dir)
            (rules_dir / "new-requirement-intake.md").write_text("# wrong ext\n")
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_14_intake_rule_wired()
        self.assertFalse(passed)
        self.assertIn(".mdc", detail)

    def test_fails_when_not_always_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".cursor" / "rules"
            rules_dir.mkdir(parents=True)
            self._make_intake_rule(rules_dir, always_on=False)
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_14_intake_rule_wired()
        self.assertFalse(passed)

    def test_fails_when_canonical_marker_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".cursor" / "rules"
            rules_dir.mkdir(parents=True)
            self._make_intake_rule(rules_dir, has_marker=False)
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_14_intake_rule_wired()
        self.assertFalse(passed)
        self.assertIn("canonical:intake", detail)

    def test_fails_when_canonical_sentence_in_other_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".cursor" / "rules"
            rules_dir.mkdir(parents=True)
            self._make_intake_rule(rules_dir)
            # Duplicate the canonical sentence into another rule
            (rules_dir / "self-drive.mdc").write_text(
                "---\nalwaysApply: true\n---\n"
                "When the operator signals a new requirement, run new_requirement.py.\n"
            )
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_14_intake_rule_wired()
        self.assertFalse(passed)
        self.assertIn("single source", detail.lower())


# ---------------------------------------------------------------------------
# Assertion 15 — no .md files in rules dir
# ---------------------------------------------------------------------------

class TestAssertion15(unittest.TestCase):
    def test_passes_when_all_mdc(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".cursor" / "rules"
            rules_dir.mkdir(parents=True)
            (rules_dir / "self-drive.mdc").write_text("---\nalwaysApply: true\n---\n# ok\n")
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_15_no_md_rules()
        self.assertTrue(passed, detail)

    def test_fails_when_md_file_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".cursor" / "rules"
            rules_dir.mkdir(parents=True)
            (rules_dir / "self-drive.md").write_text("---\nalwaysApply: true\n---\n# wrong ext\n")
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_15_no_md_rules()
        self.assertFalse(passed)
        self.assertIn("self-drive.md", detail)

    def test_passes_against_real_repo(self):
        passed, detail = vl.assert_15_no_md_rules()
        self.assertTrue(passed, detail)


# ---------------------------------------------------------------------------
# Assertion 16 — rule load semantics preserved
# ---------------------------------------------------------------------------

class TestAssertion16(unittest.TestCase):
    def _write_rule(self, rules_dir: Path, name: str, always_on: bool,
                    globs: list[str] | None = None) -> None:
        body = "---\n"
        if always_on:
            body += "alwaysApply: true\n"
        else:
            body += "alwaysApply: false\n"
        if globs is not None:
            body += "globs:\n"
            for g in globs:
                body += f'  - "{g}"\n'
        body += "---\n# content\n"
        (rules_dir / name).write_text(body)

    def test_passes_with_correct_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".cursor" / "rules"
            rules_dir.mkdir(parents=True)
            for name in ("behavioral-anchor.mdc", "jarvis.mdc", "plan-execution-readiness.mdc",
                         "preference-consult.mdc", "self-drive.mdc", "user-preferences.mdc",
                         "new-requirement-intake.mdc"):
                self._write_rule(rules_dir, name, always_on=True)
            self._write_rule(rules_dir, "jarvis-hard-lessons.mdc", always_on=False,
                             globs=[".cursor/rules/jarvis*.mdc"])
            self._write_rule(rules_dir, "chitra-playbook.mdc", always_on=False, globs=[])
            self._write_rule(rules_dir, "chitra-workflows.mdc", always_on=False, globs=[])
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_16_rule_semantics_preserved()
        self.assertTrue(passed, detail)

    def test_fails_when_hard_lessons_made_always_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".cursor" / "rules"
            rules_dir.mkdir(parents=True)
            for name in ("behavioral-anchor.mdc", "jarvis.mdc", "plan-execution-readiness.mdc",
                         "preference-consult.mdc", "self-drive.mdc", "user-preferences.mdc",
                         "new-requirement-intake.mdc"):
                self._write_rule(rules_dir, name, always_on=True)
            # Accidentally make hard-lessons always-on (regression)
            self._write_rule(rules_dir, "jarvis-hard-lessons.mdc", always_on=True)
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_16_rule_semantics_preserved()
        self.assertFalse(passed)
        self.assertIn("jarvis-hard-lessons", detail)

    def test_passes_against_real_repo(self):
        passed, detail = vl.assert_16_rule_semantics_preserved()
        self.assertTrue(passed, detail)


# ---------------------------------------------------------------------------
# Assertion 17 — new_requirement seeds worktree cache
# ---------------------------------------------------------------------------

class TestAssertion17(unittest.TestCase):
    def test_passes_against_real_repo(self):
        passed, detail = vl.assert_17_new_requirement_seeds_worktree_cache()
        self.assertTrue(passed, detail)

    def test_fails_when_seeding_code_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts_dir = Path(tmp) / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "new_requirement.py").write_text(
                "# stub — no worktree cache seeding\n"
                "def init_phase_tracking(**kw): pass\n"
            )
            with patch("verify_lifecycle.REPO_ROOT", Path(tmp)):
                passed, detail = vl.assert_17_new_requirement_seeds_worktree_cache()
        self.assertFalse(passed)
        self.assertIn("worktree", detail.lower())


class TestAssertion18(unittest.TestCase):
    def test_passes_against_real_repo(self):
        passed, detail = vl.assert_18_intake_hook_harness_wired()
        self.assertTrue(passed, detail)

    def test_fails_when_enforce_sh_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # create the other required files
            hooks_dir = root / ".cursor" / "hooks"
            hooks_dir.mkdir(parents=True)
            gate = hooks_dir / "prompt_gate.py"
            gate.write_text(
                '# new_requirement.py\n'
                'def main(): append_to_corpus("x"); print({"continue": True}); pass  # //inline\n'
            )
            (root / "scripts").mkdir()
            (root / "scripts" / "install-git-hooks.sh").write_text(
                "hooks.json\nbeforeSubmitPrompt"
            )
            (root / "skills" / "user_model").mkdir(parents=True)
            (root / "skills" / "user_model" / "store.py").write_text("corpus-append\n")
            # enforce.sh deliberately NOT created
            with patch("verify_lifecycle.REPO_ROOT", root):
                passed, detail = vl.assert_18_intake_hook_harness_wired()
        self.assertFalse(passed)
        self.assertIn("enforce.sh", detail)

    def test_fails_when_prompt_gate_missing_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hooks_dir = root / ".cursor" / "hooks"
            hooks_dir.mkdir(parents=True)
            enforce = hooks_dir / "enforce.sh"
            enforce.write_text("#!/bin/bash\nexec python3 prompt_gate.py\n")
            enforce.chmod(0o755)
            # prompt_gate.py missing //inline
            gate = hooks_dir / "prompt_gate.py"
            gate.write_text(
                '# new_requirement.py\ndef main(): append_to_corpus("x"); return {"continue": True}\n'
            )
            (root / "scripts").mkdir()
            (root / "scripts" / "install-git-hooks.sh").write_text(
                "hooks.json\nbeforeSubmitPrompt"
            )
            (root / "skills" / "user_model").mkdir(parents=True)
            (root / "skills" / "user_model" / "store.py").write_text("corpus-append\n")
            with patch("verify_lifecycle.REPO_ROOT", root):
                passed, detail = vl.assert_18_intake_hook_harness_wired()
        self.assertFalse(passed)
        self.assertIn("//inline", detail)

    def test_fails_when_installer_missing_dispatcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hooks_dir = root / ".cursor" / "hooks"
            hooks_dir.mkdir(parents=True)
            enforce = hooks_dir / "enforce.sh"
            enforce.write_text("#!/bin/bash\nexec python3 prompt_gate.py\n")
            enforce.chmod(0o755)
            gate = hooks_dir / "prompt_gate.py"
            gate.write_text(
                '# new_requirement.py\ndef main(): append_to_corpus("x"); return {"continue": True}  # //inline\n'
            )
            (root / "scripts").mkdir()
            # installer missing hooks.json reference
            (root / "scripts" / "install-git-hooks.sh").write_text("git config core.hooksPath\n")
            (root / "skills" / "user_model").mkdir(parents=True)
            (root / "skills" / "user_model" / "store.py").write_text("corpus-append\n")
            with patch("verify_lifecycle.REPO_ROOT", root):
                passed, detail = vl.assert_18_intake_hook_harness_wired()
        self.assertFalse(passed)
        self.assertIn("hooks.json", detail)

    def test_fails_when_corpus_append_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hooks_dir = root / ".cursor" / "hooks"
            hooks_dir.mkdir(parents=True)
            enforce = hooks_dir / "enforce.sh"
            enforce.write_text("#!/bin/bash\nexec python3 prompt_gate.py\n")
            enforce.chmod(0o755)
            gate = hooks_dir / "prompt_gate.py"
            gate.write_text(
                '# new_requirement.py\ndef main(): append_to_corpus("x"); return {"continue": True}  # //inline\n'
            )
            (root / "scripts").mkdir()
            (root / "scripts" / "install-git-hooks.sh").write_text(
                "hooks.json\nbeforeSubmitPrompt"
            )
            (root / "skills" / "user_model").mkdir(parents=True)
            # store.py missing corpus-append
            (root / "skills" / "user_model" / "store.py").write_text("corpus-tail\n")
            with patch("verify_lifecycle.REPO_ROOT", root):
                passed, detail = vl.assert_18_intake_hook_harness_wired()
        self.assertFalse(passed)
        self.assertIn("corpus-append", detail)


class TestRunFunction(unittest.TestCase):
    def _all_mocks(self):
        return {
            "assert_1_new_requirement_dry_run": (True, "ok"),
            "assert_2_brief_contains_stage_ladder": (True, "ok"),
            "assert_3_self_drive_rule_always_on": (True, "ok"),
            "assert_4_verify_gates_present": (True, "ok"),
            "assert_5_scripts_exist_and_help": (True, "ok"),
            "assert_6_agent_card_dedup": (True, "ok"),
            "assert_7_operator_gate_refused": (True, "ok"),
            "assert_8_new_requirement_wires_phase_state": (True, "ok"),
            "assert_9_front_door_interrogation_free": (True, "ok"),
            "assert_10_jam_handoff_ask_mode_honest": (True, "ok"),
            "assert_11_phase_gate_enforces_ladder": (True, "ok"),
            "assert_12_guardrail_enforced_at_store": (True, "ok"),
            "assert_13_observable_floor_has_plan_entry": (True, "ok"),
            "assert_14_intake_rule_wired": (True, "ok"),
            "assert_15_no_md_rules": (True, "ok"),
            "assert_16_rule_semantics_preserved": (True, "ok"),
            "assert_17_new_requirement_seeds_worktree_cache": (True, "ok"),
            "assert_18_intake_hook_harness_wired": (True, "ok"),
        }

    def test_run_returns_0_on_full_pass(self):
        mocks = self._all_mocks()
        with patch.multiple(vl, **{k: staticmethod(lambda rv=v: rv)
                                   for k, v in mocks.items()}):
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
             patch.object(vl, "assert_11_phase_gate_enforces_ladder", return_value=(True, "ok")), \
             patch.object(vl, "assert_12_guardrail_enforced_at_store", return_value=(True, "ok")), \
             patch.object(vl, "assert_13_observable_floor_has_plan_entry", return_value=(True, "ok")), \
             patch.object(vl, "assert_14_intake_rule_wired", return_value=(True, "ok")), \
             patch.object(vl, "assert_15_no_md_rules", return_value=(True, "ok")), \
             patch.object(vl, "assert_16_rule_semantics_preserved", return_value=(True, "ok")), \
             patch.object(vl, "assert_17_new_requirement_seeds_worktree_cache", return_value=(True, "ok")), \
             patch.object(vl, "assert_18_intake_hook_harness_wired", return_value=(True, "ok")):
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
             patch.object(vl, "assert_11_phase_gate_enforces_ladder", return_value=(True, "ok")), \
             patch.object(vl, "assert_12_guardrail_enforced_at_store", return_value=(True, "ok")), \
             patch.object(vl, "assert_13_observable_floor_has_plan_entry", return_value=(True, "ok")), \
             patch.object(vl, "assert_14_intake_rule_wired", return_value=(True, "ok")), \
             patch.object(vl, "assert_15_no_md_rules", return_value=(True, "ok")), \
             patch.object(vl, "assert_16_rule_semantics_preserved", return_value=(True, "ok")), \
             patch.object(vl, "assert_17_new_requirement_seeds_worktree_cache", return_value=(True, "ok")), \
             patch.object(vl, "assert_18_intake_hook_harness_wired", return_value=(True, "ok")):
            rc = vl.run()
        self.assertEqual(rc, 0, "Pre-milestone WARNs should not cause exit 1")


if __name__ == "__main__":
    unittest.main()
