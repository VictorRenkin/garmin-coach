import json
from datetime import date, timedelta

import pytest

from garmin_coach import coach_chat, config, dashboard, history_import, history_storage
from garmin_coach.config import Settings


REFERENCE = date(2026, 9, 30)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.setattr(dashboard, "build_first_run_journey", lambda user: {})


def seed(user, sports):
    with config.user_context(user):
        history_import.initialize_history_import("all", reference_date=REFERENCE)
        for i, sport in enumerate(sports):
            history_storage.save_activity_metadata({
                "activityId": f"{user}-{i}", "activityName": "PRIVATE TITLE",
                "activityType": {"typeKey": sport},
                "startTimeLocal": f"{(REFERENCE - timedelta(days=i)).isoformat()}T08:00:00",
                "distance": 6000, "movingDuration": 2400,
                "ownerEmail": "private@example.com", "latitude": 12,
            })


def sent_payload(user, monkeypatch):
    captured = {}
    class Responses:
        def parse(self, **kwargs):
            captured.update(json.loads(kwargs["input"])["contexte"])
            return type("Result", (), {"output_parsed": coach_chat.CoachResponse(message="Contexte reçu.")})()
    class Client:
        def __init__(self, **kwargs):
            self.responses = Responses()
    monkeypatch.setattr(coach_chat, "OpenAI", Client)
    monkeypatch.setattr(coach_chat, "record_usage", lambda *args, **kwargs: None)
    with config.user_context(user):
        context = dashboard.coach_context()
        coach_chat.generate_response(
            {"content": "Quels sports sont enregistrés ?"},
            Settings(None, None, 300, "fake-test-key", "test-model"), context,
        )
    return captured


@pytest.mark.parametrize("sports", [
    ["running"], ["running", "soccer"], ["running", "cycling", "swimming"], [],
])
def test_actual_model_payload_contains_available_sports(sports, monkeypatch):
    seed("athlete", sports)
    payload = sent_payload("athlete", monkeypatch)
    multi = payload["contexte_multisport"]
    assert set(multi["context"]["athlete_state"]["sports_observed"]) == set(sports)
    assert {item["sport"] for item in [a for week in multi["weeks"].values() for a in week["activities"]]} == set(sports)
    assert multi["history"]["source"] == "multisport"
    assert multi["history"]["coverage"]["activity_count"] == len(sports)
    assert "courses_recentes" not in payload and "trace_sportive" not in payload
    assert "PRIVATE TITLE" not in json.dumps(multi)
    assert "private@example.com" not in json.dumps(multi)
    assert "latitude" not in json.dumps(multi)
    for item in [a for week in multi["weeks"].values() for a in week["activities"]]:
        assert item["average_heart_rate"] is None
        assert item["average_power_watts"] is None
    assert multi["context"]["data_confidence"]["heart_rate"] != "high"


def test_other_account_data_never_enters_model_payload(monkeypatch):
    seed("alice", ["running"])
    seed("bob", ["soccer", "swimming"])
    alice = sent_payload("alice", monkeypatch)["contexte_multisport"]
    bob = sent_payload("bob", monkeypatch)["contexte_multisport"]
    assert alice["context"]["athlete_state"]["sports_observed"] == ["running"]
    assert set(bob["context"]["athlete_state"]["sports_observed"]) == {"soccer", "swimming"}
    assert alice["history"]["coverage"]["activity_count"] == 1
    assert bob["history"]["coverage"]["activity_count"] == 2


def test_recent_payload_is_bounded_but_canonical_summary_keeps_sports(monkeypatch):
    seed("athlete", ["running"] * 11 + ["soccer"])
    multi = sent_payload("athlete", monkeypatch)["contexte_multisport"]
    assert len([a for week in multi["weeks"].values() for a in week["activities"]]) == 12
    assert multi["budget"]["activities_truncated"] is False
    assert "soccer" in multi["context"]["athlete_state"]["sports_observed"]


def test_canonical_failure_is_explicit_without_fallback(monkeypatch):
    seed("athlete", [])
    def fail(user):
        raise ValueError("private sensitive failure")
    monkeypatch.setattr(dashboard, "build_conversation_sport_context", fail)
    assert sent_payload("athlete", monkeypatch)["contexte_multisport"] == {
        "status": "unavailable", "reason": "canonical_context_unavailable"
    }


def test_measured_values_and_declared_pain_remain_distinct(monkeypatch):
    seed("athlete", ["running"])
    with config.user_context("athlete"):
        config.PROFILE_FILE.write_text(json.dumps({"objective": {
            "injuryPain": "oui", "injuryDetails": "Gêne déclarée au genou",
        }}), encoding="utf-8")
        history_storage.save_activity_metadata({
            "activityId": "measured", "activityType": {"typeKey": "cycling"},
            "startTimeLocal": f"{REFERENCE.isoformat()}T10:00:00",
            "averageHR": 132, "averagePower": 185, "movingDuration": 1800,
        })
    multi = sent_payload("athlete", monkeypatch)["contexte_multisport"]
    cycling = next(item for item in [a for week in multi["weeks"].values() for a in week["activities"]] if item["sport"] == "cycling")
    assert cycling["average_heart_rate"] == 132
    assert cycling["average_power_watts"] == 185
    assert cycling["distance_m"] is None
    assert multi["context"]["subjective_context"]["pain_or_injury"]["affecting_training"] is True


def test_old_activities_are_not_presented_as_recent(monkeypatch):
    seed("athlete", ["soccer"] * 35)
    multi = sent_payload("athlete", monkeypatch)["contexte_multisport"]
    assert multi["context"]["athlete_state"]["recent_activity_count"] == 28
    assert all(item["date"] >= "2026-09-17" for item in [a for week in multi["weeks"].values() for a in week["activities"]])


@pytest.mark.parametrize("sports", [["running"] * 4, ["running"] * 28,
    ["running", "cycling", "lap_swimming"] * 5, [], ["running"] * 100])
def test_two_weeks_and_canonical_values(sports, monkeypatch):
    from garmin_coach.training_history_summary import build_training_history_summary
    seed("athlete", sports)
    multi = sent_payload("athlete", monkeypatch)["contexte_multisport"]
    with config.user_context("athlete"):
        summary = build_training_history_summary(reference_date=REFERENCE)
    assert multi["history"]["windows"] == {
        key: summary.windows[key].model_dump(mode="json")
        for key in ("last_28_days", "last_84_days")}
    assert multi["history"]["trends"] == summary.trends.model_dump(mode="json")
    for key, low, high in (("last_7_days", 0, 7), ("previous_7_days", 7, 14)):
        week = multi["weeks"][key]
        assert week["summary"]["activity_count"] == max(0, min(len(sports), high) - low)
        assert week["activities_omitted"] == 0
        assert week["activity_list_complete"]


def test_busy_weeks_have_no_count_cap(monkeypatch):
    seed("athlete", [])
    with config.user_context("athlete"):
        for offset in range(14):
            for slot in range(3):
                history_storage.save_activity_metadata({
                    "activityId": f"busy-{offset}-{slot}",
                    "activityType": {"typeKey": "running"},
                    "startTimeLocal": f"{REFERENCE-timedelta(days=offset)}T{slot+8:02d}:00:00",
                    "distance": 5000, "movingDuration": 1800})
    multi = sent_payload("athlete", monkeypatch)["contexte_multisport"]
    assert [len(w["activities"]) for w in multi["weeks"].values()] == [21, 21]
    assert not multi["budget"]["activities_truncated"]


def test_budget_preserves_all_totals(monkeypatch):
    from garmin_coach import conversation_sport_context as module
    seed("athlete", ["running"] * 14)
    full = sent_payload("athlete", monkeypatch)["contexte_multisport"]
    monkeypatch.setattr(module, "MAX_CONTEXT_BYTES", module._size(full) - 1500)
    partial = sent_payload("athlete", monkeypatch)["contexte_multisport"]
    assert module._size(partial) <= module.MAX_CONTEXT_BYTES
    assert partial["budget"]["activities_truncated"]
    for key, week in partial["weeks"].items():
        assert week["summary"] == full["weeks"][key]["summary"]
        assert len(week["activities"]) + week["activities_omitted"] == week["summary"]["activity_count"]


def test_irrelevant_distance_and_private_data(monkeypatch):
    seed("athlete", ["strength_training"])
    multi = sent_payload("athlete", monkeypatch)["contexte_multisport"]
    activity = multi["weeks"]["last_7_days"]["activities"][0]
    assert activity["distance_m"] is None
    assert not activity["distance_relevant"]
    encoded = json.dumps(multi)
    for secret in ("PRIVATE TITLE", "private@example.com", "athlete-0", "latitude"):
        assert secret not in encoded
