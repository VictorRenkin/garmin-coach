from __future__ import annotations

import builtins
import json
import socket
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from garmin_coach import (
    athlete_state,
    availability,
    config,
    data_confidence,
    goal,
    history_import,
    history_storage,
    motionest_context,
    subjective_context,
)
from garmin_coach import training_history_summary as summary_module


REFERENCE = date(2026, 9, 30)


@pytest.fixture
def integrated_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    user_id = "u_context"
    with config.user_context(user_id):
        history_import.initialize_history_import("all", reference_date=REFERENCE)
        for index in range(18):
            sport = "cycling" if index % 5 == 0 else "running"
            activity: dict[str, Any] = {
                "activityId": f"a-{index}",
                "activityType": {"typeKey": sport},
                "activityName": f"Private activity {index}",
                "startTimeLocal": f"{(REFERENCE - timedelta(days=index * 4)).isoformat()}T08:00:00",
                "distance": 15000 if sport == "cycling" else 6000 + index * 100,
                "movingDuration": 2400,
                "averageHR": 145,
                "maxHR": 172,
                "activityTrainingLoad": 70,
            }
            if sport == "running":
                activity.update(
                    hrTimeInZone_1=200,
                    hrTimeInZone_2=1200,
                    aerobicTrainingEffect=2.8,
                    anaerobicTrainingEffect=0.0,
                )
            history_storage.save_activity_metadata(activity)
        summary = summary_module.build_training_history_summary(
            reference_date=REFERENCE
        )
    selected_goal = goal.build_goal_v1(
        "race",
        event_type="half_marathon",
        race_date="2027-04-18",
        target_time="02:00:00",
    )
    confidence = data_confidence.build_data_confidence(
        summary,
        objective=goal.goal_context_for_legacy_confidence(selected_goal),
        reference_date=REFERENCE,
    )
    athlete = athlete_state.build_athlete_state(
        summary, confidence, reference_date=REFERENCE
    )
    return {
        "goal": selected_goal,
        "availability": availability.build_availability_v1(
            ["tue", "thu", "sat"], preferred_sessions_per_week=3
        ),
        "subjective_context": subjective_context.build_subjective_context_v1(
            affecting_training=False
        ),
        "training_history_summary": summary,
        "data_confidence": confidence,
        "athlete_state": athlete,
        "reference_date": REFERENCE,
    }


def build(inputs: dict[str, Any], **updates: Any) -> motionest_context.MotionestContextV1:
    return motionest_context.build_motionest_context_v1(
        **{**inputs, **updates}
    )


def test_all_valid_contracts_are_integrated(
    integrated_inputs: dict[str, Any],
) -> None:
    result = build(integrated_inputs)
    assert result.integration.status == "ready_with_limitations"
    assert result.integration.blocking_issues == []
    assert result.goal and result.goal.intent == "race"
    assert result.athlete_state.recent_activity_count > 0


def test_goal_absent_is_blocking(integrated_inputs: dict[str, Any]) -> None:
    result = build(integrated_inputs, goal=None)
    assert result.integration.status == "insufficient"
    assert "goal_absent" in {item.code for item in result.integration.blocking_issues}


def test_availability_absent_is_blocking(
    integrated_inputs: dict[str, Any],
) -> None:
    result = build(integrated_inputs, availability=None)
    assert result.integration.status == "insufficient"
    assert "availability_absent" in {
        item.code for item in result.integration.blocking_issues
    }


def test_unknown_subjective_pain_is_partial_not_blocking(
    integrated_inputs: dict[str, Any],
) -> None:
    unknown = subjective_context.build_subjective_context_v1(
        affecting_training=None
    )
    result = build(integrated_inputs, subjective_context=unknown)
    assert result.integration.status == "partial"
    assert result.integration.blocking_issues == []
    assert result.integration.safety_context.pain_status == "unknown"
    assert "pain_or_injury_unknown" in {
        item.code for item in result.integration.limitations
    }


def test_explicit_no_pain_is_preserved(integrated_inputs: dict[str, Any]) -> None:
    result = build(integrated_inputs)
    safety = result.integration.safety_context
    assert safety.pain_status == "explicit_no"
    assert safety.pain_or_injury_affecting_training is False


def test_explicit_yes_pain_produces_nonblocking_safety_signal(
    integrated_inputs: dict[str, Any],
) -> None:
    reported = subjective_context.build_subjective_context_v1(
        affecting_training=True, pain_description="Gêne au genou."
    )
    result = build(integrated_inputs, subjective_context=reported)
    assert result.integration.status == "ready_with_limitations"
    assert result.integration.safety_context.pain_or_injury_affecting_training is True
    pain_issue = next(
        item
        for item in result.integration.limitations
        if item.code == "pain_or_injury_affecting_training"
    )
    assert pain_issue.blocking is False


def test_rich_history_is_exposed_without_rescan(
    integrated_inputs: dict[str, Any],
) -> None:
    result = build(integrated_inputs)
    assert result.training_history_summary.activity_count == 18
    assert result.data_confidence.training_history == "high"
    assert result.athlete_state.status == "available"


def test_insufficient_athlete_state_is_blocking(
    integrated_inputs: dict[str, Any],
) -> None:
    insufficient = integrated_inputs["athlete_state"].model_copy(
        update={"status": "insufficient_data"}
    )
    result = build(integrated_inputs, athlete_state=insufficient)
    assert result.integration.status == "insufficient"
    assert "athlete_state_insufficient" in {
        item.code for item in result.integration.blocking_issues
    }


def test_recovery_unavailable_is_nonblocking(
    integrated_inputs: dict[str, Any],
) -> None:
    result = build(integrated_inputs)
    assert result.data_confidence.recovery == "none"
    recovery_codes = {
        item.code for item in result.integration.limitations if "recovery" in item.code
    }
    assert recovery_codes
    assert result.integration.status != "insufficient"


def test_training_load_unavailable_is_nonblocking(
    integrated_inputs: dict[str, Any],
) -> None:
    confidence = integrated_inputs["data_confidence"]
    load = confidence.components.training_load.model_copy(
        update={"confidence": "none", "status": "unavailable"}
    )
    components = confidence.components.model_copy(update={"training_load": load})
    missing = [
        *confidence.missing_data,
        data_confidence.DataIssue(
            code="training_load_unavailable", component="training_load"
        ),
    ]
    modified = confidence.model_copy(
        update={"components": components, "missing_data": missing}
    )
    athlete = integrated_inputs["athlete_state"].model_copy(
        update={
            "confidence": integrated_inputs["athlete_state"].confidence.model_copy(
                update={"training_load": "none"}
            )
        }
    )
    result = build(
        integrated_inputs, data_confidence=modified, athlete_state=athlete
    )
    assert "training_load_unavailable" in {
        item.code for item in result.integration.limitations
    }
    assert result.integration.status != "insufficient"


def test_partial_sport_classification_is_nonblocking(
    integrated_inputs: dict[str, Any],
) -> None:
    limitation = athlete_state.AthleteLimitation(
        code="sport_classification_partial",
        affects=["sport_profile"],
        affected_activities=2,
    )
    athlete = integrated_inputs["athlete_state"].model_copy(
        update={
            "limitations": [
                *integrated_inputs["athlete_state"].limitations,
                limitation,
            ]
        }
    )
    result = build(integrated_inputs, athlete_state=athlete)
    assert "sport_classification_partial" in {
        item.code for item in result.integration.limitations
    }
    assert result.integration.status != "insufficient"


@pytest.mark.parametrize(
    "selected_goal",
    [
        goal.build_goal_v1("race", event_type="10k"),
        goal.build_goal_v1("race", event_type="10k", race_date="2027-01-10"),
    ],
)
def test_race_without_time_or_date_is_not_blocking(
    integrated_inputs: dict[str, Any], selected_goal: goal.GoalV1
) -> None:
    result = build(integrated_inputs, goal=selected_goal)
    assert result.integration.status != "insufficient"


@pytest.mark.parametrize("intent", ["progress", "beginner", "comeback"])
def test_non_race_goals_integrate_identically(
    integrated_inputs: dict[str, Any], intent: str
) -> None:
    selected_goal = goal.build_goal_v1(intent)  # type: ignore[arg-type]
    result = build(integrated_inputs, goal=selected_goal)
    assert result.goal and result.goal.intent == intent
    assert result.integration.status != "insufficient"


def test_preferred_sessions_null_is_valid_and_not_inferred(
    integrated_inputs: dict[str, Any],
) -> None:
    declared = availability.build_availability_v1(["mon", "wed", "fri"])
    result = build(integrated_inputs, availability=declared)
    frequency = result.integration.frequency_context
    assert frequency.preferred_sessions_per_week is None
    assert frequency.preferred_minus_observed_sessions is None


def test_preferred_and_observed_frequency_are_exposed_without_judgment(
    integrated_inputs: dict[str, Any],
) -> None:
    result = build(integrated_inputs)
    frequency = result.integration.frequency_context
    assert frequency.preferred_sessions_per_week == 3
    assert frequency.observed_sessions_per_week_last_28_days >= 0
    assert frequency.preferred_minus_observed_sessions is not None
    payload = json.dumps(frequency.model_dump()).lower()
    assert "recommended" not in payload


def test_unrecorded_activities_remain_user_reported(
    integrated_inputs: dict[str, Any],
) -> None:
    subjective = subjective_context.build_subjective_context_v1(
        affecting_training=False,
        unrecorded_activities=[
            {"sport": "strength", "sessions_per_week": 2}
        ],
    )
    result = build(integrated_inputs, subjective_context=subjective)
    assert result.subjective_context.unrecorded_activities[0].source == (
        "user_reported_unrecorded"
    )
    assert result.integration.safety_context.unrecorded_activities_count == 1


def test_reference_date_contradiction_is_blocking(
    integrated_inputs: dict[str, Any],
) -> None:
    contradictory = integrated_inputs["athlete_state"].model_copy(
        update={"as_of": "2026-09-29"}
    )
    result = build(integrated_inputs, athlete_state=contradictory)
    assert result.integration.status == "insufficient"
    assert "source_reference_date_mismatch" in {
        item.code for item in result.integration.blocking_issues
    }


def test_summary_athlete_contradiction_is_blocking(
    integrated_inputs: dict[str, Any],
) -> None:
    current = integrated_inputs["athlete_state"].current_training.model_copy(
        update={"observed_sessions_per_week_last_28_days": 6.5}
    )
    contradictory = integrated_inputs["athlete_state"].model_copy(
        update={"current_training": current}
    )
    result = build(integrated_inputs, athlete_state=contradictory)
    assert "athlete_state_summary_mismatch" in {
        item.code for item in result.integration.blocking_issues
    }


def test_context_is_deterministic(integrated_inputs: dict[str, Any]) -> None:
    first = build(integrated_inputs)
    second = build(dict(integrated_inputs))
    assert first == second


def test_context_is_fast_and_uses_only_existing_objects(
    integrated_inputs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("External access is forbidden")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    diagnostics: dict[str, Any] = {}
    started = time.perf_counter()
    result = motionest_context.build_motionest_context_v1(
        **integrated_inputs, diagnostics=diagnostics
    )
    elapsed = time.perf_counter() - started
    assert result.integration.status != "insufficient"
    assert elapsed < 0.05
    assert diagnostics["computation_seconds"] < 0.05


def test_ai_safe_context_contains_no_identity_or_raw_history(
    integrated_inputs: dict[str, Any],
) -> None:
    result = build(integrated_inputs)
    payload = json.dumps(
        motionest_context.motionest_context_for_ai(result)
    ).lower()
    for forbidden in (
        "private activity",
        "activityid",
        "email",
        "latitude",
        "longitude",
        "token",
        "cookie",
        "garminuserid",
    ):
        assert forbidden not in payload
    assert "activities" not in result.training_history_summary.model_dump()


def test_exact_top_level_contract(integrated_inputs: dict[str, Any]) -> None:
    result = build(integrated_inputs).model_dump(mode="json")
    assert set(result) == {
        "schema_version",
        "as_of",
        "goal",
        "availability",
        "subjective_context",
        "athlete_state",
        "training_history_summary",
        "data_confidence",
        "integration",
    }
    assert result["schema_version"] == 1
