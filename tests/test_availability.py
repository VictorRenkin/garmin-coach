from __future__ import annotations

import builtins
import json
import socket
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from garmin_coach import availability


def test_one_available_day_is_valid() -> None:
    result = availability.build_availability_v1(["wed"])
    assert result.available_days == ["wed"]
    assert result.preferred_sessions_per_week is None


def test_seven_available_days_are_canonical() -> None:
    result = availability.build_availability_v1(
        ["sun", "fri", "wed", "mon", "sat", "thu", "tue"]
    )
    assert result.available_days == list(availability.CANONICAL_DAYS)


def test_ui_constructor_normalizes_order_case_space_and_duplicates() -> None:
    result = availability.build_availability_v1(
        [" SUN ", "tue", "THU", "tue"]
    )
    assert result.available_days == ["tue", "thu", "sun"]


def test_strict_contract_rejects_disordered_days_and_duplicates() -> None:
    with pytest.raises(ValidationError):
        availability.AvailabilityV1(available_days=["sun", "tue"])
    with pytest.raises(ValidationError):
        availability.AvailabilityV1(available_days=["tue", "tue"])


def test_empty_or_invalid_available_days_are_rejected() -> None:
    with pytest.raises((ValueError, ValidationError)):
        availability.build_availability_v1([])
    with pytest.raises(ValueError):
        availability.build_availability_v1(["monday"])


@pytest.mark.parametrize("sessions", [None, 1, 7])
def test_preferred_sessions_valid_values(sessions: int | None) -> None:
    result = availability.build_availability_v1(
        availability.CANONICAL_DAYS,
        preferred_sessions_per_week=sessions,
    )
    assert result.preferred_sessions_per_week == sessions


@pytest.mark.parametrize("sessions", [0, 8, -1])
def test_preferred_sessions_out_of_range_are_rejected(sessions: int) -> None:
    with pytest.raises(ValidationError):
        availability.build_availability_v1(
            availability.CANONICAL_DAYS,
            preferred_sessions_per_week=sessions,
        )


@pytest.mark.parametrize("sessions", [True, 3.0, "3"])
def test_strict_preferred_sessions_requires_an_integer(sessions: object) -> None:
    with pytest.raises(ValidationError):
        availability.AvailabilityV1(
            available_days=["mon", "tue", "wed"],
            preferred_sessions_per_week=sessions,
        )


def test_sessions_do_not_default_to_number_of_available_days() -> None:
    result = availability.build_availability_v1(
        ["mon", "tue", "thu", "sat", "sun"]
    )
    assert result.preferred_sessions_per_week is None


def test_sessions_cannot_exceed_available_days_in_strict_contract() -> None:
    with pytest.raises(ValidationError):
        availability.build_availability_v1(
            ["tue", "thu", "sat"], preferred_sessions_per_week=5
        )


def test_empty_long_session_preference_is_valid() -> None:
    result = availability.build_availability_v1(["tue", "sat"])
    assert result.long_session_preferred_days == []


def test_one_or_multiple_long_session_days_are_normalized() -> None:
    one = availability.build_availability_v1(
        ["tue", "sat", "sun"], long_session_preferred_days=["sat"]
    )
    multiple = availability.build_availability_v1(
        ["tue", "sat", "sun"], long_session_preferred_days=["sun", "sat"]
    )
    assert one.long_session_preferred_days == ["sat"]
    assert multiple.long_session_preferred_days == ["sat", "sun"]


def test_long_session_day_must_be_available() -> None:
    with pytest.raises(ValidationError):
        availability.build_availability_v1(
            ["tue", "thu"], long_session_preferred_days=["sun"]
        )


def test_strict_long_session_days_reject_duplicates_and_bad_order() -> None:
    with pytest.raises(ValidationError):
        availability.AvailabilityV1(
            available_days=["sat", "sun"],
            long_session_preferred_days=["sat", "sat"],
        )
    with pytest.raises(ValidationError):
        availability.AvailabilityV1(
            available_days=["sat", "sun"],
            long_session_preferred_days=["sun", "sat"],
        )


def test_effective_from_is_optional_and_never_invented() -> None:
    absent = availability.build_availability_v1(["mon"])
    present = availability.build_availability_v1(
        ["mon"], effective_from="2027-01-04"
    )
    assert absent.effective_from is None
    assert present.effective_from == date(2027, 1, 4)


def test_invalid_effective_from_is_rejected() -> None:
    with pytest.raises(ValidationError):
        availability.build_availability_v1(["mon"], effective_from="not-a-date")


def test_legacy_available_days_and_sessions_are_mapped() -> None:
    result = availability.availability_from_legacy_objective(
        {
            "availableDays": ["sun", "tue", "thu"],
            "sessionsPerWeek": 3,
        }
    )
    assert result.availability
    assert result.availability.available_days == ["tue", "thu", "sun"]
    assert result.availability.preferred_sessions_per_week == 3
    assert result.diagnostics == []


def test_legacy_without_sessions_is_valid_and_not_missing() -> None:
    result = availability.availability_from_legacy_objective(
        {"availableDays": ["tue", "thu"]}
    )
    assert result.availability
    assert result.availability.preferred_sessions_per_week is None
    assert result.diagnostics == []


@pytest.mark.parametrize("value", [None, [], "tue"])
def test_legacy_without_usable_available_days_returns_none(value: object) -> None:
    result = availability.availability_from_legacy_objective(
        {"availableDays": value}
    )
    assert result.availability is None
    assert "available_days_missing" in {item.code for item in result.diagnostics}


def test_legacy_normalizes_duplicates_and_reports_them() -> None:
    result = availability.availability_from_legacy_objective(
        {"availableDays": [" TUE ", "thu", "tue"]}
    )
    assert result.availability
    assert result.availability.available_days == ["tue", "thu"]
    assert [item.code for item in result.diagnostics] == [
        "duplicate_available_day"
    ]


def test_legacy_ignores_unknown_days_with_diagnostic() -> None:
    result = availability.availability_from_legacy_objective(
        {"availableDays": ["tue", "funday", "sat"]}
    )
    assert result.availability
    assert result.availability.available_days == ["tue", "sat"]
    assert [item.code for item in result.diagnostics] == [
        "invalid_available_day"
    ]


@pytest.mark.parametrize("value", [0, 8, "three", True, 2.5])
def test_invalid_legacy_sessions_become_null_with_diagnostic(value: object) -> None:
    result = availability.availability_from_legacy_objective(
        {"availableDays": ["mon", "wed", "fri"], "sessionsPerWeek": value}
    )
    assert result.availability
    assert result.availability.preferred_sessions_per_week is None
    assert [item.code for item in result.diagnostics] == [
        "invalid_sessions_per_week"
    ]


def test_legacy_sessions_exceeding_days_are_not_silently_clamped() -> None:
    result = availability.availability_from_legacy_objective(
        {"availableDays": ["tue", "thu", "sat"], "sessionsPerWeek": 5}
    )
    assert result.availability
    assert result.availability.preferred_sessions_per_week is None
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == "sessions_exceed_available_days"
    assert diagnostic.details == {
        "preferred_sessions_per_week": 5,
        "available_day_count": 3,
    }


def test_legacy_long_session_preferences_are_tolerant_and_diagnostic() -> None:
    result = availability.availability_from_legacy_objective(
        {
            "availableDays": ["tue", "sat"],
            "longSessionPreferredDays": ["sun", "sat", "sat"],
        }
    )
    assert result.availability
    assert result.availability.long_session_preferred_days == ["sat"]
    assert {item.code for item in result.diagnostics} == {
        "duplicate_available_day",
        "long_session_day_not_available",
    }


def test_goal_and_subjective_fields_are_ignored() -> None:
    base = {"availableDays": ["tue", "thu", "sat"], "sessionsPerWeek": 3}
    polluted = {
        **base,
        "goalType": "race",
        "title": "Private title",
        "distanceKm": 42.195,
        "raceDate": "2027-04-18",
        "goal_target_time": "02:59:59",
        "injuryPain": "Private pain",
        "preferences": "Private preference",
        "constraints": "Private constraint",
    }
    assert availability.availability_from_legacy_objective(
        base
    ) == availability.availability_from_legacy_objective(polluted)


def test_determinism() -> None:
    legacy = {
        "availableDays": ["SAT", "tue", "thu"],
        "sessionsPerWeek": "3",
    }
    assert availability.availability_from_legacy_objective(
        legacy
    ) == availability.availability_from_legacy_objective(dict(legacy))


def test_exact_json_contract() -> None:
    result = availability.build_availability_v1(
        ["tue", "thu", "sat"],
        preferred_sessions_per_week=3,
        long_session_preferred_days=["sat"],
        effective_from="2027-01-04",
    )
    assert result.model_dump(mode="json") == {
        "schema_version": 1,
        "available_days": ["tue", "thu", "sat"],
        "preferred_sessions_per_week": 3,
        "long_session_preferred_days": ["sat"],
        "effective_from": "2027-01-04",
    }


def test_privacy_and_no_files_network_garmin_or_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = {
        "availableDays": ["tue", "thu", "sat"],
        "sessionsPerWeek": 3,
        "name": "Private name",
        "email": "private@example.com",
        "location": "Private location",
        "garminId": "12345",
        "token": "secret-token",
    }

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("External access is forbidden")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = availability.availability_from_legacy_objective(legacy)
    assert result.availability
    payload = json.dumps(
        availability.availability_for_ai(result.availability)
    ).lower()
    for forbidden_value in (
        "private",
        "email",
        "location",
        "garmin",
        "token",
        "12345",
    ):
        assert forbidden_value not in payload
