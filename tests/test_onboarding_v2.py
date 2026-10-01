from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from garmin_coach import config, dashboard, user_accounts
from garmin_coach.availability import build_availability_v1
from garmin_coach.goal import build_goal_v1
from garmin_coach.onboarding import onboarding_v2_state
from garmin_coach.runtime_context import (
    load_motionest_inputs_v1,
    update_motionest_inputs_v1,
)
from garmin_coach.subjective_context import build_subjective_context_v1


@pytest.fixture
def isolated_v2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    monkeypatch.setattr(user_accounts, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(
        user_accounts, "SESSION_KEY_FILE", tmp_path / ".secrets" / "session.key"
    )
    return tmp_path


def _serve() -> tuple[dashboard.ThreadingHTTPServer, threading.Thread]:
    server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _post(server, cookie: str, path: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Cookie": cookie},
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())
    with response:
        return response.status, json.loads(response.read())


@pytest.mark.parametrize("intent", ["progress", "beginner", "comeback"])
def test_non_race_goals_are_persisted_without_measured_fields(
    isolated_v2: Path, intent: str
) -> None:
    update_motionest_inputs_v1("u_goal", goal=build_goal_v1(intent))
    stored = load_motionest_inputs_v1("u_goal")
    assert stored.goal.intent == intent
    assert stored.goal.race is None


@pytest.mark.parametrize(
    ("event_type", "distance"),
    [("5k", 5.0), ("10k", 10.0), ("half_marathon", 21.0975), ("marathon", 42.195)],
)
def test_standard_race_distances_are_canonical(
    isolated_v2: Path, event_type: str, distance: float
) -> None:
    goal = build_goal_v1("race", event_type=event_type)
    assert goal.race.distance_km == distance


@pytest.mark.parametrize("event_type", ["trail", "custom"])
def test_variable_races_require_positive_distance(isolated_v2: Path, event_type: str) -> None:
    assert build_goal_v1("race", event_type=event_type, distance_km=18.5).race.distance_km == 18.5
    cookie = f"motionest_session={user_accounts.create_session('u_distance')}"
    server, thread = _serve()
    try:
        status, _ = _post(
            server,
            cookie,
            "/api/onboarding/goal",
            {"intent": "race", "eventType": event_type},
        )
        assert status == 400
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_target_time_is_structural_not_sporting_judgment(isolated_v2: Path) -> None:
    goal = build_goal_v1(
        "race", event_type="marathon", target_time="00:01:01", race_date="2027-10-18"
    )
    assert goal.race.target_time.canonical == "00:01:01"
    with pytest.raises(ValueError):
        build_goal_v1("race", event_type="10k", target_time="1:70:00")


@pytest.mark.parametrize(
    "days", [["mon"], ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]]
)
def test_availability_has_no_frequency_and_uses_canonical_days(
    isolated_v2: Path, days: list[str]
) -> None:
    value = build_availability_v1(reversed(days))
    assert value.available_days == days
    assert value.preferred_sessions_per_week is None
    assert value.long_session_preferred_days == []


def test_step_by_step_api_resume_edit_and_finalize(isolated_v2: Path) -> None:
    cookie = f"motionest_session={user_accounts.create_session('u_flow')}"
    server, thread = _serve()
    try:
        status, goal = _post(server, cookie, "/api/onboarding/goal", {"intent": "beginner"})
        assert status == 200
        assert goal["nextStep"] == "availability"
        assert goal["onboarding"]["input_progress"]["goal"] is True

        status, availability = _post(
            server,
            cookie,
            "/api/onboarding/availability",
            {"availableDays": ["sun", "mon"]},
        )
        assert status == 200
        assert availability["motionestInputs"]["availability"]["available_days"] == ["mon", "sun"]
        assert availability["motionestInputs"]["availability"]["preferred_sessions_per_week"] is None
        assert availability["nextStep"] == "subjective_context"

        status, final = _post(
            server,
            cookie,
            "/api/onboarding/subjective-context",
            {
                "painOrInjuryAffectingTraining": False,
                "painDescription": "must be discarded",
                "unrecordedActivities": [
                    {"sport": "Musculation", "sessions_per_week": 2, "typical_duration_minutes": 45}
                ],
                "importantPreferences": ["J'aime courir le week-end"],
            },
        )
        assert status == 200
        assert final["onboarding"]["status"] == "ready"
        assert final["nextStep"] == "complete"
        subjective = final["motionestInputs"]["subjective_context"]
        assert subjective["pain_or_injury"] == {
            "status": "explicit_no", "affecting_training": False, "description": None
        }
        assert subjective["unrecorded_activities"][0]["source"] == "user_reported_unrecorded"

        status, edited = _post(server, cookie, "/api/onboarding/goal", {"intent": "progress"})
        assert status == 200
        assert edited["motionestInputs"]["availability"] == final["motionestInputs"]["availability"]
        assert edited["motionestInputs"]["goal"]["created_at"] == final["motionestInputs"]["goal"]["created_at"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_sessions_per_week_is_rejected_by_v2_api(isolated_v2: Path) -> None:
    cookie = f"motionest_session={user_accounts.create_session('u_frequency')}"
    server, thread = _serve()
    try:
        status, _ = _post(
            server,
            cookie,
            "/api/onboarding/availability",
            {"availableDays": ["mon"], "sessionsPerWeek": 1},
        )
        assert status == 400
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_concurrent_updates_preserve_contracts_and_atomic_store(isolated_v2: Path) -> None:
    user_id = "u_concurrent"
    update_motionest_inputs_v1(user_id, goal=build_goal_v1("progress"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda operation: operation(),
                [
                    lambda: update_motionest_inputs_v1(
                        user_id, availability=build_availability_v1(["wed"])
                    ),
                    lambda: update_motionest_inputs_v1(
                        user_id,
                        subjective_context=build_subjective_context_v1(
                            affecting_training=True, pain_description="Genou"
                        ),
                    ),
                ],
            )
        )
    stored = load_motionest_inputs_v1(user_id)
    assert stored.goal.intent == "progress"
    assert stored.availability.available_days == ["wed"]
    assert stored.subjective_context.pain_or_injury.status == "explicit_yes"
    workspace = isolated_v2 / "users" / user_id
    assert not list(workspace.glob("*.tmp"))


def test_v2_api_isolates_two_users(isolated_v2: Path) -> None:
    server, thread = _serve()
    try:
        alice = f"motionest_session={user_accounts.create_session('u_alice')}"
        bob = f"motionest_session={user_accounts.create_session('u_bob')}"
        assert _post(server, alice, "/api/onboarding/goal", {"intent": "beginner"})[0] == 200
        assert _post(server, bob, "/api/onboarding/goal", {"intent": "comeback"})[0] == 200
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
    assert load_motionest_inputs_v1("u_alice").goal.intent == "beginner"
    assert load_motionest_inputs_v1("u_bob").goal.intent == "comeback"


def test_legacy_and_corrupt_progress_strategy(isolated_v2: Path) -> None:
    incomplete = isolated_v2 / "users" / "u_legacy" / "onboarding.json"
    incomplete.parent.mkdir(parents=True)
    incomplete.write_text(json.dumps({"status": "needs_profile"}), encoding="utf-8")
    inputs = load_motionest_inputs_v1("u_legacy")
    state = onboarding_v2_state(inputs_source=inputs.source, inputs=inputs)
    assert state["status"] == "collecting_inputs"
    assert state["input_progress"]["current_step"] == "goal"

    completed = isolated_v2 / "users" / "u_complete" / "onboarding.json"
    completed.parent.mkdir(parents=True)
    completed.write_text(json.dumps({"status": "complete"}), encoding="utf-8")
    from garmin_coach.config import user_context
    with user_context("u_complete"):
        assert onboarding_v2_state()["status"] == "complete"

    corrupt = isolated_v2 / "users" / "u_corrupt" / "onboarding.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("not-json", encoding="utf-8")
    with user_context("u_corrupt"):
        safe = onboarding_v2_state()
    assert safe["status"] == "collecting_inputs"
    assert safe["input_progress"]["current_step"] == "goal"


def test_ui_uses_v2_endpoints_without_legacy_profile_submit() -> None:
    html = dashboard.TEMPLATE_FILE.read_text(encoding="utf-8")
    onboarding_script = html[html.index("const onboardingSteps=") : html.index("$('chatInput').addEventListener")]
    assert "/api/onboarding/${step}" in onboarding_script
    assert "/api/profile" not in onboarding_script
    assert "sessionsPerWeek" not in html[html.index('<dialog class="onboarding-dialog"') : html.index("</dialog>", html.index('<dialog class="onboarding-dialog"'))]
    assert "onboardingObservation" not in html
    connect_html = dashboard.CONNECT_GARMIN_FILE.read_text(encoding="utf-8")
    assert "data-status=\"analysis_ready\"" in connect_html
    assert "['analysis_ready','plan_proposed','complete']" in connect_html
    assert "Préparation de ton plan" not in connect_html
