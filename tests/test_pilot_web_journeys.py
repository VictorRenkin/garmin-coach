"""Missing pilot recovery seams; real HTTP, synthetic Garmin transport only."""
from pathlib import Path

import pytest

from garmin_coach import config, dashboard
from tests.test_discovery_phase5_quality import isolated, server, request, declare
from tests.test_user_journey import _write_tokens


@pytest.mark.parametrize("exception_name,status,marker", [
    ("GarminMFARequiredError", 409, "mfaRequired"),
    ("GarminConnectAuthenticationError", 401, None),
    ("GarminConnectConnectionError", 503, None),
    ("GarminConnectTooManyRequestsError", 429, None),
])
def test_late_connection_error_keeps_discovery_and_allows_explicit_retry(
    server, isolated, monkeypatch, exception_name, status, marker
):
    snapshots = declare("alice")
    assert request(server, "/api/onboarding/continue-without-garmin", user="alice", method="POST")[0] == 200
    calls = []
    client = object()

    def authenticate(email, password, mfa):
        calls.append((email, mfa))
        if len(calls) == 1:
            raise getattr(dashboard, exception_name)("SYNTHETIC_PRIVATE_PROVIDER_DETAIL")
        return client

    def persist(value):
        assert value is client
        _write_tokens(isolated, "alice")

    starts = []
    monkeypatch.setattr(dashboard, "authenticate_with_credentials", authenticate)
    monkeypatch.setattr(dashboard, "persist_client_tokens", persist)
    monkeypatch.setattr(dashboard, "start_initial_setup", lambda user: starts.append(user) or True)
    payload = {"email": "synthetic@example.invalid", "password": "SYNTHETIC_PRIVATE_PASSWORD", "historyScope": "all"}
    actual, _, body = request(server, "/api/garmin/connect", user="alice", method="POST", payload=payload)
    assert actual == status, body
    if marker:
        assert body[marker] is True
    assert "PRIVATE" not in str(body)
    assert starts == []
    with config.user_context("alice"):
        assert dashboard.coach_context()["connexion_sportive"] == {
            "garmin_connected": False, "discovery_without_garmin": True}
    assert request(server, "/dashboard", user="alice")[0] == 200
    assert request(server, "/connect-garmin", user="alice")[0] == 200
    assert len(calls) == 1  # Reading pages never retries authentication.
    payload["mfaCode"] = "123456"
    actual, _, body = request(server, "/api/garmin/connect", user="alice", method="POST", payload=payload)
    assert actual == 202, body
    assert calls == [("synthetic@example.invalid", None), ("synthetic@example.invalid", "123456")]
    assert starts == ["alice"]
    for path, before in snapshots.items():
        assert Path(path).read_bytes() == before
    with config.user_context("bob"):
        assert not dashboard.stored_tokens_available()
