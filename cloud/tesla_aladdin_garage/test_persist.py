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
    from google.cloud.firestore import DELETE_FIELD

    fake.collection.assert_called_with("tesla_aladdin_garage")
    fake.collection.return_value.document.assert_called_with("config")
    kwargs = fake.collection.return_value.document.return_value.set.call_args.kwargs
    payload = fake.collection.return_value.document.return_value.set.call_args.args[0]
    assert payload["enter_m"] is DELETE_FIELD
    assert payload["hysteresis_m"] is DELETE_FIELD
    assert kwargs.get("merge") is True
    persist.set_client(None)
