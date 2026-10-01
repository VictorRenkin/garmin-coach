from __future__ import annotations

import builtins
import json
import socket
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from garmin_coach import athlete_state, config, data_confidence, history_import, history_storage
from garmin_coach import training_history_summary as summary_module
from garmin_coach.goal import GoalV1, build_goal_v1
from garmin_coach.goal_evidence import (
    RIEGEL_EXPONENT_V1,
    build_goal_evidence_v1,
    goal_evidence_for_ai,
)


REFERENCE = date(2026, 9, 30)


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    return tmp_path


def activity(
    activity_id: str,
    day: date,
    *,
    distance_m: float = 6000,
    duration_s: float = 2100,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "activityId": activity_id,
        "activityName": f"Private runner and place {activity_id}",
        "activityType": {"typeKey": "running"},
        "startTimeLocal": f"{day.isoformat()}T08:00:00",
        "distance": distance_m,
        "movingDuration": duration_s,
        **extra,
    }


def contracts(
    user_id: str,
    activities: list[dict[str, Any]],
) -> tuple[
    athlete_state.AthleteState,
    summary_module.TrainingHistorySummary,
    data_confidence.DataConfidence,
]:
    with config.user_context(user_id):
        history_import.initialize_history_import("all", reference_date=REFERENCE)
        for item in activities:
            history_storage.save_activity_metadata(item)
        summary = summary_module.build_training_history_summary(reference_date=REFERENCE)
    confidence = data_confidence.build_data_confidence(summary, reference_date=REFERENCE)
    athlete = athlete_state.build_athlete_state(
        summary, confidence, reference_date=REFERENCE
    )
    return athlete, summary, confidence


def rich_history(*, evidence: bool = True) -> list[dict[str, Any]]:
    items = [
        activity(
            f"run-{index}",
            REFERENCE - timedelta(days=index * 4),
            distance_m=6000 + index * 300,
            duration_s=2100 + index * 100,
        )
        for index in range(18)
    ]
    if evidence:
        items[0].update(
            fastestSplit_1000=230.0,
            fastestSplit_5000=1200.0,
            fastestSplit_10000=2520.0,
        )
    return items


def seconds_to_target(seconds: float) -> str:
    total = max(1, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}"


def road_goal(
    event_type: str = "10k",
    *,
    race_date: date | None = REFERENCE + timedelta(days=30),
    target_seconds: float = 2600,
) -> GoalV1:
    return build_goal_v1(
        "race",
        event_type=event_type,  # type: ignore[arg-type]
        race_date=race_date,
        target_time=seconds_to_target(target_seconds),
    )


def assess(
    goal: GoalV1,
    inputs: tuple[
        athlete_state.AthleteState,
        summary_module.TrainingHistorySummary,
        data_confidence.DataConfidence,
    ],
    *,
    diagnostics: dict[str, Any] | None = None,
):
    athlete, summary, confidence = inputs
    return build_goal_evidence_v1(
        goal,
        athlete,
        summary,
        confidence,
        reference_date=REFERENCE,
        diagnostics=diagnostics,
    )


@pytest.mark.parametrize("intent", ["progress", "beginner", "comeback"])
def test_non_race_intentions_have_no_timed_comparison(intent: str) -> None:
    inputs = contracts(f"u_{intent}", [] if intent == "beginner" else rich_history())
    result = assess(build_goal_v1(intent), inputs)  # type: ignore[arg-type]

    assert result.assessment_scope == "non_race"
    assert result.status == "not_applicable"
    assert result.performance_comparison.status == "not_applicable"


@pytest.mark.parametrize(
    ("event_type", "distance_km"),
    [
        ("5k", 5.0),
        ("10k", 10.0),
        ("half_marathon", 21.0975),
        ("marathon", 42.195),
    ],
)
def test_standard_road_events_use_explicit_riegel_comparison(
    event_type: str, distance_km: float
) -> None:
    inputs = contracts(f"u_{event_type}", rich_history())
    reference_distance, reference_time = (
        (5000, 1200) if event_type == "5k" else (10000, 2520)
    )
    equivalent = reference_time * (
        distance_km * 1000 / reference_distance
    ) ** RIEGEL_EXPONENT_V1
    result = assess(road_goal(event_type, target_seconds=equivalent + 60), inputs)

    assert result.status == "available"
    assert result.race.distance_km == distance_km
    assert result.performance_comparison.method == "riegel_v1"
    assert result.performance_comparison.status == "available"
    assert result.performance_comparison.exponent == RIEGEL_EXPONENT_V1
    assert result.performance_comparison.reference is not None


@pytest.mark.parametrize("event_type", ["trail", "custom"])
def test_trail_and_custom_are_never_blindly_extrapolated(event_type: str) -> None:
    inputs = contracts(f"u_{event_type}", rich_history())
    goal = build_goal_v1(
        "race",
        event_type=event_type,  # type: ignore[arg-type]
        distance_km=25,
        race_date=REFERENCE + timedelta(days=40),
        target_time="02:00:00",
    )
    result = assess(goal, inputs)

    assert result.status == "partial"
    assert result.performance_comparison.status == "insufficient_data"
    assert result.performance_comparison.method is None
    assert "course_specific_comparison_unavailable" in result.limitations


def test_race_without_target_time_is_not_applicable_and_keeps_training_facts() -> None:
    inputs = contracts("u_no_time", rich_history())
    goal = build_goal_v1(
        "race", event_type="half_marathon", race_date=REFERENCE + timedelta(days=60)
    )
    result = assess(goal, inputs)

    assert result.assessment_scope == "race_without_target_time"
    assert result.status == "partial"
    assert result.performance_comparison.status == "not_applicable"
    assert "race_target_time_missing" in result.limitations
    assert result.race.target_time_seconds is None
    assert result.training_support.running_sessions_last_84_days == 18


def test_race_without_date_can_compare_capacity_but_not_deadline_feasibility() -> None:
    inputs = contracts("u_no_date", rich_history())
    result = assess(road_goal(race_date=None, target_seconds=2600), inputs)

    assert result.assessment_scope == "race_without_date"
    assert result.status == "partial"
    assert result.performance_comparison.status == "available"
    assert "race_date_missing" in result.limitations
    assert result.race.days_to_event is None


def test_large_gap_without_date_is_exposed_as_a_fact_without_a_verdict() -> None:
    inputs = contracts("u_no_date_large_gap", rich_history())
    result = assess(road_goal(race_date=None, target_seconds=2200), inputs)

    assert result.status == "partial"
    assert result.performance_comparison.status == "available"
    assert result.performance_comparison.target_vs_equivalent_seconds == -320
    assert result.performance_comparison.target_vs_equivalent_pct == pytest.approx(-12.698, abs=0.001)
    assert "race_date_missing" in result.limitations


def test_past_date_is_exposed_as_a_limitation_without_sporting_verdict() -> None:
    inputs = contracts("u_past", rich_history())
    result = assess(
        road_goal(race_date=REFERENCE - timedelta(days=1), target_seconds=2500), inputs
    )

    assert result.status == "partial"
    assert "race_date_passed" in result.limitations
    assert result.performance_comparison.status == "available"
    assert result.performance_comparison.reference is not None


def test_race_today_exposes_zero_days_and_comparison_facts() -> None:
    inputs = contracts("u_today", rich_history())
    result = assess(road_goal(race_date=REFERENCE, target_seconds=2200), inputs)

    assert result.race.days_to_event == 0
    assert result.status == "available"
    assert result.performance_comparison.status == "available"


def test_target_gap_is_reported_without_a_deterministic_sporting_verdict() -> None:
    inputs = contracts("u_statuses", rich_history())
    equivalent = 2520
    slower = assess(road_goal(target_seconds=equivalent + 1), inputs)
    slightly_faster = assess(road_goal(target_seconds=equivalent * 0.97), inputs)
    much_faster = assess(road_goal(target_seconds=equivalent * 0.90), inputs)

    assert {item.status for item in (slower, slightly_faster, much_faster)} == {"available"}
    assert {
        item.performance_comparison.status
        for item in (slower, slightly_faster, much_faster)
    } == {"available"}
    assert slower.performance_comparison.target_vs_equivalent_seconds == 1
    assert slightly_faster.performance_comparison.target_vs_equivalent_seconds < 0
    assert much_faster.performance_comparison.target_vs_equivalent_seconds < 0
    allowed_statuses = {"available", "partial", "insufficient_data", "not_applicable"}
    assert all(
        item.status in allowed_statuses
        and item.performance_comparison.status in allowed_statuses
        for item in (slower, slightly_faster, much_faster)
    )


def test_no_performance_evidence_is_technically_insufficient() -> None:
    inputs = contracts("u_no_evidence", rich_history(evidence=False))
    result = assess(road_goal(target_seconds=3600), inputs)

    assert result.status == "insufficient_data"
    assert result.performance_comparison.reference is None
    assert result.performance_comparison.status == "insufficient_data"
    assert "performance_evidence_insufficient" in result.limitations


def test_old_structured_performance_is_kept_as_partial_low_confidence_evidence() -> None:
    inputs = contracts(
        "u_old_evidence",
        [activity("old", REFERENCE - timedelta(days=400), fastestSplit_5000=1200)],
    )
    original_evidence = inputs[1].running.performance_evidence[0].model_dump(
        mode="json"
    )
    result = assess(road_goal(target_seconds=3000), inputs)

    assert result.confidence.performance_evidence == "low"
    assert result.status == "partial"
    assert result.performance_comparison.status == "partial"
    assert result.performance_comparison.reference is not None
    assert result.performance_comparison.reference.duration_seconds == 1200
    assert result.performance_comparison.reference.recency == "historical"
    assert result.performance_comparison.equivalent_target_distance_seconds == pytest.approx(
        1200 * 2**RIEGEL_EXPONENT_V1,
        abs=0.01,
    )
    assert result.performance_comparison.target_vs_equivalent_seconds is not None
    assert "historical_performance_reference" in result.limitations
    assert "performance_evidence_low_confidence" in result.limitations
    assert inputs[1].running.performance_evidence[0].model_dump(mode="json") == original_evidence


def test_reference_selection_prefers_recency_before_distance_relevance() -> None:
    activities = rich_history(evidence=False)
    activities[0].update(fastestSplit_10000=2700)
    activities.append(
        activity(
            "older-close",
            REFERENCE - timedelta(days=170),
            fastestSplit_5000=1100,
        )
    )
    inputs = contracts("u_selection", activities)
    result = assess(
        road_goal("half_marathon", target_seconds=6500), inputs
    )

    reference = result.performance_comparison.reference
    assert reference is not None
    assert reference.distance_m == 10000
    assert reference.age_days == 0


def test_recent_same_distance_evidence_beats_an_older_faster_record() -> None:
    activities = rich_history(evidence=False)
    activities[0].update(isPR=True, distance=5000, movingDuration=1250)
    activities.append(
        activity(
            "old-faster-record",
            REFERENCE - timedelta(days=300),
            distance_m=5000,
            duration_s=1100,
            isPR=True,
        )
    )
    inputs = contracts("u_recent_over_record", activities)
    result = assess(road_goal("5k", target_seconds=1300), inputs)

    reference = result.performance_comparison.reference
    assert reference is not None
    assert reference.age_days == 0
    assert reference.duration_seconds == 1250


@pytest.mark.parametrize("evidence_count", [1, 3])
def test_medium_and_high_performance_confidence_allow_comparison(
    evidence_count: int,
) -> None:
    activities = rich_history(evidence=False)
    activities[0]["fastestSplit_5000"] = 1200
    if evidence_count == 3:
        activities[0].update(fastestSplit_1000=230, fastestSplit_10000=2520)
    inputs = contracts(f"u_confidence_{evidence_count}", activities)
    result = assess(road_goal(target_seconds=2700), inputs)

    assert result.confidence.performance_evidence in {"medium", "high"}
    assert result.status == "available"


def test_weak_history_with_usable_performance_is_limited_not_falsely_rejected() -> None:
    inputs = contracts(
        "u_weak_history",
        [activity("only", REFERENCE, fastestSplit_5000=1200)],
    )
    result = assess(road_goal("5k", target_seconds=1300), inputs)

    assert result.status == "available"
    assert result.confidence.training_history in {"low", "medium"}
    assert "limited_training_history" in result.limitations


def test_stale_current_state_is_an_explicit_limitation_not_fatigue_invention() -> None:
    activities = [
        activity(
            "stale",
            REFERENCE - timedelta(days=40),
            fastestSplit_5000=1200,
            fastestSplit_10000=2520,
            fastestSplit_1000=230,
        )
    ]
    inputs = contracts("u_stale", activities)
    result = assess(road_goal(target_seconds=2700), inputs)

    assert "current_state_stale" in result.limitations
    assert result.status == "available"
    assert not any("fatigue" in item for item in result.limitations)


def test_training_load_recovery_long_run_and_interruptions_are_factual_context() -> None:
    activities = rich_history()
    activities.append(activity("old-gap", REFERENCE - timedelta(days=150)))
    inputs = contracts("u_context_facts", activities)
    result = assess(road_goal("half_marathon", target_seconds=7000), inputs)

    assert "training_load_unavailable" in result.limitations
    assert "recovery_unavailable" in result.limitations
    assert result.training_support.longest_run_last_84_days_km is not None
    assert result.training_support.long_run_ratio_to_event is not None
    assert result.training_support.recorded_interruptions_count >= 1


def test_long_run_absence_is_explicit_without_universal_distance_rule() -> None:
    athlete, summary, confidence = contracts("u_long_absent", rich_history())
    running = summary.running.model_copy(
        update={
            "long_runs": summary.running.long_runs.model_copy(
                update={
                    "longest_distance_km": None,
                    "distance_percentile_75_km": None,
                    "top_three": [],
                }
            )
        }
    )
    modified = summary.model_copy(update={"running": running})
    result = assess(road_goal(target_seconds=2700), (athlete, modified, confidence))

    assert result.training_support.longest_run_last_84_days_km is None
    assert "long_run_unavailable" in result.limitations


@pytest.mark.parametrize(
    ("source_distance", "event_type", "event_distance"),
    [
        (5000, "10k", 10.0),
        (10000, "half_marathon", 21.0975),
        (21097.5, "marathon", 42.195),
    ],
)
def test_riegel_equivalence_pairs(
    source_distance: float, event_type: str, event_distance: float
) -> None:
    key = {
        5000: "fastestSplit_5000",
        10000: "fastestSplit_10000",
    }.get(source_distance)
    activities = rich_history(evidence=False)
    if key is not None:
        activities[0][key] = 2400
    else:
        activities[0].update(
            distance=source_distance,
            movingDuration=2400,
            isPR=True,
        )
    inputs = contracts(f"u_riegel_{int(source_distance)}", activities)
    expected = 2400 * (event_distance * 1000 / source_distance) ** RIEGEL_EXPONENT_V1
    result = assess(road_goal(event_type, target_seconds=expected + 30), inputs)

    assert result.performance_comparison.equivalent_target_distance_seconds == pytest.approx(
        expected, abs=0.01
    )


def test_builder_is_deterministic_and_uses_only_explicit_reference_date() -> None:
    inputs = contracts("u_deterministic", rich_history())
    goal = road_goal(target_seconds=2600)

    first = assess(goal, inputs)
    second = assess(goal, inputs)

    assert first == second
    assert first.as_of == REFERENCE.isoformat()


def test_reference_date_mismatch_is_a_safe_technical_error() -> None:
    inputs = contracts("u_mismatch", rich_history())
    athlete, summary, confidence = inputs
    with pytest.raises(ValueError, match="alignés"):
        build_goal_evidence_v1(
            road_goal(),
            athlete,
            summary,
            confidence,
            reference_date=REFERENCE + timedelta(days=1),
        )


def test_builder_has_no_file_network_garmin_or_openai_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = contracts("u_pure", rich_history())

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("external access is forbidden")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = assess(road_goal(), inputs)

    assert result.schema_version == 1


def test_output_is_compact_and_contains_no_raw_identity_gps_or_secret() -> None:
    activities = rich_history()
    activities[0].update(
        latitude=50.123,
        longitude=4.456,
        ownerEmail="private@example.test",
        token="oauth-secret",
    )
    inputs = contracts("u_private", activities)
    result = assess(road_goal(), inputs)
    payload = json.dumps(goal_evidence_for_ai(result), sort_keys=True).lower()

    for forbidden in (
        "private runner",
        "private@example.test",
        "latitude",
        "longitude",
        "oauth-secret",
        "activityid",
    ):
        assert forbidden not in payload


def test_builder_is_very_light_and_json_is_compact() -> None:
    inputs = contracts("u_perf", rich_history())
    diagnostics: dict[str, Any] = {}
    started = time.perf_counter()
    result = assess(road_goal(), inputs, diagnostics=diagnostics)
    elapsed = time.perf_counter() - started
    size = len(result.model_dump_json().encode("utf-8"))

    assert elapsed < 0.05
    assert diagnostics["computation_seconds"] < 0.05
    assert size < 10_000
