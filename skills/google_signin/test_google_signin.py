"""Google sign-in building blocks: only a verified IAP assertion yields an identity."""

from __future__ import annotations

import pytest

from skills.google_signin import access, identity

AUD = identity.audience("store-tasks-app")


def test_audience_is_the_direct_cloud_run_form():
    assert AUD == "/projects/887772634501/locations/us-central1/services/store-tasks-app"


def test_identity_needs_a_verified_assertion(monkeypatch):
    monkeypatch.delenv("BYPASS_IAP_EMAIL", raising=False)
    with pytest.raises(identity.NotSignedIn):
        identity.email_from_headers({"x-goog-authenticated-user-email": "accounts.google.com:a@b.com"}, AUD)
    with pytest.raises(identity.NotSignedIn):
        identity.verify("not-a-jwt", AUD)

    def good(jwt, request, audience, certs_url):
        assert audience == AUD and certs_url == identity.CERTS_URL
        return {"iss": identity.ISSUER, "email": "A@B.com"}

    assert identity.verify("jwt", AUD, verify_token=good) == "a@b.com"
    with pytest.raises(identity.NotSignedIn):
        identity.verify("jwt", AUD, verify_token=lambda *a, **k: {"iss": "https://evil", "email": "a@b.com"})


class FakeSession:
    def __init__(self):
        self.policy = {"etag": "e1", "bindings": [
            {"role": "roles/iap.admin", "members": ["serviceAccount:x"]},
            {"role": access.ACCESSOR, "members": ["user:A@b.com"]}]}

    def post(self, url, json, timeout):
        assert url.startswith(access.resource("svc"))
        if url.endswith(":setIamPolicy"):
            self.policy = json["policy"]
        return type("R", (), {"status_code": 200, "raise_for_status": lambda s: None, "json": lambda s: self.policy})()


def test_access_list_grant_revoke_keeps_other_bindings():
    a = access.Access("svc", session=FakeSession())
    assert a.members() == ["a@b.com"]
    assert a.set("a@b.com", True) == "unchanged"
    assert a.set("New@c.com", True) == "granted" and a.members() == ["a@b.com", "new@c.com"]
    assert a.set("a@b.com", False) == "removed" and a.members() == ["new@c.com"]
    assert any(b["role"] == "roles/iap.admin" for b in a.session.policy["bindings"])
