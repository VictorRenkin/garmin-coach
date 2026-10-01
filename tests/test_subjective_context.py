from __future__ import annotations

import builtins
import json
import socket
from typing import Any

import pytest
from pydantic import ValidationError

from garmin_coach import subjective_context


def test_explicit_no_pain() -> None:
    result = subjective_context.build_subjective_context_v1(
        affecting_training=False
    )
    assert result.pain_or_injury.model_dump() == {
        "status": "explicit_no",
        "affecting_training": False,
        "description": None,
    }


def test_explicit_yes_pain_without_description_is_valid() -> None:
    result = subjective_context.build_subjective_context_v1(
        affecting_training=True
    )
    assert result.pain_or_injury.status == "explicit_yes"
    assert result.pain_or_injury.description is None


def test_explicit_yes_with_cleaned_description() -> None:
    result = subjective_context.build_subjective_context_v1(
        affecting_training=True,
        pain_description="  Gêne au tendon   après la course. ",
    )
    assert result.pain_or_injury.description == "Gêne au tendon après la course."


@pytest.mark.parametrize("affecting", [False, None])
def test_description_is_invalid_without_explicit_yes(
    affecting: bool | None,
) -> None:
    with pytest.raises(ValidationError):
        subjective_context.build_subjective_context_v1(
            affecting_training=affecting,
            pain_description="Description incompatible",
        )


def test_unknown_state_is_distinct_from_explicit_no() -> None:
    result = subjective_context.build_subjective_context_v1(
        affecting_training=None
    )
    assert result.pain_or_injury.status == "unknown"
    assert result.pain_or_injury.affecting_training is None


def test_affecting_training_requires_bool_or_none() -> None:
    with pytest.raises(ValueError):
        subjective_context.build_subjective_context_v1(
            affecting_training="yes"  # type: ignore[arg-type]
        )


def test_description_too_long_is_rejected() -> None:
    with pytest.raises(ValidationError):
        subjective_context.build_subjective_context_v1(
            affecting_training=True,
            pain_description="x" * 301,
        )


def test_no_preferences_is_valid() -> None:
    result = subjective_context.build_subjective_context_v1(
        affecting_training=False
    )
    assert result.important_preferences == []


def test_one_or_multiple_preferences_are_preserved_in_order() -> None:
    one = subjective_context.build_subjective_context_v1(
        affecting_training=False,
        important_preferences=["Éviter deux jours de course consécutifs."],
    )
    multiple = subjective_context.build_subjective_context_v1(
        affecting_training=False,
        important_preferences=[
            "Éviter deux jours de course consécutifs.",
            "Conserver une séance de vélo.",
        ],
    )
    assert len(one.important_preferences) == 1
    assert multiple.important_preferences == [
        "Éviter deux jours de course consécutifs.",
        "Conserver une séance de vélo.",
    ]


def test_constructor_normalizes_duplicate_preferences() -> None:
    result = subjective_context.build_subjective_context_v1(
        affecting_training=False,
        important_preferences=[" Vélo le mercredi ", "Vélo le mercredi"],
    )
    assert result.important_preferences == ["Vélo le mercredi"]


def test_strict_contract_rejects_duplicate_or_overlong_preferences() -> None:
    pain = subjective_context.PainOrInjury(
        status="explicit_no", affecting_training=False
    )
    with pytest.raises(ValidationError):
        subjective_context.SubjectiveContextV1(
            pain_or_injury=pain,
            important_preferences=["Même préférence", "Même préférence"],
        )
    with pytest.raises((ValueError, ValidationError)):
        subjective_context.build_subjective_context_v1(
            affecting_training=False,
            important_preferences=["x" * 201],
        )


def test_valid_unrecorded_activity_is_normalized_and_marked_user_reported() -> None:
    result = subjective_context.build_subjective_context_v1(
        affecting_training=False,
        unrecorded_activities=[
            {
                "sport": " Musculation libre ",
                "sessions_per_week": 2,
                "typical_duration_minutes": 60,
            }
        ],
    )
    assert result.unrecorded_activities[0].model_dump() == {
        "source": "user_reported_unrecorded",
        "sport": "musculation_libre",
        "sessions_per_week": 2,
        "typical_duration_minutes": 60,
    }


def test_multiple_unrecorded_activities_are_supported() -> None:
    result = subjective_context.build_subjective_context_v1(
        affecting_training=False,
        unrecorded_activities=[
            {"sport": "strength", "sessions_per_week": 2},
            {
                "sport": "yoga",
                "sessions_per_week": 1,
                "typical_duration_minutes": 45,
            },
        ],
    )
    assert [item.sport for item in result.unrecorded_activities] == [
        "strength",
        "yoga",
    ]


def test_unrecorded_activity_requires_sport() -> None:
    with pytest.raises(ValueError):
        subjective_context.build_subjective_context_v1(
            affecting_training=False,
            unrecorded_activities=[{"sessions_per_week": 2}],
        )


@pytest.mark.parametrize("frequency", [0, -1, True, 2.5, "2"])
def test_unrecorded_activity_frequency_must_be_positive_integer(
    frequency: object,
) -> None:
    with pytest.raises((ValueError, ValidationError)):
        subjective_context.build_subjective_context_v1(
            affecting_training=False,
            unrecorded_activities=[
                {"sport": "strength", "sessions_per_week": frequency}
            ],
        )


@pytest.mark.parametrize("duration", [0, -10, True, 45.5, "45"])
def test_unrecorded_activity_duration_must_be_positive_integer(
    duration: object,
) -> None:
    with pytest.raises((ValueError, ValidationError)):
        subjective_context.build_subjective_context_v1(
            affecting_training=False,
            unrecorded_activities=[
                {
                    "sport": "strength",
                    "sessions_per_week": 2,
                    "typical_duration_minutes": duration,
                }
            ],
        )


def test_reported_at_is_optional_and_not_invented() -> None:
    absent = subjective_context.build_subjective_context_v1(
        affecting_training=False
    )
    present = subjective_context.build_subjective_context_v1(
        affecting_training=False,
        reported_at="2026-09-30T18:30:00+02:00",
    )
    assert absent.reported_at is None
    assert present.reported_at == "2026-09-30T18:30:00+02:00"


def test_invalid_reported_at_is_rejected() -> None:
    with pytest.raises(ValidationError):
        subjective_context.build_subjective_context_v1(
            affecting_training=False,
            reported_at="not-a-timestamp",
        )


@pytest.mark.parametrize(
    "legacy_value",
    ["", "   ", None],
)
def test_empty_legacy_injury_is_unknown(legacy_value: object) -> None:
    result = subjective_context.subjective_context_from_legacy_objective(
        {"injuryPain": legacy_value}
    )
    assert result.context.pain_or_injury.status == "unknown"
    assert [item.code for item in result.diagnostics] == [
        "reported_context_unknown"
    ]


@pytest.mark.parametrize(
    "legacy_value",
    ["Non", "Aucune douleur actuelle.", "RAS", "Pas de blessure"],
)
def test_legacy_explicit_no_is_recognized(legacy_value: str) -> None:
    result = subjective_context.subjective_context_from_legacy_objective(
        {"injuryPain": legacy_value}
    )
    assert result.context.pain_or_injury.status == "explicit_no"
    assert result.context.pain_or_injury.description is None
    assert [item.code for item in result.diagnostics] == [
        "legacy_explicit_no_recognized"
    ]


def test_legacy_injury_text_is_preserved_conservatively() -> None:
    result = subjective_context.subjective_context_from_legacy_objective(
        {"injuryPain": "Gêne au genou après les sorties longues."}
    )
    assert result.context.pain_or_injury.status == "explicit_yes"
    assert result.context.pain_or_injury.description is not None
    assert [item.code for item in result.diagnostics] == [
        "legacy_injury_text_assumed_affecting_training"
    ]


def test_legacy_fatigue_text_is_not_turned_into_recovery_score() -> None:
    result = subjective_context.subjective_context_from_legacy_objective(
        {"injuryPain": "Très fatigué cette semaine"}
    )
    payload = result.context.model_dump(mode="json")
    assert result.context.pain_or_injury.status == "explicit_yes"
    assert "recovery_state" not in payload
    assert "fatigue_score" not in payload


def test_long_legacy_injury_is_truncated_with_diagnostic() -> None:
    result = subjective_context.subjective_context_from_legacy_objective(
        {"injuryPain": "x" * 400}
    )
    assert len(result.context.pain_or_injury.description or "") == 300
    assert {item.code for item in result.diagnostics} == {
        "legacy_injury_text_assumed_affecting_training",
        "legacy_injury_description_truncated",
    }


def test_legacy_preferences_are_preserved() -> None:
    result = subjective_context.subjective_context_from_legacy_objective(
        {"preferences": "Éviter deux jours consécutifs."}
    )
    assert result.context.important_preferences == [
        "Éviter deux jours consécutifs."
    ]
    assert "legacy_preference_preserved" in {
        item.code for item in result.diagnostics
    }


def test_legacy_constraints_are_diagnostic_not_contract_debt() -> None:
    result = subjective_context.subjective_context_from_legacy_objective(
        {"constraints": "Indisponible le mercredi"}
    )
    payload = result.context.model_dump(mode="json")
    assert "constraints" not in payload
    assert "Indisponible" not in json.dumps(payload)
    assert "legacy_constraint_unmapped" in {
        item.code for item in result.diagnostics
    }


def test_goal_and_availability_fields_are_ignored() -> None:
    base: dict[str, Any] = {}
    polluted = {
        "goalType": "race",
        "title": "Private race",
        "distanceKm": 42.195,
        "raceDate": "2027-04-18",
        "goal_target_time": "02:59:59",
        "availableDays": ["tue", "thu", "sat"],
        "sessionsPerWeek": 3,
    }
    assert subjective_context.subjective_context_from_legacy_objective(
        base
    ) == subjective_context.subjective_context_from_legacy_objective(polluted)


def test_contract_has_no_temporary_notes_or_measured_metrics() -> None:
    payload = subjective_context.build_subjective_context_v1(
        affecting_training=False
    ).model_dump(mode="json")
    for forbidden in (
        "temporary_notes",
        "vo2max",
        "training_load",
        "heart_rate",
        "goal",
        "availability",
    ):
        assert forbidden not in payload


def test_determinism() -> None:
    legacy = {
        "injuryPain": "Aucune douleur actuelle.",
        "preferences": "Garder une séance de vélo.",
    }
    assert subjective_context.subjective_context_from_legacy_objective(
        legacy
    ) == subjective_context.subjective_context_from_legacy_objective(dict(legacy))


def test_privacy_and_no_files_network_garmin_or_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = {
        "injuryPain": "",
        "name": "Private name",
        "email": "private@example.com",
        "address": "Private address",
        "garminId": "12345",
        "latitude": 50.0,
        "longitude": 4.0,
        "token": "secret-token",
        "cookie": "secret-cookie",
        "mfa": "654321",
    }

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("External access is forbidden")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = subjective_context.subjective_context_from_legacy_objective(legacy)
    payload = json.dumps(
        subjective_context.subjective_context_for_ai(result.context)
    ).lower()
    for forbidden_value in (
        "private",
        "email",
        "address",
        "garmin",
        "latitude",
        "longitude",
        "token",
        "cookie",
        "mfa",
        "12345",
        "654321",
    ):
        assert forbidden_value not in payload


def test_exact_json_contract() -> None:
    result = subjective_context.build_subjective_context_v1(
        affecting_training=True,
        pain_description="Gêne au genou.",
        important_preferences=["Éviter deux jours consécutifs."],
        unrecorded_activities=[
            {
                "sport": "strength",
                "sessions_per_week": 2,
                "typical_duration_minutes": 60,
            }
        ],
        reported_at="2026-09-30T18:30:00+02:00",
    )
    assert result.model_dump(mode="json") == {
        "schema_version": 1,
        "pain_or_injury": {
            "status": "explicit_yes",
            "affecting_training": True,
            "description": "Gêne au genou.",
        },
        "important_preferences": ["Éviter deux jours consécutifs."],
        "unrecorded_activities": [
            {
                "source": "user_reported_unrecorded",
                "sport": "strength",
                "sessions_per_week": 2,
                "typical_duration_minutes": 60,
            }
        ],
        "reported_at": "2026-09-30T18:30:00+02:00",
    }
