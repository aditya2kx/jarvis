"""Firestore client construction — never pass database=(default) (REST double-encode)."""

from unittest.mock import MagicMock

from cloud.tesla_aladdin_garage import persist


def test_db_defaults_to_named_garage(monkeypatch):
    persist.set_client(None)
    monkeypatch.setenv("GARAGE_PERSIST", "1")
    monkeypatch.setenv("GCP_PROJECT", "jarvis-bhaga-prod")
    monkeypatch.delenv("FIRESTORE_DB", raising=False)
    monkeypatch.delenv("GARAGE_FIRESTORE_DB", raising=False)
    fake = MagicMock()
    monkeypatch.setattr("google.cloud.firestore.Client", fake)
    persist._db()
    kwargs = fake.call_args.kwargs
    assert kwargs.get("project") == "jarvis-bhaga-prod"
    assert kwargs.get("database") == "garage"
    persist.set_client(None)


def test_db_garage_env_wins(monkeypatch):
    persist.set_client(None)
    monkeypatch.setenv("GARAGE_PERSIST", "1")
    monkeypatch.setenv("GCP_PROJECT", "jarvis-bhaga-prod")
    monkeypatch.setenv("FIRESTORE_DB", "other")
    monkeypatch.setenv("GARAGE_FIRESTORE_DB", "garage")
    fake = MagicMock()
    monkeypatch.setattr("google.cloud.firestore.Client", fake)
    persist._db()
    assert fake.call_args.kwargs["database"] == "garage"
    persist.set_client(None)


def test_db_passes_named_database(monkeypatch):
    persist.set_client(None)
    monkeypatch.setenv("GARAGE_PERSIST", "1")
    monkeypatch.setenv("GCP_PROJECT", "jarvis-bhaga-prod")
    monkeypatch.setenv("FIRESTORE_DB", "named-db")
    fake = MagicMock()
    monkeypatch.setattr("google.cloud.firestore.Client", fake)
    persist._db()
    assert fake.call_args.kwargs["database"] == "named-db"
    persist.set_client(None)


def test_db_strips_encoded_default_alias(monkeypatch):
    persist.set_client(None)
    monkeypatch.setenv("GARAGE_PERSIST", "1")
    monkeypatch.setenv("FIRESTORE_DB", "(default)")
    fake = MagicMock()
    monkeypatch.setattr("google.cloud.firestore.Client", fake)
    persist._db()
    assert "database" not in fake.call_args.kwargs
    persist.set_client(None)


def test_clear_geofence_overlay_deletes_radius_keys(monkeypatch):
    monkeypatch.setenv("GARAGE_PERSIST", "1")
    fake = MagicMock()
    persist.set_client(fake)
    persist.clear_geofence_overlay()
    fake.collection.assert_called_with("tesla_aladdin_garage")
    persist.set_client(None)
