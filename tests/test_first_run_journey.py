import json
import threading
import urllib.request
import urllib.error
from pathlib import Path

import pytest

from garmin_coach import config, dashboard, first_run_journey, user_accounts
from garmin_coach.availability import build_availability_v1
from garmin_coach.config import user_context
from garmin_coach.first_run_journey import (
    build_first_run_journey,
    confirm_first_run_step,
)
from garmin_coach.goal import build_goal_v1
from garmin_coach.runtime_context import update_motionest_inputs_v1
from garmin_coach.subjective_context import build_subjective_context_v1


@pytest.fixture
def isolated_first_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    monkeypatch.setattr(user_accounts, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(
        user_accounts, "SESSION_KEY_FILE", tmp_path / ".secrets" / "session.key"
    )
    return tmp_path


def _tokens(root: Path, user_id: str) -> None:
    target = root / ".secrets" / "garmin_tokens" / user_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "oauth1_token.json").write_text(
        json.dumps({"oauth_token": "token", "oauth_token_secret": "secret"}),
        encoding="utf-8",
    )
    (target / "oauth2_token.json").write_text(
        json.dumps({"access_token": "access", "refresh_token": "refresh"}),
        encoding="utf-8",
    )


def _onboarding(root: Path, user_id: str, setup_status: str) -> None:
    target = root / "users" / user_id / "onboarding.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "onboarding_version": 2,
                "status": "ready_for_garmin",
                "garmin_setup": {"status": setup_status},
            }
        ),
        encoding="utf-8",
    )


def _goal(user_id: str, intent: str = "beginner") -> None:
    update_motionest_inputs_v1(user_id, goal=build_goal_v1(intent))


def _declarations(user_id: str, *, preference: int | None = 4) -> None:
    update_motionest_inputs_v1(
        user_id,
        availability=build_availability_v1(
            ["mon", "tue", "thu", "sat"],
            preferred_sessions_per_week=preference,
        ),
        subjective_context=build_subjective_context_v1(
            affecting_training=False,
            unrecorded_activities=[
                {
                    "sport": "musculation",
                    "sessions_per_week": 2,
                    "typical_duration_minutes": 60,
                }
            ],
        ),
    )


def test_first_run_is_resumable_from_import_to_goal_assessment(
    isolated_first_run: Path,
) -> None:
    user_id = "u_first_run"
    assert build_first_run_journey(user_id)["stage"] == "initial_intent"

    _goal(user_id)
    assert build_first_run_journey(user_id)["stage"] == "connect_garmin"

    _tokens(isolated_first_run, user_id)
    _onboarding(isolated_first_run, user_id, "importing")
    assert build_first_run_journey(user_id)["stage"] == "importing"

    _onboarding(isolated_first_run, user_id, "analysis_ready")
    observed = build_first_run_journey(user_id)
    assert observed["stage"] == "review_garmin"
    assert observed["observations"]["provenance"] == "measured_and_deterministic"

    assert confirm_first_run_step(user_id, "observations")["stage"] == "complete_declarations"
    _declarations(user_id)
    understood = build_first_run_journey(user_id)
    assert understood["stage"] == "review_understanding"
    assert understood["declarations"]["goal"]["provenance"] == "declared"
    assert understood["declarations"]["subjective_context"][
        "unrecorded_activities"
    ][0]["source"] == "user_reported_unrecorded"

    assessed = confirm_first_run_step(user_id, "understanding")
    assert assessed["stage"] == "goal_assessment"
    assert build_first_run_journey(user_id)["stage"] == "goal_assessment"


def test_import_failure_is_explicit_and_retryable(isolated_first_run: Path) -> None:
    user_id = "u_failed_import"
    _goal(user_id)
    _tokens(isolated_first_run, user_id)
    _onboarding(isolated_first_run, user_id, "error")

    state = build_first_run_journey(user_id)

    assert state["stage"] == "import_failed"
    assert state["actions"] == ["retry_import"]


def test_ready_for_plan_requires_current_decision(
    isolated_first_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = "u_decision_gate"
    _goal(user_id)
    _declarations(user_id)
    _tokens(isolated_first_run, user_id)
    _onboarding(isolated_first_run, user_id, "analysis_ready")
    confirm_first_run_step(user_id, "observations")
    confirm_first_run_step(user_id, "understanding")
    monkeypatch.setattr(
        first_run_journey,
        "_safe_assessment_state",
        lambda *args: {"status": "available", "assessment": {"assessment": "not_applicable"}},
    )
    monkeypatch.setattr(
        first_run_journey,
        "_safe_recommendation_state",
        lambda *args: {
            "status": "available",
            "input_fingerprint": "a" * 64,
            "recommendation": {"recommended_sessions_per_week": 3},
        },
    )
    monkeypatch.setattr(
        first_run_journey,
        "_safe_decision_state",
        lambda *args: {"status": "missing", "decision": None},
    )
    awaiting = build_first_run_journey(user_id)
    assert awaiting["stage"] == "confirm_frequency"
    assert awaiting["declarations"]["availability"][
        "preferred_sessions_per_week"
    ] == 4
    assert awaiting["planning_recommendation"]["recommendation"][
        "recommended_sessions_per_week"
    ] == 3

    monkeypatch.setattr(
        first_run_journey,
        "_safe_decision_state",
        lambda *args: {
            "status": "available",
            "decision": {"confirmed_sessions_per_week": 2},
            "plan_constraints": {"confirmed_sessions_per_week": 2},
        },
    )
    ready = build_first_run_journey(user_id)
    assert ready["stage"] == "ready_for_plan"
    assert ready["planning_decision"]["decision"][
        "confirmed_sessions_per_week"
    ] == 2


@pytest.mark.parametrize("legacy_payload", [None, {"objective": {"title": "Objectif à définir"}}])
def test_goal_v1_is_canonical_for_profile_and_coach(
    isolated_first_run: Path, legacy_payload: dict | None
) -> None:
    user_id = "u_canonical_goal"
    update_motionest_inputs_v1(
        user_id,
        goal=build_goal_v1("race", event_type="half_marathon", race_date="2027-10-18"),
    )
    profile = isolated_first_run / "users" / user_id / "profile.json"
    if legacy_payload is not None:
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(json.dumps(legacy_payload), encoding="utf-8")

    with user_context(user_id):
        objective = dashboard.load_objective()
        context = dashboard.coach_context()

    assert objective["title"] == "Semi-marathon"
    assert objective["raceDate"] == "2027-10-18"
    assert objective["source"] == "motionest_inputs_v1"
    assert context["objectif"]["title"] == "Semi-marathon"
    assert context["declarations_v1"]["goal"]["intent"] == "race"


def test_legacy_profile_writer_cannot_override_explicit_goal_v1(
    isolated_first_run: Path,
) -> None:
    user_id = "u_v1_write_guard"
    _goal(user_id, "beginner")
    cookie = f"motionest_session={user_accounts.create_session(user_id)}"
    server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/api/profile",
        data=json.dumps(
            {
                "title": "Legacy override",
                "sessionsPerWeek": 7,
                "availableDays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            }
        ).encode(),
        headers={"Content-Type": "application/json", "Cookie": cookie},
        method="POST",
    )
    try:
        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=5)
        payload = json.loads(raised.value.read())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    assert raised.value.code == 409
    assert payload["code"] == "canonical_v1_inputs_active"
    assert not (isolated_first_run / "users" / user_id / "profile.json").exists()


def test_get_journey_never_calls_ai_or_garmin(
    isolated_first_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = "u_read_only_journey"
    _goal(user_id)
    cookie = f"motionest_session={user_accounts.create_session(user_id)}"

    def forbidden(*args, **kwargs):
        raise AssertionError("Un GET du parcours ne doit produire aucun effet externe.")

    monkeypatch.setattr(dashboard, "generate_goal_assessment", forbidden)
    monkeypatch.setattr(dashboard, "generate_planning_recommendation", forbidden)
    monkeypatch.setattr(dashboard, "connect", forbidden)
    server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/first-run-journey",
            headers={"Cookie": cookie},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    assert payload["journey"]["stage"] == "connect_garmin"


def test_first_run_isolated_between_two_users(isolated_first_run: Path) -> None:
    _goal("u_alpha", "beginner")
    _goal("u_beta", "comeback")
    _tokens(isolated_first_run, "u_alpha")
    _onboarding(isolated_first_run, "u_alpha", "analysis_ready")

    alpha = build_first_run_journey("u_alpha")
    beta = build_first_run_journey("u_beta")

    assert alpha["stage"] == "review_garmin"
    assert alpha["declarations"]["goal"]["intent"] == "beginner"
    assert beta["stage"] == "connect_garmin"
