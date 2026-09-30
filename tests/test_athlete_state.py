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


REFERENCE = date(2026, 9, 30)


@pytest.fixture
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    return tmp_path


def activity(
    activity_id: str,
    day: date,
    sport: str = "running",
    *,
    distance_m: float | None = 5000,
    duration_s: float | None = 1800,
    **extra: Any,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "activityId": activity_id,
        "activityType": {"typeKey": sport},
        "activityName": f"Secret place and person {activity_id}",
        "startTimeLocal": f"{day.isoformat()}T08:00:00",
        **extra,
    }
    if distance_m is not None:
        item["distance"] = distance_m
    if duration_s is not None:
        item["movingDuration"] = duration_s
    return item


def prepare(user_id: str, activities: list[dict[str, Any]]) -> None:
    with config.user_context(user_id):
        history_import.initialize_history_import("all", reference_date=REFERENCE)
        for item in activities:
            history_storage.save_activity_metadata(item)


def summarize(user_id: str) -> summary_module.TrainingHistorySummary:
    with config.user_context(user_id):
        return summary_module.build_training_history_summary(reference_date=REFERENCE)


def build(
    user_id: str,
    activities: list[dict[str, Any]],
    *,
    diagnostics: dict[str, Any] | None = None,
) -> tuple[
    athlete_state.AthleteState,
    summary_module.TrainingHistorySummary,
    data_confidence.DataConfidence,
]:
    prepare(user_id, activities)
    summary = summarize(user_id)
    confidence = data_confidence.build_data_confidence(summary, reference_date=REFERENCE)
    state = athlete_state.build_athlete_state(
        summary, confidence, reference_date=REFERENCE, diagnostics=diagnostics
    )
    return state, summary, confidence


def rich_running(*, metrics: bool = False) -> list[dict[str, Any]]:
    result = []
    for index in range(18):
        extra: dict[str, Any] = {}
        if metrics:
            extra.update(
                averageHR=145,
                maxHR=172,
                hrTimeInZone_1=100,
                hrTimeInZone_2=1200,
                aerobicTrainingEffect=2.5,
                anaerobicTrainingEffect=0.0,
                activityTrainingLoad=70,
            )
        result.append(
            activity(
                f"run-{index}",
                REFERENCE - timedelta(days=index * 4),
                distance_m=5000 + index * 250,
                duration_s=1800 + index * 60,
                **extra,
            )
        )
    return result


def test_empty_history_is_safe_and_insufficient(isolated_data: Path) -> None:
    state, _, _ = build("u_empty_state", [])

    assert state.status == "insufficient_data"
    assert state.sport_profile.sports_observed == []
    assert state.running_state.status == "not_applicable"
    assert state.current_capability.status == "insufficient_data"
    assert state.recovery_state.status == "unavailable"


def test_one_activity_remains_partial_without_invented_experience(isolated_data: Path) -> None:
    state, _, _ = build("u_one_state", [activity("one", REFERENCE)])

    assert state.status == "partial"
    assert state.interpretation_boundaries.experience_state == "unknown"
    assert state.interpretation_boundaries.load_tolerance == "unknown"
    assert state.interpretation_boundaries.primary_sport == "not_inferred"


@pytest.mark.parametrize(
    ("sports", "expected_cross"),
    [
        (["running"], set()),
        (["running", "cycling"], {"cycling"}),
        (["running", "strength_training"], {"strength"}),
        (["running", "other"], {"padel"}),
    ],
)
def test_running_and_multisport_combinations(
    isolated_data: Path, sports: list[str], expected_cross: set[str]
) -> None:
    items = []
    for index, sport in enumerate(sports):
        extra = {"activityName": "Padel"} if sport == "other" else {}
        items.append(activity(f"sport-{index}", REFERENCE - timedelta(days=index), sport, **extra))
    state, _, _ = build(f"u_combo_{len(sports)}_{sports[-1]}", items)

    assert state.sport_profile.running_present is True
    assert {item.sport for item in state.cross_training} == expected_cross
    assert all(item.observed_distance_last_84_days_km is None for item in state.cross_training if not item.distance_relevant)


def test_many_other_activities_are_reported_as_a_limitation(isolated_data: Path) -> None:
    items = [activity(f"other-{index}", REFERENCE - timedelta(days=index), "other") for index in range(12)]
    state, _, _ = build("u_other_state", items)
    limitation = next(item for item in state.limitations if item.code == "sport_classification_partial")

    assert limitation.affected_activities == 12
    assert state.confidence.sport_classification == "none"


def test_strong_history_with_weak_recent_activity_is_partial(isolated_data: Path) -> None:
    items = [activity(f"old-{index}", REFERENCE - timedelta(days=40 + index * 5)) for index in range(18)]
    state, _, confidence = build("u_old_state", items)

    assert confidence.training_history_confidence.confidence == "high"
    assert confidence.current_state_confidence.confidence == "low"
    assert state.status == "partial"


def test_strong_recent_activity_with_short_history_keeps_history_limit(isolated_data: Path) -> None:
    items = [activity(f"new-{index}", REFERENCE - timedelta(days=index * 2)) for index in range(8)]
    state, _, confidence = build("u_short_recent", items)

    assert confidence.components.history_coverage.confidence == "low"
    assert state.confidence.training_history == "low"
    assert state.current_training.observed_sessions_per_week_last_28_days == 2.0


def test_metric_gating_for_absent_and_complete_heart_rate(isolated_data: Path) -> None:
    absent, _, _ = build("u_no_hr", rich_running())
    complete, _, _ = build("u_hr", rich_running(metrics=True))

    assert absent.running_state.heart_rate.status == "unavailable"
    assert absent.running_state.heart_rate_zones.status == "unavailable"
    assert absent.running_state.aerobic_training_effect.status == "unavailable"
    assert absent.running_state.training_load_status == "unavailable"
    assert complete.running_state.heart_rate.status == "available"
    assert complete.running_state.heart_rate_zones.status == "available"
    assert complete.running_state.aerobic_training_effect.status == "available"
    assert complete.running_state.anaerobic_training_effect.covered_count == 18
    assert complete.running_state.training_load_status == "available"


def test_performance_absent_is_insufficient(isolated_data: Path) -> None:
    state, _, _ = build("u_no_performance", rich_running())
    assert state.current_capability.status == "insufficient_data"
    assert state.current_capability.reference_performances == []


def test_recent_performance_is_a_reference_not_a_prediction(isolated_data: Path) -> None:
    items = rich_running()
    items[0]["fastestSplit_5000"] = 1200.0
    state, _, _ = build("u_recent_perf", items)

    reference = state.current_capability.reference_performances[0]
    assert reference.distance_m == 5000
    assert reference.recency == "recent"
    assert state.current_capability.prediction_status == "not_computed"


def test_historical_performance_is_retained_and_labelled(isolated_data: Path) -> None:
    items = [
        activity("historic", REFERENCE - timedelta(days=300), fastestSplit_5000=1250.0),
        *rich_running(),
    ]
    state, _, _ = build("u_historic_perf", items)

    reference = next(item for item in state.current_capability.reference_performances if item.distance_m == 5000)
    assert reference.age_days == 300
    assert reference.recency == "historical"
    assert state.current_capability.status == "partial"


def test_best_supported_performance_is_selected_per_distance(isolated_data: Path) -> None:
    items = rich_running()
    items[0].update(fastestSplit_1000=240.0, fastestSplit_5000=1300.0)
    items[1].update(fastestSplit_1000=230.0, fastestSplit_5000=1200.0, fastestSplit_10000=2700.0)
    state, _, _ = build("u_multi_perf", items)

    references = {item.distance_m: item.duration_seconds for item in state.current_capability.reference_performances}
    assert references == {1000.0: 230.0, 5000.0: 1200.0, 10000.0: 2700.0}


def test_interruption_is_described_as_recording_gap_only(isolated_data: Path) -> None:
    items = [activity("before", REFERENCE - timedelta(days=20)), activity("after", REFERENCE)]
    state, _, _ = build("u_gap_state", items)

    assert state.continuity.recorded_interruptions[0]["days_without_recorded_activity"] == 19
    assert "Garmin" in state.continuity.definition


def test_multisport_shift_preserves_opposing_trends(isolated_data: Path) -> None:
    previous = [
        activity(f"previous-run-{index}", REFERENCE - timedelta(days=30 + index * 3), distance_m=10000, duration_s=3600)
        for index in range(4)
    ]
    current = [
        activity(f"bike-{index}", REFERENCE - timedelta(days=index * 3), "cycling", distance_m=20000, duration_s=3000)
        for index in range(6)
    ] + [activity("current-run", REFERENCE - timedelta(days=1), distance_m=5000, duration_s=1800)]
    state, _, _ = build("u_shift_state", previous + current)

    assert state.recent_changes.activity_count.direction == "increased"
    assert state.recent_changes.running_distance_km.direction == "decreased"
    assert state.recent_changes.sport_duration_seconds["cycling"].direction == "newly_observed"


def test_recovery_and_load_tolerance_never_inferred(isolated_data: Path) -> None:
    state, _, _ = build("u_recovery_state", rich_running(metrics=True))

    assert state.recovery_state.status == "unavailable"
    assert state.interpretation_boundaries.load_tolerance == "unknown"
    assert "sleep" in state.recovery_state.missing_sources


def test_output_is_deterministic_and_privacy_safe(isolated_data: Path) -> None:
    items = rich_running(metrics=True)
    items[0].update(latitude=50.123, longitude=4.456, ownerEmail="private@example.com", token="secret")
    state, summary, confidence = build("u_private_state", items)
    again = athlete_state.build_athlete_state(summary, confidence, reference_date=REFERENCE)
    payload = json.dumps(state.model_dump(mode="json"), sort_keys=True).lower()

    assert state == again
    for forbidden in ("secret place", "private@example.com", "latitude", "longitude", "token", "activityid"):
        assert forbidden not in payload


def test_build_uses_only_compact_inputs_without_files_network_or_openai(
    isolated_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, summary, confidence = build("u_pure_state", rich_running(metrics=True))

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("external access is forbidden")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    state = athlete_state.build_athlete_state(summary, confidence, reference_date=REFERENCE)

    assert state.status == "available"


def test_compact_summary_representing_5000_activities_stays_fast(
    isolated_data: Path,
) -> None:
    prepare("u_5000_state", rich_running(metrics=True))
    original = summarize("u_5000_state")
    summary = original.model_copy(
        update={
            "coverage": original.coverage.model_copy(update={"activity_count": 5000}),
            "source_measurements": original.source_measurements.model_copy(
                update={"metadata_files_read": 5000, "in_scope_activities": 5000}
            ),
        }
    )
    confidence = data_confidence.build_data_confidence(summary, reference_date=REFERENCE)
    diagnostics: dict[str, Any] = {}
    started = time.perf_counter()
    state = athlete_state.build_athlete_state(
        summary, confidence, reference_date=REFERENCE, diagnostics=diagnostics
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 0.05
    assert diagnostics["computation_seconds"] < 0.05
    assert len(json.dumps(state.model_dump(mode="json"))) < 20000


def test_contract_has_exact_v1_top_level_sections(isolated_data: Path) -> None:
    state, _, _ = build("u_contract_state", rich_running(metrics=True))
    assert set(state.model_dump(mode="json")) == {
        "schema_version",
        "rule_set_version",
        "as_of",
        "status",
        "sport_profile",
        "current_training",
        "running_state",
        "cross_training",
        "current_capability",
        "recent_changes",
        "continuity",
        "recovery_state",
        "interpretation_boundaries",
        "limitations",
        "confidence",
    }
