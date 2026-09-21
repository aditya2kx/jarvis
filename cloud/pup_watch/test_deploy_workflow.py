"""The deploy config is where the cost model actually lives — pin it in a test."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github/workflows/pup-watch-deploy.yml"
DOCKERFILE = ROOT / "cloud/pup_watch/Dockerfile"


def _commands() -> str:
    """Workflow body with YAML comments stripped, so prose cannot satisfy a gate."""
    lines = []
    for line in WF.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def test_service_scales_to_zero():
    """A warm instance is what would make this cost money instead of nothing."""
    cmds = _commands()
    assert "--min-instances" not in cmds
    assert "--no-cpu-throttling" not in cmds
    assert "--max-instances 1" in cmds


def test_scheduler_ticks_every_minute_in_store_timezone():
    text = WF.read_text()
    assert '--schedule "* 6-20 * * *"' in text
    assert '--time-zone "America/Chicago"' in text
    assert "/tick" in text


def test_scheduler_is_idempotent_across_deploys():
    """create fails once the job exists; the workflow must update instead."""
    text = WF.read_text()
    assert "gcloud scheduler jobs describe" in text
    assert "VERB=update" in text
    assert "VERB=create" in text


def test_scheduler_header_flag_matches_the_verb():
    """`create` takes --headers; `update` takes --update-headers.

    The first real deploy used the update spelling on the create path and died
    with "unrecognized arguments: --update-headers" — invisible until the job
    did not yet exist, i.e. exactly once, on the deploy that mattered.
    """
    cmds = _commands()
    assert "VERB=create\n" in cmds.replace("            ", "").replace("  ", "") or "VERB=create" in cmds
    assert "HEADER_FLAG=--headers" in cmds
    assert "HEADER_FLAG=--update-headers" in cmds
    # The literal update-only spelling must not be hardcoded on the shared call.
    assert "--update-headers \"X-PupWatch-Token" not in cmds


def test_env_vars_use_a_delimiter_that_survives_comma_separated_values():
    """`--set-env-vars` splits on comma by default, but two values ARE lists.

    PUPWATCH_REFERENCE_URIS (five gs:// paths) and PUPWATCH_NOTIFY_TO (two
    addresses) both contain commas, so the default delimiter made gcloud read a
    photo path as an env var name: "Bad syntax for dict arg". The "^|^" prefix
    switches the delimiter. It must not be "@", which appears in the addresses.
    """
    cmds = _commands()
    assert '--set-env-vars "^|^' in cmds, "must set a non-comma delimiter"
    assert '--set-env-vars "^@^' not in cmds, "'@' collides with email addresses"
    env_arg = cmds.split('--set-env-vars "^|^', 1)[1].split('"', 1)[0]
    # Every pair must be pipe-separated, so no bare comma may separate keys.
    for pair in env_arg.split("|"):
        assert "=" in pair, f"not a KEY=VALUE pair: {pair!r}"
    for required in ("PUPWATCH_REFERENCE_URIS=", "PUPWATCH_NOTIFY_TO="):
        assert any(p.startswith(required) for p in env_arg.split("|")), required


def test_tick_is_authenticated_with_the_admin_token():
    text = WF.read_text()
    assert "X-PupWatch-Token=" in text
    assert "PUPWATCH_ADMIN_TOKEN=pupwatch-admin-token:latest" in text


def test_recipients_come_from_a_secret_not_the_workflow_file():
    """tesla-aladdin-garage hardcodes its address; pup-watch must not."""
    import re

    text = WF.read_text()
    assert "PUPWATCH_NOTIFY_TO=${{ secrets.PUPWATCH_NOTIFY_TO }}" in text
    assert not re.search(r"[\w.+-]+@(?!example\.)[\w-]+\.[\w.]+", text.replace("${{", "").replace("}}", "")) or \
        all("gserviceaccount.com" in m for m in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text))


def test_gmail_and_gemini_credentials_are_mounted_from_secret_manager():
    text = WF.read_text()
    for secret in (
        "GMAIL_CLIENT_ID=gmail-client-id:latest",
        "GMAIL_CLIENT_SECRET=gmail-client-secret:latest",
        "GMAIL_REFRESH_TOKEN=gmail-refresh-token:latest",
        "PUPWATCH_GEMINI_TOKEN=pupwatch-gemini-token:latest",
    ):
        assert secret in text


def test_state_uses_its_own_named_database():
    """BHAGA owns (default); pup-watch must not write into it."""
    text = WF.read_text()
    assert "FIRESTORE_DB: pupwatch" in text
    assert "PUPWATCH_FIRESTORE_DB=${{ env.FIRESTORE_DB }}" in text
    assert "--database=${{ env.FIRESTORE_DB }}" in text
    # The app's own default must agree with the deployed value.
    from cloud.pup_watch import persist

    assert persist._firestore_database() == "pupwatch"


def test_firestore_create_is_nonfatal():
    text = WF.read_text()
    assert "Ensure Firestore database" in text
    block = text.split("Ensure Firestore database", 1)[1].split("- name:", 1)[0]
    assert "continue-on-error: true" in block


def test_memory_fits_the_onnx_detector():
    assert "--memory 2Gi" in WF.read_text()


def test_detector_weights_are_fetched_at_build_time_and_checksummed():
    """A 136MB binary does not belong in git, and an upstream swap must fail loudly."""
    text = DOCKERFILE.read_text()
    assert "DETECTOR_URL=" in text
    assert "DETECTOR_SHA256=008ce02156c1c6e7e636302afd4e14bb04d0c2e291274904414d040505847d6b" in text
    assert "sha256sum -c -" in text
    assert not (ROOT / "cloud/pup_watch/models").exists()


def test_ffmpeg_is_installed_in_the_image():
    assert "ffmpeg" in DOCKERFILE.read_text()


def test_cpu_is_enough_for_two_cameras_and_matches_the_thread_count():
    """Prod regression, 2026-09-18: with `--cpu 1` a two-camera tick measured
    100-120s against a 60s schedule, so ticks overlapped, Cloud Run aborted them
    ("no available instance") and `b-yard` was starved of polls. At 4 vCPU the
    same tick measured 52s.

    The pairing matters as much as the number: the deploy asked for 1 vCPU while
    telling onnxruntime to use 2 threads, so the detector was oversubscribed
    against itself. Whoever changes one must change the other.
    """
    text = _commands()
    cpu = re.search(r"--cpu (\d+)", text)
    threads = re.search(r"PUPWATCH_ORT_THREADS=(\d+)", text)
    assert cpu and threads, "deploy must pin both --cpu and PUPWATCH_ORT_THREADS"
    assert int(cpu.group(1)) >= 4, "two cameras do not fit in the 60s tick below 4 vCPU"
    assert cpu.group(1) == threads.group(1), (
        f"--cpu {cpu.group(1)} but ORT_THREADS={threads.group(1)}; "
        "oversubscribing the detector is what made ticks overlap"
    )


def test_no_comment_hides_inside_the_deploy_continuation():
    """A `#` line between backslash-continued arguments comments out the rest of
    the command. The explanation for --cpu was briefly written that way while
    fixing the above, and this workflow has already shipped two deploy-breaking
    argument bugs, so the shape is worth pinning rather than remembering.
    """
    lines = WF.read_text().splitlines()
    for i, line in enumerate(lines[:-1]):
        if line.rstrip().endswith("\\") and lines[i + 1].lstrip().startswith("#"):
            raise AssertionError(
                f"line {i + 2} is a comment inside a continued command: {lines[i + 1]!r}"
            )
