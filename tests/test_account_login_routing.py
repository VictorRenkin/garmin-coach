from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from garmin_coach import account_security, config, dashboard, user_accounts


@pytest.fixture
def isolated_accounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    users_root = tmp_path / "users"
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", users_root)
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    monkeypatch.setattr(user_accounts, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_accounts, "SESSION_KEY_FILE", tmp_path / "session.key")
    monkeypatch.setattr(account_security, "OUTBOX_DIR", tmp_path / "outbox")
    return tmp_path


def _verified_account(email: str = "runner@example.com") -> tuple[str, str]:
    password = "correct-horse-battery-staple"
    account = user_accounts.register_account(email, password, terms_accepted=True)
    assert user_accounts.confirm_email(str(account["verificationToken"]))
    return str(account["id"]), password


def _write_onboarding(root: Path, user_id: str, status: str) -> None:
    target = root / "users" / user_id / "onboarding.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"status": status}), encoding="utf-8")


def _write_tokens(root: Path, user_id: str) -> None:
    target = root / ".secrets" / "garmin_tokens" / user_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "oauth1_token.json").write_text(
        json.dumps(
            {"oauth_token": "oauth1-token", "oauth_token_secret": "oauth1-secret"}
        ),
        encoding="utf-8",
    )
    (target / "oauth2_token.json").write_text(
        json.dumps(
            {"access_token": "access-token", "refresh_token": "refresh-token"}
        ),
        encoding="utf-8",
    )


def _login(email: str, password: str) -> tuple[int, dict, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = Request(
        f"http://127.0.0.1:{server.server_port}/api/account/login",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        try:
            with urlopen(request, timeout=3) as response:  # noqa: S310
                return (
                    response.status,
                    json.loads(response.read()),
                    response.headers.get("Set-Cookie", ""),
                )
        except HTTPError as error:
            return (
                error.code,
                json.loads(error.read()),
                error.headers.get("Set-Cookie", ""),
            )
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_login_routes_new_user_to_existing_onboarding(
    isolated_accounts: Path,
) -> None:
    _, password = _verified_account()

    status, payload, cookie = _login("runner@example.com", password)

    assert status == 200
    assert payload["nextStep"] == "onboarding"
    assert payload["redirect"] == "/dashboard"
    assert cookie.startswith("motionest_session=")


def test_login_routes_completed_onboarding_to_future_garmin_step(
    isolated_accounts: Path,
) -> None:
    user_id, password = _verified_account()
    _write_onboarding(isolated_accounts, user_id, "complete")

    status, payload, _ = _login("runner@example.com", password)

    assert status == 200
    assert payload["nextStep"] == "connect_garmin"
    assert payload["redirect"] == "/connect-garmin"


def test_login_routes_ready_user_to_dashboard(isolated_accounts: Path) -> None:
    user_id, password = _verified_account()
    _write_onboarding(isolated_accounts, user_id, "complete")
    _write_tokens(isolated_accounts, user_id)

    status, payload, _ = _login("runner@example.com", password)

    assert status == 200
    assert payload["nextStep"] == "dashboard"
    assert payload["redirect"] == "/dashboard"


def test_login_keeps_unverified_user_in_verification_flow(
    isolated_accounts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MOTIONEST_ENV", raising=False)
    password = "correct-horse-battery-staple"
    user_accounts.register_account(
        "runner@example.com", password, terms_accepted=True
    )

    status, payload, cookie = _login("runner@example.com", password)

    assert status == 403
    assert payload["nextStep"] == "verify_email"
    assert payload["redirect"] == "/"
    assert payload["verificationPending"] is True
    assert payload["developmentVerificationUrl"].startswith(
        "http://127.0.0.1:8765/verify-email?token="
    )
    assert not cookie


def test_production_login_never_exposes_verification_token(
    isolated_accounts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOTIONEST_ENV", "production")
    password = "correct-horse-battery-staple"
    user_accounts.register_account(
        "runner@example.com", password, terms_accepted=True
    )

    status, payload, _ = _login("runner@example.com", password)

    assert status == 403
    assert payload["verificationPending"] is True
    assert "developmentVerificationUrl" not in payload


def test_relogin_never_resets_completed_onboarding(isolated_accounts: Path) -> None:
    user_id, password = _verified_account()
    _write_onboarding(isolated_accounts, user_id, "complete")
    _write_tokens(isolated_accounts, user_id)

    for _ in range(2):
        status, payload, _ = _login("runner@example.com", password)
        assert status == 200
        assert payload["nextStep"] == "dashboard"

    stored = json.loads(user_accounts.USERS_FILE.read_text(encoding="utf-8"))[0]
    assert stored["onboarding_complete"] is True
    onboarding = json.loads(
        (isolated_accounts / "users" / user_id / "onboarding.json").read_text(
            encoding="utf-8"
        )
    )
    assert onboarding["status"] == "complete"
