"""Deploy job must not fail the Cloud Run rollout on log-metric IAM."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github/workflows/tesla-aladdin-garage-deploy.yml"


def test_stale_poll_metric_step_is_nonfatal():
    text = WF.read_text()
    assert "Ensure stale-poll log metric" in text
    block = text.split("Ensure stale-poll log metric", 1)[1]
    head = block.split("- name:", 1)[0]
    assert "continue-on-error: true" in head


def test_iam_binding_step_is_nonfatal():
    text = WF.read_text()
    assert "Allow Cloud Run to persist rotated Tesla refresh tokens" in text
    block = text.split("Allow Cloud Run to persist rotated Tesla refresh tokens", 1)[1]
    head = block.split("- name:", 1)[0]
    assert "continue-on-error: true" in head


def test_deploy_enables_telemetry_not_rest_poll():
    text = WF.read_text()
    assert "TESLA_TELEMETRY=1" in text
    assert "POLL_INTERVAL_S=0" in text
    assert "LOCATION_MIN_DELTA_M=80" in text
