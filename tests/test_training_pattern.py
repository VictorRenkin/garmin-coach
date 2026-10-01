from __future__ import annotations

import builtins
import json
import socket
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from garmin_coach import config, data_confidence, history_import, history_storage
from garmin_coach import training_history_summary as summary_module
from garmin_coach import training_pattern as pattern_module
from garmin_coach.training_pattern import (
    TrainingPatternSessionInput,
    build_training_pattern_evidence_v1,
    collect_local_training_pattern_inputs,
    training_pattern_for_ai,
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
    sport: str = "running",
    distance_m: float | None = 6000,
    duration_s: float | None = 2100,
    **extra: Any,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "activityId": activity_id,
        "activityName": f"Private place and person {activity_id}",
        "activityType": {"typeKey": sport},
        "startTimeLocal": f"{day.isoformat()}T08:00:00",
        **extra,
    }
    if distance_m is not None:
        item["distance"] = distance_m
    if duration_s is not None:
        item["movingDuration"] = duration_s
    return item


def contracts(
    user_id: str, activities: list[dict[str, Any]]
) -> tuple[summary_module.TrainingHistorySummary, data_confidence.DataConfidence]:
    with config.user_context(user_id):
        history_import.initialize_history_import("all", reference_date=REFERENCE)
        for item in activities:
            history_storage.save_activity_metadata(item)
        summary = summary_module.build_training_history_summary(reference_date=REFERENCE)
    confidence = data_confidence.build_data_confidence(summary, reference_date=REFERENCE)
    return summary, confidence


def session(
    offset: int,
    *,
    distance_m: float | None = 6000,
    duration_seconds: float | None = 2100,
    splits: tuple[str, ...] = (),
    inspected: bool = True,
    event: str | None = "uncategorized",
) -> TrainingPatternSessionInput:
    return TrainingPatternSessionInput(
        date=REFERENCE - timedelta(days=offset),
        distance_m=distance_m,
        duration_seconds=duration_seconds,
        typed_split_types=splits,
        typed_splits_inspected=inspected,
        event_type=event,
    )


def build(
    activities: list[dict[str, Any]],
    sessions: list[TrainingPatternSessionInput],
    *,
    user_id: str = "pattern_user",
):
    summary, confidence = contracts(user_id, activities)
    return build_training_pattern_evidence_v1(
        summary, confidence, sessions, reference_date=REFERENCE
    )


def four_runs(**first_extra: Any) -> list[dict[str, Any]]:
    distances = (4000, 6000, 8000, 12000)
    return [
        activity(
            f"run-{index}",
            REFERENCE - timedelta(days=index * 3),
            distance_m=distance,
            **(first_extra if index == 0 else {}),
        )
        for index, distance in enumerate(distances)
    ]


def four_sessions(**first_extra: Any) -> list[TrainingPatternSessionInput]:
    distances = (4000, 6000, 8000, 12000)
    return [
        session(index * 3, distance_m=distance, **(first_extra if index == 0 else {}))
        for index, distance in enumerate(distances)
    ]


def test_empty_history_and_no_running_are_safe() -> None:
    empty = build([], [])
    cycling = build(
        [activity("bike", REFERENCE, sport="cycling")], [], user_id="cycling_user"
    )

    for result in (empty, cycling):
        assert result.running.sessions_last_84_days == 0
        assert result.pattern_coverage.eligible_sessions == 0
        assert result.session_patterns.structured_intervals.status == "not_applicable"
        assert result.past_race_evidence == []


def test_running_without_details_has_only_relative_pattern() -> None:
    activities = four_runs()
    sessions = [
        session(index * 3, distance_m=distance, inspected=False, event=None)
        for index, distance in enumerate((4000, 6000, 8000, 12000))
    ]
    result = build(activities, sessions)

    assert result.pattern_coverage.eligible_sessions == 4
    assert result.pattern_coverage.sessions_without_observed_pattern == 3
    assert result.session_patterns.structured_intervals.status == "insufficient_data"
    assert "typed_splits_unavailable" in result.limitations
    assert result.intensity_evidence.heart_rate_zone_coverage.available_count == 0
    assert result.intensity_evidence.aerobic_training_effect_coverage.available_count == 0
    assert result.intensity_evidence.anaerobic_training_effect_coverage.available_count == 0


def test_explicit_repeated_typed_splits_classify_structured_intervals() -> None:
    splits = (
        "INTERVAL_WARMUP",
        "INTERVAL_ACTIVE",
        "INTERVAL_RECOVERY",
        "INTERVAL_ACTIVE",
        "INTERVAL_COOLDOWN",
    )
    result = build(four_runs(), four_sessions(splits=splits))

    evidence = result.session_patterns.structured_intervals
    assert evidence.count_84_days == 1
    assert evidence.status == "observed"
    assert evidence.provenance == ["garmin_typed_splits"]
    assert evidence.confidence == "high"


def test_pace_variation_or_single_active_split_does_not_imply_intervals() -> None:
    activities = four_runs(fastestSplit_1000=215.0, averageSpeed=4.2)
    sessions = four_sessions(splits=("INTERVAL_ACTIVE", "INTERVAL_COOLDOWN"))
    result = build(activities, sessions)

    assert result.session_patterns.structured_intervals.count_84_days == 0
    assert result.session_patterns.structured_intervals.status == "not_observed"


def test_relative_long_run_uses_personal_distribution_not_fixed_distance() -> None:
    result = build(four_runs(), four_sessions())

    evidence = result.session_patterns.relative_long_run
    assert evidence.status == "observed"
    assert evidence.count_84_days == 1
    assert evidence.provenance == ["deterministic_personal_distance_distribution"]
    assert evidence.confidence == "medium"
    assert result.pattern_coverage.sessions_without_observed_pattern == 3


def test_session_with_two_patterns_counts_once_in_pattern_coverage() -> None:
    repeated = (
        "INTERVAL_ACTIVE",
        "INTERVAL_RECOVERY",
        "INTERVAL_ACTIVE",
    )
    sessions = four_sessions()
    sessions[-1] = session(9, distance_m=12000, splits=repeated)
    result = build(four_runs(), sessions)

    assert result.session_patterns.structured_intervals.count_84_days == 1
    assert result.session_patterns.relative_long_run.count_84_days == 1
    assert result.pattern_coverage.sessions_with_observed_pattern == 1
    assert result.pattern_coverage.sessions_without_observed_pattern == 3
    assert result.pattern_coverage.coverage_ratio == 0.25


def test_no_observed_pattern_counts_every_eligible_session_once() -> None:
    activities = [
        activity(f"run-{index}", REFERENCE - timedelta(days=index), distance_m=5000)
        for index in range(3)
    ]
    sessions = [session(index, distance_m=5000) for index in range(3)]
    result = build(activities, sessions)

    assert result.pattern_coverage.eligible_sessions == 3
    assert result.pattern_coverage.sessions_with_observed_pattern == 0
    assert result.pattern_coverage.sessions_without_observed_pattern == 3
    assert result.pattern_coverage.coverage_ratio == 0.0


def test_short_session_is_not_relative_long_and_small_sample_is_insufficient() -> None:
    result = build(
        [activity("short", REFERENCE, distance_m=3000)],
        [session(0, distance_m=3000)],
    )

    assert result.session_patterns.relative_long_run.count_84_days == 0
    assert result.session_patterns.relative_long_run.status == "insufficient_data"
    assert "personal_distance_distribution_insufficient" in result.limitations


@pytest.mark.parametrize(
    "extra",
    [
        {"isPR": True},
        {"fastestSplit_5000": 1200.0},
        {"distance_m": 10000},
        {"activityName": "Official-looking 10K race"},
    ],
)
def test_weak_or_textual_signals_never_imply_race(extra: dict[str, Any]) -> None:
    activity_extra = dict(extra)
    distance = activity_extra.pop("distance_m", 6000)
    item = activity("weak", REFERENCE, distance_m=distance, **activity_extra)
    result = build([item], [session(0, distance_m=distance)])

    assert result.session_patterns.race.count_84_days == 0
    assert result.past_race_evidence == []


@pytest.mark.parametrize("event_type", ["race", "competition"])
def test_explicit_garmin_race_creates_past_race_evidence(
    event_type: str,
) -> None:
    activities = four_runs(eventType={"typeKey": event_type})
    sessions = four_sessions(event=event_type)
    result = build(activities, sessions)

    race = result.session_patterns.race
    assert race.count_84_days == 1
    assert race.provenance == ["garmin_explicit_race"]
    assert result.past_race_evidence[0].identification == "explicit"
    assert result.past_race_evidence[0].source == "garmin_event_type"


def test_no_explicit_race_has_no_invented_past_race() -> None:
    result = build(four_runs(isPR=True), four_sessions())
    assert result.past_race_evidence == []
    assert "past_race_evidence_unavailable" in result.limitations


def test_uncategorized_event_type_is_inspected_but_not_informative() -> None:
    result = build(four_runs(), four_sessions(event="uncategorized"))

    coverage = result.pattern_coverage.event_type_coverage
    assert coverage.inspected_sessions == 4
    assert coverage.informative_sessions == 0
    assert coverage.explicit_race_sessions == 0
    assert result.session_patterns.race.status == "insufficient_data"
    assert result.session_patterns.race.confidence == "none"
    assert result.confidence.race_evidence == "none"
    assert "informative_event_type_unavailable" in result.limitations


def test_intensity_evidence_preserves_coverage_without_labels() -> None:
    activities = four_runs(
        averageHR=145,
        hrTimeInZone_2=1200,
        aerobicTrainingEffect=3.2,
        anaerobicTrainingEffect=0.5,
    )
    result = build(activities, four_sessions())

    intensity = result.intensity_evidence
    assert intensity.heart_rate_zone_coverage.available_count == 1
    assert intensity.heart_rate_zone_seconds == {"zone_2": 1200.0}
    assert intensity.aerobic_training_effect_coverage.available_count == 1
    serialized = json.dumps(result.model_dump(mode="json"))
    assert all(label not in serialized for label in ('"easy"', '"tempo"', '"threshold"'))


def test_collector_filters_other_sports_and_uses_only_structured_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activities = [
        activity("run", REFERENCE, eventType={"typeKey": "race"}),
        activity("bike", REFERENCE, sport="cycling"),
    ]
    monkeypatch.setattr(pattern_module, "load_local_activity_history", lambda: activities)
    monkeypatch.setattr(
        pattern_module,
        "load_local_activity_details",
        lambda activity_id: {
            "endpoints": {
                "get_activity_typed_splits": {
                    "status": "success",
                    "data": {
                        "splits": [
                            {"type": "INTERVAL_ACTIVE"},
                            {"type": "INTERVAL_RECOVERY"},
                            {"type": "INTERVAL_ACTIVE"},
                        ]
                    },
                }
            }
        },
    )

    diagnostics: dict[str, Any] = {}
    collected = collect_local_training_pattern_inputs(diagnostics=diagnostics)
    assert len(collected) == 1
    assert collected[0].event_type == "race"
    assert collected[0].typed_splits_inspected is True
    assert diagnostics["activity_records_read"] == 2
    assert diagnostics["detail_files_opened"] == 1


def test_determinism_privacy_and_ai_projection() -> None:
    activities = four_runs()
    sessions = four_sessions()
    first = build(activities, sessions, user_id="deterministic_one")
    second = build(activities, sessions, user_id="deterministic_two")
    assert first == second

    payload = json.dumps(training_pattern_for_ai(first)).lower()
    for forbidden in ("activityid", "activityname", "private", "email", "token", "gps"):
        assert forbidden not in payload


def test_pure_builder_makes_no_io_network_garmin_or_ai_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary, confidence = contracts("pure", four_runs())
    original_open = builtins.open

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unexpected external access")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    try:
        result = build_training_pattern_evidence_v1(
            summary, confidence, four_sessions(), reference_date=REFERENCE
        )
    finally:
        monkeypatch.setattr(builtins, "open", original_open)
    assert result.schema_version == 1


def test_large_input_is_compact_and_fast() -> None:
    activities = four_runs()
    summary, confidence = contracts("performance", activities)
    sessions = [
        session(index % 84, distance_m=4000 + (index % 12) * 500)
        for index in range(5000)
    ]
    started = time.perf_counter()
    result = build_training_pattern_evidence_v1(
        summary, confidence, sessions, reference_date=REFERENCE
    )
    elapsed = time.perf_counter() - started
    encoded = json.dumps(result.model_dump(mode="json"))

    assert elapsed < 1.0
    assert len(encoded.encode("utf-8")) < 12_000
    assert "sessions" not in result.model_dump()
