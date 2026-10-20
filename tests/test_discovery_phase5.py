"""Explicit discovery reuses account and declarations without invented Garmin data."""
import json

import pytest

from garmin_coach import config, onboarding, first_run_journey, user_journey
from tests.test_user_journey import isolated_journey, _write_accounts, _write_onboarding, _write_tokens


@pytest.fixture(autouse=True)
def isolated(isolated_journey):
    return isolated_journey


def account(root, user="athlete", verified=True):
    _write_accounts(root, {"id": user, "email_verified": verified})
    _write_onboarding(root, user, "ready")


def test_no_garmin_requires_explicit_choice_and_preserves_default_journey(isolated):
    account(isolated)
    assert user_journey.determine_user_stage("athlete") == "connect_garmin"
    with config.user_context("athlete"):
        onboarding.enable_discovery_without_garmin()
    assert user_journey.determine_user_stage("athlete") == "dashboard"
    journey = first_run_journey.build_first_run_journey("athlete")
    assert journey["discovery_without_garmin"] is True
    assert journey["garmin_connected"] is False
    assert "connect_garmin" in journey["actions"]
    assert not (isolated / ".secrets" / "garmin_tokens").exists()


def test_discovery_does_not_override_email_verification(isolated):
    account(isolated, verified=False)
    with config.user_context("athlete"):
        onboarding.enable_discovery_without_garmin()
    assert user_journey.determine_user_stage("athlete") == "verify_email"


def test_discovery_choice_preserves_onboarding_profile_and_import_files(isolated):
    account(isolated)
    with config.user_context("athlete"):
        initial = onboarding.load_onboarding()
        initial.update(input_progress={"goal": True}, garmin_setup={"status": "error"})
        onboarding._write_onboarding(initial)
        config.PROFILE_FILE.write_text(json.dumps({"objective": {"title": "Mon objectif"}}), encoding="utf-8")
        before = config.PROFILE_FILE.read_bytes()
        result = onboarding.enable_discovery_without_garmin()
        assert result == dict(initial, discovery_without_garmin=True)
        assert config.PROFILE_FILE.read_bytes() == before
    assert not list((isolated / "users" / "athlete" / "activity_history").glob("*.json"))


@pytest.mark.parametrize("setup", ["idle", "error", "importing", "analysis_ready"])
def test_later_connection_uses_normal_garmin_stage_instead_of_discovery(isolated, setup):
    account(isolated)
    with config.user_context("athlete"):
        onboarding.enable_discovery_without_garmin()
        onboarding.update_garmin_setup(setup)
    _write_tokens(isolated, "athlete")
    # Legacy-ready account follows its existing setup rules, not the discovery flag.
    expected = "dashboard" if setup == "analysis_ready" else "connect_garmin"
    assert user_journey.determine_user_stage("athlete") == expected


def test_discovery_choice_is_scoped_to_its_account(isolated):
    _write_accounts(isolated, {"id": "alice", "email_verified": True}, {"id": "bob", "email_verified": True})
    for user in ("alice", "bob"):
        _write_onboarding(isolated, user, "ready")
    with config.user_context("alice"):
        onboarding.enable_discovery_without_garmin()
    assert user_journey.determine_user_stage("alice") == "dashboard"
    assert user_journey.determine_user_stage("bob") == "connect_garmin"
