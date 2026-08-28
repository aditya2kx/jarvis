"""The deploy config is where the cost model actually lives — pin it in a test."""

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
