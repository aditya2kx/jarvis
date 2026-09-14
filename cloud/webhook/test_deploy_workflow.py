"""The BHAGA deploy must land traffic on the revision it just built.

Issue #294: `gcloud run services update --image` creates a revision but will
NOT move traffic when the service has traffic pinned to a named revision.
bhaga-webhook was pinned to an `i223-pr224` preview on 2026-08-05 and served it
for six weeks — #264 and #291 merged, built, deployed green, and never went
live. Nothing in the Actions UI distinguished those runs from good ones.

The operator-console workflow already learned this in #240. These tests hold
both workflows to the same contract so the BHAGA side cannot quietly drift back.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BHAGA_WF = ROOT / ".github/workflows/deploy.yml"
CONSOLE_WF = ROOT / ".github/workflows/operator-console-deploy.yml"


def _step(text: str, name: str) -> str:
    """The body of the named step, up to the next step."""
    assert name in text, f"missing deploy step: {name}"
    return text.split(name, 1)[1].split("- name:", 1)[0]


def test_bhaga_routes_traffic_to_latest_after_deploying_the_webhook():
    text = BHAGA_WF.read_text()
    body = _step(text, "Route 100% traffic to latest revision")
    assert "--to-latest" in body
    assert "bhaga-webhook" in body
    # Routing before the deploy would pin traffic to the *previous* revision.
    assert text.index("Deploy webhook (Cloud Run Service)") < text.index(
        "Route 100% traffic to latest revision"
    ), "traffic routing must come after the webhook deploy"


def test_bhaga_verifies_the_serving_revision_matches_the_commit():
    """The pin was the cause; a green deploy serving old code was the defect."""
    text = BHAGA_WF.read_text()
    body = _step(text, "Verify deployed units run this commit")
    # Services resolve tags to digests on the revision, so the check is by digest.
    assert "image_summary.digest" in body
    assert "status.traffic" in body and "percent == 100" in body
    # The job keeps the tag it was given, so it is checked by tag.
    assert "bhaga-daily-refresh" in body
    assert "github.sha" in body
    # Must be able to fail the workflow — a warning would reproduce #294.
    assert "::error::" in body
    assert "exit 1" in body
    assert "continue-on-error" not in body


def test_verification_runs_after_traffic_routing():
    text = BHAGA_WF.read_text()
    assert text.index("Route 100% traffic to latest revision") < text.index(
        "Verify deployed units run this commit"
    ), "verifying before routing would assert against the pre-deploy revision"


def test_console_workflow_still_routes_traffic_to_latest():
    """#240's fix is the precedent this one mirrors — keep it in place."""
    body = _step(CONSOLE_WF.read_text(), "Route 100% traffic to latest revision")
    assert "--to-latest" in body
