from pathlib import Path

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from garth.exc import GarthException

from garmin_coach import garmin
from garmin_coach.config import Settings


class FakeGarth:
    def __init__(self) -> None:
        self.dumped_to: str | None = None

    def dump(self, target: str) -> None:
        self.dumped_to = target


class FakeGarmin:
    instances: list["FakeGarmin"] = []

    def __init__(self, email=None, password=None, prompt_mfa=None) -> None:
        self.email = email
        self.password = password
        self.prompt_mfa = prompt_mfa
        self.login_tokenstore: str | None = "not-called"
        self.garth = FakeGarth()
        self.instances.append(self)

    def login(self, tokenstore=None) -> None:
        self.login_tokenstore = tokenstore


def test_first_login_authenticates_before_saving_tokens(tmp_path: Path, monkeypatch) -> None:
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()  # Reproduit le dossier vide qui causait l'erreur.
    FakeGarmin.instances.clear()
    monkeypatch.setattr(garmin, "TOKEN_DIR", token_dir)
    monkeypatch.setattr(garmin, "Garmin", FakeGarmin)
    monkeypatch.setattr(garmin, "ensure_directories", lambda: None)

    client = garmin.connect(Settings("runner@example.com", "secret", 300))

    assert client.login_tokenstore is None
    assert client.garth.dumped_to == str(token_dir)


def test_complete_tokens_are_reused(tmp_path: Path, monkeypatch) -> None:
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    (token_dir / "oauth1_token.json").write_text("{}", encoding="utf-8")
    (token_dir / "oauth2_token.json").write_text("{}", encoding="utf-8")
    FakeGarmin.instances.clear()
    monkeypatch.setattr(garmin, "TOKEN_DIR", token_dir)
    monkeypatch.setattr(garmin, "Garmin", FakeGarmin)
    monkeypatch.setattr(garmin, "ensure_directories", lambda: None)

    client = garmin.connect(Settings(None, None, 300))

    assert client.login_tokenstore == str(token_dir)
    assert client.email is None


def test_sign_in_page_after_submit_is_reported_as_bad_credentials(monkeypatch) -> None:
    class RejectedGarmin(FakeGarmin):
        def login(self, tokenstore=None) -> None:
            cause = GarthException("Unexpected title: Garmin: Sign In")
            raise GarminConnectConnectionError(f"Login failed: {cause}") from cause

    monkeypatch.setattr(garmin, "Garmin", RejectedGarmin)

    with pytest.raises(GarminConnectAuthenticationError):
        garmin.authenticate_with_credentials("runner@example.com", "wrong")


def test_real_connection_failure_is_not_reported_as_bad_credentials(monkeypatch) -> None:
    class OfflineGarmin(FakeGarmin):
        def login(self, tokenstore=None) -> None:
            raise GarminConnectConnectionError("Login failed: service unavailable")

    monkeypatch.setattr(garmin, "Garmin", OfflineGarmin)

    with pytest.raises(GarminConnectConnectionError):
        garmin.authenticate_with_credentials("runner@example.com", "secret")


def test_web_login_supplies_the_received_mfa_code(monkeypatch) -> None:
    class MFAGarmin(FakeGarmin):
        def login(self, tokenstore=None) -> None:
            assert self.prompt_mfa() == "123456"

    monkeypatch.setattr(garmin, "Garmin", MFAGarmin)

    client = garmin.authenticate_with_credentials(
        "runner@example.com", "secret", " 123456 "
    )

    assert isinstance(client, MFAGarmin)


def test_embedded_429_is_reported_as_rate_limit(monkeypatch) -> None:
    class RateLimitedGarmin(FakeGarmin):
        def login(self, tokenstore=None) -> None:
            cause = GarthException(
                "Error in request: 429 Client Error: Too Many Requests"
            )
            raise GarminConnectConnectionError(f"Login failed: {cause}") from cause

    monkeypatch.setattr(garmin, "Garmin", RateLimitedGarmin)

    with pytest.raises(GarminConnectTooManyRequestsError):
        garmin.authenticate_with_credentials("runner@example.com", "secret")
