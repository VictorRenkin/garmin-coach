from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from garminconnect import GarminConnectAuthenticationError, GarminConnectConnectionError

from garmin_coach import account_security, config, dashboard, manager, user_accounts


@pytest.fixture(autouse=True)
def isolated_web_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(user_accounts, "SESSION_KEY_FILE", tmp_path / "web_session.key")
    monkeypatch.setattr(manager, "ACCOUNT_FILE", tmp_path / "manager_account.json")
    monkeypatch.setattr(account_security, "OUTBOX_DIR", tmp_path / "outbox")


@pytest.fixture
def isolated_workspaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep account workspaces out of the developer's real local data."""
    users_root = tmp_path / "users"
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", users_root)
    monkeypatch.setattr(user_accounts, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(manager, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(manager, "USERS_DATA_DIR", users_root)
    return users_root


def _write_activity(user_id: str, activity_id: str, name: str) -> None:
    with config.user_context(user_id):
        config.ACTIVITIES_DIR.mkdir(parents=True, exist_ok=True)
        (config.ACTIVITIES_DIR / f"{activity_id}.json").write_text(
            json.dumps(
                {
                    "activityId": activity_id,
                    "activityName": name,
                    "activityType": {"typeKey": "running"},
                    "startTimeLocal": "2026-08-17 08:00:00",
                    "distance": 5000,
                    "duration": 1800,
                }
            ),
            encoding="utf-8",
        )


def _serve() -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _get(url: str, cookie: str | None = None) -> tuple[int, dict]:
    request = Request(url, headers={"Cookie": cookie} if cookie else {})
    try:
        with urlopen(request, timeout=3) as response:  # noqa: S310 - local server only
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def _post(url: str, payload: dict, cookie: str | None = None) -> tuple[int, dict, str]:
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:  # noqa: S310 - local server only
            return response.status, json.loads(response.read()), response.headers.get("Set-Cookie", "")
    except HTTPError as error:
        return error.code, json.loads(error.read()), error.headers.get("Set-Cookie", "")


def test_workspaces_and_activity_lists_are_isolated(isolated_workspaces: Path) -> None:
    _write_activity("u_alice", "101", "Course Alice")
    _write_activity("u_bob", "202", "Course Bob")

    with config.user_context("u_alice"):
        assert [item["id"] for item in dashboard.load_public_activities()] == ["101"]
        assert "Alice" in dashboard.load_public_activities()[0]["name"]
    with config.user_context("u_bob"):
        assert [item["id"] for item in dashboard.load_public_activities()] == ["202"]
        assert "Bob" in dashboard.load_public_activities()[0]["name"]

    assert (isolated_workspaces / "u_alice" / "activities" / "101.json").is_file()
    assert not (isolated_workspaces / "u_alice" / "activities" / "202.json").exists()


def test_dashboard_api_requires_session_and_never_crosses_accounts(isolated_workspaces: Path) -> None:
    _write_activity("u_alice", "101", "Course Alice")
    _write_activity("u_bob", "202", "Course Bob")
    alice_cookie = f"motionest_session={user_accounts.create_session('u_alice')}"
    bob_cookie = f"motionest_session={user_accounts.create_session('u_bob')}"
    server, thread = _serve()
    url = f"http://127.0.0.1:{server.server_port}/api/activities"
    try:
        status, anonymous = _get(url)
        assert status == 401
        assert anonymous["ok"] is False

        status, alice = _get(url, alice_cookie)
        assert status == 200
        assert [item["name"] for item in alice["activities"]] == ["Course Alice"]

        status, bob = _get(url, bob_cookie)
        assert status == 200
        assert [item["name"] for item in bob["activities"]] == ["Course Bob"]
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_manager_cookie_does_not_grant_access_to_athlete_api() -> None:
    # A manager session must not double as an athlete session.
    server, thread = _serve()
    try:
        status, payload = _get(
            f"http://127.0.0.1:{server.server_port}/api/activities",
            "motionest_manager=not-a-user-session",
        )
        assert status == 401
        assert payload["ok"] is False
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_athlete_session_does_not_grant_access_to_manager_stats() -> None:
    server, thread = _serve()
    try:
        athlete_cookie = f"motionest_session={user_accounts.create_session('u_alice')}"
        status, payload = _get(
            f"http://127.0.0.1:{server.server_port}/api/manager/stats", athlete_cookie
        )
        assert status == 401
        assert payload["ok"] is False
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_signed_athlete_session_survives_process_memory_and_rejects_tampering() -> None:
    token = user_accounts.create_session("u_restart_safe")

    assert user_accounts.user_from_cookie(f"motionest_session={token}") == "u_restart_safe"
    replacement = "A" if token[-1] != "A" else "B"
    assert user_accounts.user_from_cookie(
        f"motionest_session={token[:-1]}{replacement}"
    ) is None


def test_logout_endpoints_expire_only_the_expected_session_cookie() -> None:
    server, thread = _serve()
    try:
        status, payload, athlete_cookie = _post(
            f"http://127.0.0.1:{server.server_port}/api/logout", {}
        )
        assert status == 200
        assert payload["redirect"] == "/"
        assert athlete_cookie.startswith("motionest_session=;")
        assert "Max-Age=0" in athlete_cookie

        status, payload, manager_cookie = _post(
            f"http://127.0.0.1:{server.server_port}/api/manager/logout", {}
        )
        assert status == 200
        assert payload["redirect"] == "/"
        assert manager_cookie.startswith("motionest_manager=;")
        assert "Max-Age=0" in manager_cookie
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_motionest_account_registration_and_login_are_independent_from_garmin(
    isolated_workspaces: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def garmin_must_not_be_called(*args, **kwargs):
        raise AssertionError("Garmin ne doit pas authentifier un compte Motionest")

    monkeypatch.setattr(dashboard, "authenticate_with_credentials", garmin_must_not_be_called)
    server, thread = _serve()
    try:
        status, payload, set_cookie = _post(
            f"http://127.0.0.1:{server.server_port}/api/account/register",
            {
                "email": "alice@example.com",
                "password": "correct-horse-battery-staple",
                "passwordConfirm": "correct-horse-battery-staple",
                "termsAccepted": True,
                "turnstileToken": "development-pass",
                "company": "",
            },
        )
        assert status == 201
        assert payload["verificationPending"] is True
        assert not set_cookie
        assert "token=" in payload["developmentVerificationUrl"]
        stored = json.loads(user_accounts.USERS_FILE.read_text(encoding="utf-8"))[0]
        assert stored["password_hash"] != "correct-horse-battery-staple"
        assert "correct-horse-battery-staple" not in json.dumps(stored)

        status, payload, _ = _post(
            f"http://127.0.0.1:{server.server_port}/api/account/login",
            {
                "email": "alice@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        assert status == 403
        assert payload["verificationPending"] is True

        refreshed = user_accounts.issue_email_verification("alice@example.com")
        assert refreshed is not None
        verification_token = refreshed[1]
        assert user_accounts.confirm_email(verification_token)

        status, payload, _ = _post(
            f"http://127.0.0.1:{server.server_port}/api/account/login",
            {"email": "alice@example.com", "password": "wrong-password"},
        )
        assert status == 401
        assert payload["ok"] is False

        status, payload, login_cookie = _post(
            f"http://127.0.0.1:{server.server_port}/api/account/login",
            {
                "email": "ALICE@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        assert status == 200
        assert payload["created"] is False
        assert login_cookie.startswith("motionest_session=")
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_loopback_can_create_account_without_turnstile_in_local_testing(
    isolated_workspaces: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOTIONEST_ENV", "production")
    server, thread = _serve()
    try:
        status, auth_config = _get(
            f"http://127.0.0.1:{server.server_port}/api/public/auth-config"
        )
        assert status == 200
        assert auth_config["turnstileRequired"] is False

        status, payload, _ = _post(
            f"http://127.0.0.1:{server.server_port}/api/account/register",
            {
                "email": "local@example.com",
                "password": "correct-horse-battery-staple",
                "passwordConfirm": "correct-horse-battery-staple",
                "termsAccepted": True,
                "turnstileToken": "",
                "company": "",
            },
        )
        assert status == 201
        assert payload["verificationPending"] is True
        assert "token=" in payload["developmentVerificationUrl"]
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_explicit_local_mode_creates_verified_session_without_email(
    isolated_workspaces: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOTIONEST_LOCAL_AUTH_BYPASS", "true")

    def email_must_not_be_sent(*args, **kwargs):
        raise AssertionError("Le mode local ne doit pas envoyer d'e-mail.")

    monkeypatch.setattr(dashboard, "send_account_email", email_must_not_be_sent)
    server, thread = _serve()
    url = f"http://127.0.0.1:{server.server_port}/api/account/register"
    request = {
        "email": "local-flow@example.com",
        "password": "correct-horse-battery-staple",
        "passwordConfirm": "correct-horse-battery-staple",
        "termsAccepted": True,
        "turnstileToken": "",
        "company": "",
    }
    try:
        status, payload, cookie = _post(url, request)

        assert status == 201
        assert payload["created"] is True
        assert payload["verificationPending"] is False
        assert payload["nextStep"] == "onboarding"
        assert payload["redirect"] == "/dashboard"
        assert cookie.startswith("motionest_session=")
        stored = json.loads(user_accounts.USERS_FILE.read_text(encoding="utf-8"))[0]
        assert stored["email_verified"] is True
        assert "email_verification_hash" not in stored

        duplicate_status, duplicate_payload, duplicate_cookie = _post(url, request)
        assert duplicate_status == 400
        assert "existe déjà" in duplicate_payload["message"]
        assert not duplicate_cookie
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_explicit_local_mode_verifies_existing_account_on_login(
    isolated_workspaces: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOTIONEST_LOCAL_AUTH_BYPASS", "true")
    password = "correct-horse-battery-staple"
    user_accounts.register_account(
        "existing-local@example.com", password, terms_accepted=True
    )

    def email_must_not_be_sent(*args, **kwargs):
        raise AssertionError("Le mode local ne doit pas envoyer d'e-mail.")

    monkeypatch.setattr(dashboard, "send_account_email", email_must_not_be_sent)
    server, thread = _serve()
    try:
        status, payload, cookie = _post(
            f"http://127.0.0.1:{server.server_port}/api/account/login",
            {"email": "existing-local@example.com", "password": password},
        )

        assert status == 200
        assert payload["created"] is False
        assert payload["nextStep"] == "onboarding"
        assert payload["redirect"] == "/dashboard"
        assert cookie.startswith("motionest_session=")
        stored = json.loads(user_accounts.USERS_FILE.read_text(encoding="utf-8"))[0]
        assert stored["email_verified"] is True
        assert "email_verification_hash" not in stored
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_motionest_registration_rejects_duplicate_and_short_password(
    isolated_workspaces: Path,
) -> None:
    user_accounts.register_account(
        "runner@example.com", "long-enough-password", terms_accepted=True
    )

    with pytest.raises(ValueError, match="existe déjà"):
        user_accounts.register_account(
            "RUNNER@example.com", "another-long-password", terms_accepted=True
        )
    with pytest.raises(ValueError, match="au moins"):
        user_accounts.register_account("new@example.com", "short", terms_accepted=True)


def test_registration_requires_matching_passwords_terms_and_human_check(
    isolated_workspaces: Path,
) -> None:
    server, thread = _serve()
    url = f"http://127.0.0.1:{server.server_port}/api/account/register"
    base = {
        "email": "runner@example.com",
        "password": "long-enough-password",
        "passwordConfirm": "long-enough-password",
        "termsAccepted": True,
        "turnstileToken": "development-pass",
        "company": "",
    }
    try:
        status, payload, _ = _post(url, {**base, "passwordConfirm": "different-password"})
        assert status == 400
        assert "correspondent" in payload["message"]

        status, payload, _ = _post(url, {**base, "termsAccepted": False})
        assert status == 400
        assert "conditions" in payload["message"]

        status, payload, _ = _post(url, {**base, "turnstileToken": "invalid"})
        assert status == 400
        assert "anti-robot" in payload["message"]

        status, payload, _ = _post(url, {**base, "company": "bot-filled-field"})
        assert status == 400
        assert "anti-robot" in payload["message"]
        assert not user_accounts.USERS_FILE.exists()
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_email_confirmation_and_password_reset_tokens_are_single_use(
    isolated_workspaces: Path,
) -> None:
    account = user_accounts.register_account(
        "runner@example.com", "initial-long-password", terms_accepted=True
    )
    verification = str(account["verificationToken"])
    assert user_accounts.confirm_email(verification)
    assert not user_accounts.confirm_email(verification)

    issued = user_accounts.issue_password_reset("runner@example.com")
    assert issued is not None
    _, reset_token = issued
    assert user_accounts.reset_password(reset_token, "replacement-password")
    assert not user_accounts.reset_password(reset_token, "another-replacement")
    assert user_accounts.authenticate_account(
        "runner@example.com", "replacement-password"
    )
    assert user_accounts.authenticate_account(
        "runner@example.com", "initial-long-password"
    ) is None


def test_two_full_logins_create_isolated_accounts_and_manager_aggregates_them(
    isolated_workspaces: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeClient:
        pass

    monkeypatch.setattr(
        dashboard, "authenticate_with_credentials", lambda email, password: FakeClient()
    )
    monkeypatch.setattr(dashboard, "persist_client_tokens", lambda client: None)
    monkeypatch.setattr(dashboard, "load_onboarding", lambda: {"status": "not_started"})
    monkeypatch.setattr(dashboard, "inspect_garmin_account", lambda client: {"status": "needs_profile"})

    def fake_sync(client, **kwargs):
        user_id = config.current_user_id()
        assert user_id
        activity_id = "101" if "alice" in current_email[0] else "202"
        _write_activity(user_id, activity_id, f"Private session {activity_id}")
        return [Path("created.json")]

    current_email = [""]
    monkeypatch.setattr(dashboard, "sync_once", fake_sync)
    server, thread = _serve()
    try:
        cookies: dict[str, str] = {}
        for email in ("alice@example.com", "bob@example.com"):
            current_email[0] = email
            status, payload, set_cookie = _post(
                f"http://127.0.0.1:{server.server_port}/api/login",
                {"email": email, "password": "synthetic-password"},
            )
            assert status == 200
            assert payload["role"] == "athlete"
            cookies[email] = set_cookie.split(";", 1)[0]

        status, alice = _get(
            f"http://127.0.0.1:{server.server_port}/api/activities",
            cookies["alice@example.com"],
        )
        assert status == 200
        assert [item["name"] for item in alice["activities"]] == ["Private session 101"]

        status, bob = _get(
            f"http://127.0.0.1:{server.server_port}/api/activities",
            cookies["bob@example.com"],
        )
        assert status == 200
        assert [item["name"] for item in bob["activities"]] == ["Private session 202"]

        stats = manager.manager_stats()
        assert stats["userCount"] == 2
        assert stats["activityCount"] == 2
        # Les statistiques manager n'incluent pas le contenu privé des activités.
        assert "Private session 101" not in json.dumps(stats)
        assert "Private session 202" not in json.dumps(stats)
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_message"),
    [
        (
            GarminConnectAuthenticationError("bad credentials"),
            401,
            "mot de passe Garmin incorrect",
        ),
        (
            GarminConnectConnectionError("network unavailable"),
            503,
            "momentanément indisponible",
        ),
    ],
)
def test_login_distinguishes_bad_credentials_from_garmin_outage(
    isolated_workspaces: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_status: int,
    expected_message: str,
) -> None:
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(dashboard, "authenticate_with_credentials", fail)
    server, thread = _serve()
    try:
        status, payload, cookie = _post(
            f"http://127.0.0.1:{server.server_port}/api/login",
            {"email": "runner@example.com", "password": "wrong-or-network"},
        )
        assert status == expected_status
        assert expected_message in payload["message"]
        assert not cookie
        assert not user_accounts.USERS_FILE.exists()
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_manager_login_uses_same_endpoint_and_survives_restart_style_validation(
    isolated_workspaces: Path,
) -> None:
    manager.set_manager_account(manager.MANAGER_EMAIL, "synthetic-admin-password")
    server, thread = _serve()
    try:
        status, payload, set_cookie = _post(
            f"http://127.0.0.1:{server.server_port}/api/login",
            {
                "email": manager.MANAGER_EMAIL,
                "password": "synthetic-admin-password",
            },
        )
        assert status == 200
        assert payload["role"] == "manager"
        cookie = set_cookie.split(";", 1)[0]
        assert manager.manager_authorized(cookie)
        status, stats = _get(
            f"http://127.0.0.1:{server.server_port}/api/manager/stats", cookie
        )
        assert status == 200
        assert stats["userCount"] == 0
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
