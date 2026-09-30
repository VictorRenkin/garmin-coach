from __future__ import annotations

import builtins
import json
import socket
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from garmin_coach import goal


@pytest.mark.parametrize(
    ("intent", "label"),
    [
        ("progress", "Progresser"),
        ("beginner", "Débuter"),
        ("comeback", "Reprendre"),
    ],
)
def test_minimal_non_race_goals_are_valid(intent: str, label: str) -> None:
    result = goal.build_goal_v1(intent)  # type: ignore[arg-type]

    assert result.intent == intent
    assert result.race is None
    assert result.created_at is None
    assert goal.goal_display_summary(result).primary_label == label


@pytest.mark.parametrize(
    ("event_type", "distance"),
    [
        ("5k", 5.0),
        ("10k", 10.0),
        ("half_marathon", 21.0975),
        ("marathon", 42.195),
    ],
)
def test_standard_races_have_exact_distances(event_type: str, distance: float) -> None:
    result = goal.build_goal_v1("race", event_type=event_type)  # type: ignore[arg-type]

    assert result.race is not None
    assert result.race.event_type == event_type
    assert result.race.distance_km == distance


def test_trail_and_custom_distances_are_explicit() -> None:
    trail = goal.build_goal_v1("race", event_type="trail", distance_km=35.5)
    custom = goal.build_goal_v1("race", event_type="custom", distance_km=15.2)
    custom_unknown = goal.build_goal_v1("race", event_type="custom")

    assert trail.race and trail.race.distance_km == 35.5
    assert custom.race and custom.race.distance_km == 15.2
    assert custom_unknown.race and custom_unknown.race.distance_km is None


def test_race_date_and_target_time_are_independently_optional() -> None:
    neither = goal.build_goal_v1("race", event_type="marathon")
    date_only = goal.build_goal_v1(
        "race", event_type="marathon", race_date="2027-04-18"
    )
    time_only = goal.build_goal_v1(
        "race", event_type="marathon", target_time="03:30:00"
    )

    assert neither.race and neither.race.date is None and neither.race.target_time is None
    assert date_only.race and date_only.race.date == date(2027, 4, 18)
    assert time_only.race and time_only.race.date is None
    assert time_only.race.target_time and time_only.race.target_time.seconds == 12600


@pytest.mark.parametrize(
    ("canonical", "seconds"),
    [
        ("02:59:59", 10799),
        ("00:39:59", 2399),
        ("02:00:00", 7200),
        ("01:29:00", 5340),
        ("00:42:30", 2550),
    ],
)
def test_target_time_canonical_and_seconds(canonical: str, seconds: int) -> None:
    result = goal.build_goal_v1(
        "race", event_type="10k", target_time=canonical
    )

    assert result.race and result.race.target_time
    assert result.race.target_time.canonical == canonical
    assert result.race.target_time.seconds == seconds


@pytest.mark.parametrize(
    "invalid",
    ["", "1:30:00", "01:60:00", "01:00:60", "00:00:00", "abc"],
)
def test_invalid_target_times_are_rejected(invalid: str) -> None:
    with pytest.raises(ValueError):
        goal.build_goal_v1("race", event_type="10k", target_time=invalid)


@pytest.mark.parametrize("distance", [0, -1, float("nan"), float("inf")])
def test_invalid_custom_distance_is_rejected(distance: float) -> None:
    with pytest.raises((ValueError, ValidationError)):
        goal.build_goal_v1("race", event_type="custom", distance_km=distance)


def test_standard_distance_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError):
        goal.build_goal_v1("race", event_type="marathon", distance_km=42.0)


def test_invalid_date_is_rejected_but_past_date_is_data() -> None:
    with pytest.raises(ValidationError):
        goal.build_goal_v1("race", event_type="5k", race_date="not-a-date")

    past = goal.build_goal_v1(
        "race", event_type="5k", race_date="2001-01-01"
    )
    assert past.race and past.race.date == date(2001, 1, 1)


def test_strict_contract_rejects_unknown_intent_and_inactive_race_data() -> None:
    with pytest.raises(ValidationError):
        goal.GoalV1(intent="fitness")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        goal.build_goal_v1("progress", target_time="00:40:00")
    with pytest.raises(ValidationError):
        goal.GoalV1(intent="beginner", race={"event_type": "5k", "distance_km": 5.0})


@pytest.mark.parametrize(
    ("legacy_type", "expected"),
    [
        ("performance", "progress"),
        ("comeback", "comeback"),
        ("beginner", "beginner"),
    ],
)
def test_legacy_non_race_mapping(legacy_type: str, expected: str) -> None:
    result = goal.goal_from_legacy_objective({"goalType": legacy_type})
    assert result.intent == expected
    assert result.race is None


@pytest.mark.parametrize(
    ("distance", "event_type", "normalized"),
    [
        (5, "5k", 5.0),
        (10, "10k", 10.0),
        (21.1, "half_marathon", 21.0975),
        (42.2, "marathon", 42.195),
        (35, "custom", 35.0),
    ],
)
def test_legacy_race_distance_mapping(
    distance: float, event_type: str, normalized: float
) -> None:
    result = goal.goal_from_legacy_objective(
        {"goalType": "race", "distanceKm": distance}
    )

    assert result.race
    assert result.race.event_type == event_type
    assert result.race.distance_km == normalized


def test_legacy_race_without_distance_is_tolerated_with_diagnostic() -> None:
    diagnostics: list[goal.GoalAdaptationDiagnostic] = []
    result = goal.goal_from_legacy_objective(
        {"goalType": "race"}, diagnostics=diagnostics
    )

    assert result.race and result.race.event_type == "custom"
    assert result.race.distance_km is None
    assert [item.code for item in diagnostics] == ["legacy_race_distance_unavailable"]


def test_invalid_legacy_optional_values_are_ignored_with_diagnostics() -> None:
    diagnostics: list[goal.GoalAdaptationDiagnostic] = []
    result = goal.goal_from_legacy_objective(
        {
            "goalType": "race",
            "distanceKm": -5,
            "raceDate": "bad-date",
            "goal_target_time": "1:99:00",
        },
        diagnostics=diagnostics,
    )

    assert result.race and result.race.distance_km is None
    assert result.race.date is None and result.race.target_time is None
    assert {item.code for item in diagnostics} == {
        "invalid_legacy_distance_ignored",
        "legacy_race_distance_unavailable",
        "invalid_legacy_race_date_ignored",
        "invalid_legacy_target_time_ignored",
    }


def test_legacy_title_is_not_required_or_exposed() -> None:
    without = goal.goal_from_legacy_objective({"goalType": "performance"})
    with_free_text = goal.goal_from_legacy_objective(
        {"goalType": "performance", "title": "My private free text"}
    )

    assert without == with_free_text
    assert "title" not in with_free_text.model_dump(mode="json")


def test_legacy_inactive_race_fields_are_ignored_for_progress() -> None:
    diagnostics: list[goal.GoalAdaptationDiagnostic] = []
    result = goal.goal_from_legacy_objective(
        {
            "goalType": "performance",
            "distanceKm": 21.1,
            "raceDate": "2027-04-18",
            "goal_target_time": "01:30:00",
        },
        diagnostics=diagnostics,
    )

    assert result.race is None
    assert [item.code for item in diagnostics] == [
        "inactive_legacy_race_fields_ignored"
    ]


def test_availability_and_subjective_fields_never_enter_goal() -> None:
    result = goal.goal_from_legacy_objective(
        {
            "goalType": "performance",
            "sessionsPerWeek": 5,
            "availableDays": ["mon", "wed"],
            "injuryPain": "private injury",
            "preferences": "private preference",
            "constraints": "private constraint",
        }
    )
    payload = json.dumps(result.model_dump(mode="json"))

    for forbidden in (
        "sessionsPerWeek",
        "availableDays",
        "injuryPain",
        "preferences",
        "constraints",
        "private",
    ):
        assert forbidden not in payload


def test_legacy_date_and_target_time_are_preserved() -> None:
    result = goal.goal_from_legacy_objective(
        {
            "goalType": "race",
            "distanceKm": 42.195,
            "raceDate": "2027-04-18",
            "goal_target_time": "02:59:59",
        }
    )

    assert result.race and result.race.date == date(2027, 4, 18)
    assert result.race.target_time
    assert result.race.target_time.canonical == "02:59:59"
    assert result.race.target_time.seconds == 10799


def test_display_summary_is_deterministic_and_has_no_coaching() -> None:
    result = goal.build_goal_v1(
        "race",
        event_type="marathon",
        race_date="2027-04-18",
        target_time="02:59:59",
    )
    display = goal.goal_display_summary(result)

    assert display.model_dump() == {
        "primary_label": "Marathon",
        "secondary_label": "18 avril 2027 · objectif 02:59:59",
    }


def test_goal_is_deterministic_and_timestamps_are_not_invented() -> None:
    legacy = {
        "goalType": "race",
        "distanceKm": 10,
        "raceDate": "2027-01-10",
        "goal_target_time": "00:39:59",
    }
    first = goal.goal_from_legacy_objective(legacy)
    second = goal.goal_from_legacy_objective(dict(legacy))

    assert first == second
    assert first.created_at is None and first.updated_at is None


def test_goal_is_private_and_uses_no_files_network_garmin_or_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = {
        "goalType": "race",
        "distanceKm": 10,
        "title": "Secret title",
        "email": "private@example.com",
        "garminUserId": "12345",
        "token": "secret-token",
        "latitude": 50.0,
        "longitude": 4.0,
    }

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("External access is forbidden")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = goal.goal_from_legacy_objective(legacy)
    payload = json.dumps(goal.goal_for_ai(result)).lower()

    for forbidden_value in (
        "secret",
        "private@example.com",
        "garmin",
        "latitude",
        "longitude",
        "12345",
    ):
        assert forbidden_value not in payload


def test_future_data_confidence_adapter_contains_only_goal_fields() -> None:
    result = goal.build_goal_v1(
        "race",
        event_type="half_marathon",
        race_date="2026-10-18",
        target_time="02:02:00",
    )

    assert goal.goal_context_for_legacy_confidence(result) == {
        "goalType": "race",
        "title": "Semi-marathon",
        "distanceKm": 21.0975,
        "raceDate": "2026-10-18",
        "goal_target_time": "02:02:00",
    }


def test_exact_goal_v1_contract() -> None:
    result = goal.build_goal_v1(
        "race", event_type="5k", target_time="00:39:59"
    ).model_dump(mode="json")

    assert result == {
        "schema_version": 1,
        "intent": "race",
        "race": {
            "event_type": "5k",
            "distance_km": 5.0,
            "date": None,
            "target_time": {"canonical": "00:39:59", "seconds": 2399},
        },
        "created_at": None,
        "updated_at": None,
    }
