from __future__ import annotations

import json
from pathlib import Path

import pytest

from garmin_coach import config, user_accounts
from garmin_coach.goal import build_goal_v1
from garmin_coach.runtime_context import update_motionest_inputs_v1
from garmin_coach.user_journey import determine_user_stage


@pytest.fixture
def isolated_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    users_root = tmp_path / "users"
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", users_root)
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    monkeypatch.setattr(user_accounts, "USERS_FILE", tmp_path / "users.json")
    return tmp_path


def _write_accounts(root: Path, *accounts: dict[str, object]) -> None:
    (root / "users.json").write_text(json.dumps(accounts), encoding="utf-8")


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


def test_unverified_email_requires_verification(isolated_journey: Path) -> None:
    _write_accounts(
        isolated_journey,
        {"id": "u_runner", "email_verified": False},
    )
    _write_onboarding(isolated_journey, "u_runner", "complete")
    _write_tokens(isolated_journey, "u_runner")

    assert determine_user_stage("u_runner") == "verify_email"


def test_verified_email_without_onboarding_starts_onboarding(
    isolated_journey: Path,
) -> None:
    _write_accounts(
        isolated_journey,
        {"id": "u_runner", "email_verified": True},
    )

    assert determine_user_stage("u_runner") == "onboarding"


@pytest.mark.parametrize(
    "status",
    ["needs_profile"],
)
def test_incomplete_onboarding_stays_in_onboarding(
    isolated_journey: Path, status: str
) -> None:
    _write_accounts(
        isolated_journey,
        {"id": "u_runner", "email_verified": True},
    )
    _write_onboarding(isolated_journey, "u_runner", status)
    _write_tokens(isolated_journey, "u_runner")

    assert determine_user_stage("u_runner") == "onboarding"


def test_ready_with_garmin_stays_in_setup_until_plan_is_proposed(
    isolated_journey: Path,
) -> None:
    _write_accounts(
        isolated_journey,
        {"id": "u_runner", "email_verified": True},
    )
    _write_onboarding(isolated_journey, "u_runner", "ready")
    _write_tokens(isolated_journey, "u_runner")

    assert determine_user_stage("u_runner") == "connect_garmin"


@pytest.mark.parametrize("status", ["waiting_existing_plan", "plan_proposed"])
def test_existing_or_proposed_plan_opens_dashboard(
    isolated_journey: Path, status: str
) -> None:
    _write_accounts(
        isolated_journey,
        {"id": "u_runner", "email_verified": True},
    )
    _write_onboarding(isolated_journey, "u_runner", status)
    _write_tokens(isolated_journey, "u_runner")

    assert determine_user_stage("u_runner") == "dashboard"


def test_rejected_plan_with_garmin_returns_to_dashboard(
    isolated_journey: Path,
) -> None:
    _write_accounts(
        isolated_journey,
        {"id": "u_runner", "email_verified": True},
    )
    target = isolated_journey / "users" / "u_runner" / "onboarding.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {"status": "ready", "garmin_setup": {"status": "plan_rejected"}}
        ),
        encoding="utf-8",
    )
    _write_tokens(isolated_journey, "u_runner")

    assert determine_user_stage("u_runner") == "dashboard"


def test_complete_onboarding_without_garmin_requires_connection(
    isolated_journey: Path,
) -> None:
    _write_accounts(
        isolated_journey,
        {"id": "u_runner", "email_verified": True},
    )
    _write_onboarding(isolated_journey, "u_runner", "complete")

    assert determine_user_stage("u_runner") == "connect_garmin"


def test_complete_onboarding_with_garmin_opens_dashboard(
    isolated_journey: Path,
) -> None:
    _write_accounts(
        isolated_journey,
        {"id": "u_runner", "email_verified": True},
    )
    _write_onboarding(isolated_journey, "u_runner", "complete")
    _write_tokens(isolated_journey, "u_runner")

    assert determine_user_stage("u_runner") == "dashboard"


@pytest.mark.parametrize("setup_status", ["importing", "analyzing", "error"])
def test_ready_user_stays_in_garmin_step_until_analysis_ready(
    isolated_journey: Path, setup_status: str
) -> None:
    _write_accounts(isolated_journey, {"id": "u_runner", "email_verified": True})
    target = isolated_journey / "users" / "u_runner" / "onboarding.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"status": "ready", "garmin_setup": {"status": setup_status}}),
        encoding="utf-8",
    )
    _write_tokens(isolated_journey, "u_runner")
    assert determine_user_stage("u_runner") == "connect_garmin"


def test_analysis_ready_opens_dashboard(isolated_journey: Path) -> None:
    _write_accounts(isolated_journey, {"id": "u_runner", "email_verified": True})
    target = isolated_journey / "users" / "u_runner" / "onboarding.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"status": "ready", "garmin_setup": {"status": "analysis_ready"}}),
        encoding="utf-8",
    )
    _write_tokens(isolated_journey, "u_runner")
    assert determine_user_stage("u_runner") == "dashboard"


def test_v2_analysis_ready_requires_post_import_review(
    isolated_journey: Path,
) -> None:
    _write_accounts(isolated_journey, {"id": "u_runner", "email_verified": True})
    update_motionest_inputs_v1("u_runner", goal=build_goal_v1("beginner"))
    target = isolated_journey / "users" / "u_runner" / "onboarding.json"
    target.write_text(
        json.dumps(
            {
                "onboarding_version": 2,
                "status": "ready_for_garmin",
                "garmin_setup": {"status": "analysis_ready"},
            }
        ),
        encoding="utf-8",
    )
    _write_tokens(isolated_journey, "u_runner")

    assert determine_user_stage("u_runner") == "review_garmin"
    assert determine_user_stage("u_runner") == "review_garmin"


def test_garmin_tokens_are_isolated_between_users(isolated_journey: Path) -> None:
    _write_accounts(
        isolated_journey,
        {"id": "u_alice", "email_verified": True},
        {"id": "u_bob", "email_verified": True},
    )
    _write_onboarding(isolated_journey, "u_alice", "complete")
    _write_onboarding(isolated_journey, "u_bob", "complete")
    _write_tokens(isolated_journey, "u_alice")

    assert determine_user_stage("u_alice") == "dashboard"
    assert determine_user_stage("u_bob") == "connect_garmin"


def test_missing_or_corrupt_data_has_safe_deterministic_stages(
    isolated_journey: Path,
) -> None:
    assert determine_user_stage("u_missing") == "verify_email"

    _write_accounts(
        isolated_journey,
        {"id": "u_runner", "email_verified": True},
    )
    onboarding = isolated_journey / "users" / "u_runner" / "onboarding.json"
    onboarding.parent.mkdir(parents=True, exist_ok=True)
    onboarding.write_text("not-json", encoding="utf-8")
    assert determine_user_stage("u_runner") == "onboarding"

    _write_onboarding(isolated_journey, "u_runner", "complete")
    token_dir = isolated_journey / ".secrets" / "garmin_tokens" / "u_runner"
    token_dir.mkdir(parents=True, exist_ok=True)
    (token_dir / "oauth1_token.json").write_text("not-json", encoding="utf-8")
    (token_dir / "oauth2_token.json").write_text("{}", encoding="utf-8")
    assert determine_user_stage("u_runner") == "connect_garmin"
