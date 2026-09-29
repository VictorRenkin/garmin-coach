from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import pytest

from garmin_coach import account_security, config, dashboard, user_accounts


@pytest.fixture
def isolated_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    monkeypatch.setattr(user_accounts, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_accounts, "SESSION_KEY_FILE", tmp_path / "session.key")
    monkeypatch.setattr(account_security, "OUTBOX_DIR", tmp_path / "outbox")
    return tmp_path


def _verified_ready_user(root: Path) -> tuple[str, str]:
    password = "correct-horse-battery-staple"
    account = user_accounts.register_account(
        "runner@example.com", password, terms_accepted=True
    )
    assert user_accounts.confirm_email(str(account["verificationToken"]))
    user_id = str(account["id"])
    target = root / "users" / user_id / "onboarding.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"status": "ready"}), encoding="utf-8")
    return user_id, password


def _write_tokens(root: Path, user_id: str) -> None:
    target = root / ".secrets" / "garmin_tokens" / user_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "oauth1_token.json").write_text(
        json.dumps({"oauth_token": "one", "oauth_token_secret": "secret"}),
        encoding="utf-8",
    )
    (target / "oauth2_token.json").write_text(
        json.dumps({"access_token": "two", "refresh_token": "refresh"}),
        encoding="utf-8",
    )


def _post(port: int, path: str, body: dict, cookie: str = "") -> tuple[int, dict, str]:
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:  # noqa: S310
            return response.status, json.loads(response.read()), response.headers.get("Set-Cookie", "")
    except HTTPError as error:
        return error.code, json.loads(error.read()), error.headers.get("Set-Cookie", "")


def test_modern_garmin_connection_uses_only_authenticated_motionest_user(
    isolated_setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id, password = _verified_ready_user(isolated_setup)
    captured: dict[str, str | None] = {}
    monkeypatch.setattr(dashboard, "authenticate_with_credentials", lambda *args: object())
    monkeypatch.setattr(
        dashboard,
        "persist_client_tokens",
        lambda client: captured.update(persisted_for=config.current_user_id()),
    )
    monkeypatch.setattr(
        dashboard,
        "start_initial_setup",
        lambda selected: captured.update(started_for=selected) or True,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, cookie = _post(
            server.server_port,
            "/api/account/login",
            {"email": "runner@example.com", "password": password},
        )
        assert status == 200
        status, payload, _ = _post(
            server.server_port,
            "/api/garmin/connect",
            {
                "email": "garmin@example.com",
                "password": "garmin-password",
                "user_id": "u_attacker",
            },
            cookie,
        )
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()

    assert status == 202
    assert payload["started"] is True
    assert captured == {"persisted_for": user_id, "started_for": user_id}


def test_dashboard_redirects_to_setup_until_plan_is_proposed(
    isolated_setup: Path,
) -> None:
    user_id, password = _verified_ready_user(isolated_setup)
    _write_tokens(isolated_setup, user_id)
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _, _, cookie = _post(
            server.server_port,
            "/api/account/login",
            {"email": "runner@example.com", "password": password},
        )

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        request = Request(
            f"http://127.0.0.1:{server.server_port}/dashboard",
            headers={"Cookie": cookie},
        )
        with pytest.raises(HTTPError) as error:
            build_opener(NoRedirect).open(request, timeout=3)
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()

    assert error.value.code == 302
    assert error.value.headers["Location"] == "/connect-garmin"
