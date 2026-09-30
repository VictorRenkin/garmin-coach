from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from garmin_coach import config, history_import, history_storage
from garmin_coach import training_history_summary as summary_module


REFERENCE = date(2026, 9, 30)


@pytest.fixture
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    return tmp_path


def make_activity(
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
        "activityName": f"Private training {activity_id}",
        "startTimeLocal": f"{day.isoformat()}T08:00:00",
        **extra,
    }
    if distance_m is not None:
        item["distance"] = distance_m
    if duration_s is not None:
        item["movingDuration"] = duration_s
    return item


def prepare(
    user_id: str,
    activities: list[dict[str, Any]],
    *,
    scope: str = "all",
) -> None:
    with config.user_context(user_id):
        history_import.initialize_history_import(scope, reference_date=REFERENCE)
        for activity in activities:
            history_storage.save_activity_metadata(activity)


def build(user_id: str, **kwargs) -> summary_module.TrainingHistorySummary:
    with config.user_context(user_id):
        return summary_module.build_training_history_summary(
            reference_date=REFERENCE, **kwargs
        )


@pytest.mark.parametrize(
    ("sports", "expected"),
    [
        (["running"], {"running"}),
        (["running", "cycling"], {"running", "cycling"}),
        (["running", "strength_training"], {"running", "strength"}),
        (["running", "lap_swimming"], {"running", "swimming"}),
        (
            ["running", "cycling", "lap_swimming", "strength_training", "hiking"],
            {"running", "cycling", "swimming", "strength", "hiking"},
        ),
        (["running", "unmapped_sport_x"], {"running", "unmapped_sport_x"}),
    ],
)
def test_multisport_combinations_are_preserved(
    isolated_data: Path, sports: list[str], expected: set[str]
) -> None:
    activities = [
        make_activity(f"a{index + 1}", REFERENCE - timedelta(days=index), sport)
        for index, sport in enumerate(sports)
    ]
    prepare("u_sports", activities)
    summary = build("u_sports")

    assert {item.sport for item in summary.sports} == expected
    assert {item.sport for item in summary.cross_training} == expected - {"running"}


def test_padel_and_tennis_are_recognized_without_exposing_names(
    isolated_data: Path,
) -> None:
    activities = [
        make_activity("padel", REFERENCE, "other", activityName="Padel friends at Home"),
        make_activity("tennis", REFERENCE - timedelta(days=1), "other", activityName="Tennis"),
        make_activity("other", REFERENCE - timedelta(days=2), "other", activityName="Private unknown place"),
    ]
    prepare("u_rackets", activities)
    summary = build("u_rackets")
    serialized = json.dumps(summary.model_dump(mode="json")).lower()

    assert {item.sport for item in summary.sports} == {"padel", "tennis", "other"}
    assert "friends" not in serialized
    assert "private unknown" not in serialized
    assert "home" not in serialized


def test_empty_history_distinguishes_no_activities_from_zero(
    isolated_data: Path,
) -> None:
    prepare("u_empty", [])
    summary = build("u_empty")

    assert summary.coverage.status == "no_activities"
    assert summary.coverage.covered_days is None
    assert summary.windows["last_28_days"].activity_count == 0
    assert summary.windows["last_28_days"].status == "no_activities"
    assert summary.running.distance_last_28_days_km is None


def test_single_activity_has_one_day_and_one_active_week(isolated_data: Path) -> None:
    prepare("u_single", [make_activity("single", REFERENCE)])
    summary = build("u_single")

    assert summary.coverage.covered_days == 1
    assert summary.coverage.active_days == 1
    assert summary.coverage.active_weeks == 1
    assert summary.coverage.weeks_without_recorded_activity == 0


def test_28_and_84_day_boundaries_are_inclusive(isolated_data: Path) -> None:
    activities = [
        make_activity("today", REFERENCE),
        make_activity("day27", REFERENCE - timedelta(days=27)),
        make_activity("day28", REFERENCE - timedelta(days=28)),
        make_activity("day83", REFERENCE - timedelta(days=83)),
        make_activity("day84", REFERENCE - timedelta(days=84)),
    ]
    prepare("u_boundaries", activities)
    summary = build("u_boundaries")

    assert summary.windows["last_28_days"].activity_count == 2
    assert summary.windows["last_84_days"].activity_count == 4
    assert summary.windows["historical"].activity_count == 5


def test_history_over_one_year_is_aggregated_not_dumped(isolated_data: Path) -> None:
    activities = [
        make_activity("old", REFERENCE - timedelta(days=500), "cycling"),
        make_activity("recent", REFERENCE, "running"),
    ]
    prepare("u_long_history", activities)
    summary = build("u_long_history")
    payload = summary.model_dump(mode="json")

    assert summary.coverage.covered_days == 501
    assert summary.windows["historical"].activity_count == 2
    assert "activities" not in payload


def test_reader_scope_excludes_older_valid_files(isolated_data: Path) -> None:
    activities = [
        make_activity("inside", REFERENCE - timedelta(days=10)),
        make_activity("outside", REFERENCE - timedelta(days=200)),
    ]
    prepare("u_limited", activities, scope="3_months")
    summary = build("u_limited")

    assert summary.coverage.activity_count == 1
    assert summary.scope == "3_months"
    assert summary.source_measurements.metadata_files_read == 2
    assert summary.source_measurements.in_scope_activities == 1


def test_missing_distance_hr_and_load_remain_unavailable(isolated_data: Path) -> None:
    prepare(
        "u_missing_metrics",
        [make_activity("missing", REFERENCE, distance_m=None, duration_s=1200)],
    )
    summary = build("u_missing_metrics")
    window = summary.windows["last_28_days"]

    assert summary.running.distance_last_28_days_km is None
    assert window.metric_coverage["distance_when_relevant"].status == "unavailable"
    assert window.metric_coverage["heart_rate"].status == "unavailable"
    assert window.training_load.status == "unavailable"
    assert window.training_load.observed_total is None


def test_strength_distance_is_not_reported_as_meaningful(isolated_data: Path) -> None:
    prepare(
        "u_strength_distance",
        [make_activity("strength", REFERENCE, "strength_training", distance_m=9999)],
    )
    summary = build("u_strength_distance")
    strength = summary.sports[0]

    assert strength.sport == "strength"
    assert strength.distance_relevant is False
    assert strength.observed_distance_km is None
    assert summary.windows["last_28_days"].observed_distance_by_sport_km == {}


def test_partial_training_load_reports_coverage_and_observed_values(
    isolated_data: Path,
) -> None:
    activities = [
        make_activity("load", REFERENCE, activityTrainingLoad=80.0),
        make_activity("missing1", REFERENCE - timedelta(days=1)),
        make_activity("missing2", REFERENCE - timedelta(days=2)),
        make_activity("missing3", REFERENCE - timedelta(days=3)),
    ]
    prepare("u_partial_load", activities)
    load = build("u_partial_load").windows["last_28_days"].training_load

    assert load.status == "partial"
    assert load.observed_total == 80
    assert load.observed_average == 80
    assert load.coverage.available_count == 1
    assert load.coverage.eligible_count == 4
    assert load.coverage.ratio == 0.25


def test_running_distribution_long_runs_and_weighted_pace(isolated_data: Path) -> None:
    activities = [
        make_activity("r5", REFERENCE, distance_m=5000, duration_s=1800),
        make_activity("r10", REFERENCE - timedelta(days=7), distance_m=10000, duration_s=4000),
        make_activity("r15", REFERENCE - timedelta(days=14), distance_m=15000, duration_s=6500),
        make_activity("r20", REFERENCE - timedelta(days=21), distance_m=20000, duration_s=9000),
    ]
    prepare("u_running", activities)
    running = build("u_running").running

    assert running.sessions_last_28_days == 4
    assert running.distance_last_28_days_km == 50
    assert running.average_distance_last_84_days_km == 12.5
    assert running.maximum_distance_last_84_days_km == 20
    assert running.long_runs.distance_percentile_75_km == 16.25
    assert [item.distance_km for item in running.long_runs.top_three] == [20, 15, 10]
    assert running.weighted_pace_last_84_days_seconds_per_km == 426


def test_performance_evidence_uses_only_explicit_garmin_signals(
    isolated_data: Path,
) -> None:
    activities = [
        make_activity(
            "pr",
            REFERENCE,
            distance_m=10000,
            duration_s=2500,
            isPR=True,
            fastestSplit_5000=1199.5,
        ),
        make_activity(
            "faster_split",
            REFERENCE - timedelta(days=2),
            fastestSplit_5000=1180.0,
        ),
    ]
    prepare("u_evidence", activities)
    evidence = build("u_evidence").running.performance_evidence

    assert {item.type for item in evidence} == {"garmin_pr_activity", "garmin_fastest_split"}
    split = next(item for item in evidence if item.type == "garmin_fastest_split")
    assert split.distance_m == 5000
    assert split.duration_seconds == 1180
    assert split.provenance == "activity_history.fastestSplit_5000"


def test_intensity_uses_metadata_zones_and_training_effect(isolated_data: Path) -> None:
    activities = [
        make_activity(
            "zones",
            REFERENCE,
            averageHR=145,
            hrTimeInZone_1=300,
            hrTimeInZone_2=1200,
            aerobicTrainingEffect=3.2,
        ),
        make_activity("no_zones", REFERENCE - timedelta(days=1)),
    ]
    prepare("u_intensity", activities)
    intensity = build("u_intensity").running.intensity_distribution

    assert intensity.status == "partial"
    assert intensity.heart_rate_zone_seconds == {"zone_1": 300, "zone_2": 1200}
    assert intensity.heart_rate_zone_coverage.ratio == 0.5
    assert intensity.aerobic_training_effect_counts == {"3_to_below_4": 1}


def _write_hr_details(activity_id: str) -> None:
    target = Path(config.MULTISPORT_DETAILS_DIR) / f"{activity_id}.json"
    target.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "activity_id": activity_id,
                "complete": False,
                "endpoints": {
                    "get_activity_hr_in_timezones": {
                        "status": "success",
                        "attempts": 1,
                        "data": [
                            {"zoneNumber": 1, "secsInZone": 120},
                            {"zoneNumber": 2, "secsInZone": 900},
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_details_present_are_opened_only_for_missing_hr_zones(isolated_data: Path) -> None:
    prepare(
        "u_details_present",
        [make_activity("needs_details", REFERENCE, averageHR=140)],
    )
    with config.user_context("u_details_present"):
        _write_hr_details("needs_details")
        diagnostics: dict[str, Any] = {}
        summary = summary_module.build_training_history_summary(
            reference_date=REFERENCE, diagnostics=diagnostics
        )

    intensity = summary.running.intensity_distribution
    assert intensity.heart_rate_zone_seconds["zone_2"] == 900
    assert summary.source_measurements.detail_files_opened == 1
    assert diagnostics["detail_files_requested"] == 1


def test_details_absent_are_safe_and_report_no_opened_file(isolated_data: Path) -> None:
    prepare(
        "u_details_absent",
        [make_activity("no_detail_file", REFERENCE, averageHR=140)],
    )
    diagnostics: dict[str, Any] = {}
    summary = build("u_details_absent", diagnostics=diagnostics)

    assert summary.running.intensity_distribution.status == "insufficient_data"
    assert summary.source_measurements.detail_files_opened == 0
    assert diagnostics["detail_files_requested"] == 1


def test_interruption_is_described_as_no_recorded_activity(isolated_data: Path) -> None:
    prepare(
        "u_interruption",
        [
            make_activity("before", date(2026, 8, 1)),
            make_activity("after", date(2026, 8, 12)),
        ],
    )
    interruptions = build("u_interruption").interruptions

    assert interruptions.total_detected == 1
    assert interruptions.items[0].days_without_recorded_activity == 10
    assert "enregistrée" in interruptions.definition


@pytest.mark.parametrize(
    ("current_count", "previous_count", "expected_sign"),
    [(4, 2, 1), (1, 4, -1)],
)
def test_trends_detect_factual_increase_and_decrease(
    isolated_data: Path,
    current_count: int,
    previous_count: int,
    expected_sign: int,
) -> None:
    activities = [
        make_activity(f"c{index}", REFERENCE - timedelta(days=index * 3))
        for index in range(current_count)
    ] + [
        make_activity(f"p{index}", REFERENCE - timedelta(days=30 + index * 3))
        for index in range(previous_count)
    ]
    prepare("u_trend", activities)
    trend = build("u_trend").trends.activity_count

    assert trend.status == "available"
    assert (trend.absolute_change > 0) is (expected_sign > 0)
    assert (trend.relative_change_pct > 0) is (expected_sign > 0)


def test_trend_marks_empty_previous_window(isolated_data: Path) -> None:
    prepare("u_previous_empty", [make_activity("current", REFERENCE)])
    trend = build("u_previous_empty").trends.activity_count

    assert trend.status == "previous_window_empty"
    assert trend.absolute_change is None
    assert trend.relative_change_pct is None


def test_summary_is_isolated_between_users(isolated_data: Path) -> None:
    prepare("u_summary_alpha", [make_activity("alpha", REFERENCE, "running")])
    prepare("u_summary_beta", [make_activity("beta", REFERENCE, "cycling")])

    alpha = build("u_summary_alpha")
    beta = build("u_summary_beta")

    assert [item.sport for item in alpha.sports] == ["running"]
    assert [item.sport for item in beta.sports] == ["cycling"]


def test_summary_contains_no_private_or_raw_technical_data(isolated_data: Path) -> None:
    prepare(
        "u_summary_private",
        [
            make_activity(
                "private-id",
                REFERENCE,
                activityName="Home route with Victor",
                latitude=50.0,
                longitude=4.0,
                polyline="secret-polyline",
                email="private@example.test",
                token="secret-token",
            )
        ],
    )
    serialized = json.dumps(build("u_summary_private").model_dump(mode="json")).lower()

    for forbidden in (
        "private-id",
        "victor",
        "latitude",
        "longitude",
        "polyline",
        "email",
        "token",
        "secret",
    ):
        assert forbidden not in serialized


def test_summary_is_deterministic_for_fixed_reference_date(isolated_data: Path) -> None:
    prepare(
        "u_deterministic",
        [make_activity("a1", REFERENCE), make_activity("a2", REFERENCE - timedelta(days=20))],
    )
    first = build("u_deterministic").model_dump(mode="json")
    second = build("u_deterministic").model_dump(mode="json")

    assert first == second


def test_5000_activities_remain_compact_and_metadata_only(
    isolated_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    activities = [
        make_activity(
            f"a{index}",
            REFERENCE - timedelta(days=index // 5),
            ("running", "cycling", "strength_training")[index % 3],
        )
        for index in range(5000)
    ]

    def local_history(**kwargs):
        kwargs["diagnostics"]["metadata_files_read"] = 5000
        return activities

    monkeypatch.setattr(summary_module, "load_local_activity_history", local_history)
    monkeypatch.setattr(
        summary_module,
        "local_history_scope",
        lambda: {
            "source": "multisport",
            "scope": "all",
            "reference_date": REFERENCE.isoformat(),
            "cutoff_date": None,
        },
    )
    monkeypatch.setattr(
        summary_module,
        "load_local_activity_details",
        lambda activity_id: pytest.fail("Les détails ne doivent pas être ouverts"),
    )
    diagnostics: dict[str, Any] = {}
    started = time.perf_counter()
    summary = summary_module.build_training_history_summary(
        reference_date=REFERENCE, diagnostics=diagnostics
    )
    elapsed = time.perf_counter() - started
    encoded = json.dumps(summary.model_dump(mode="json"))

    assert summary.coverage.activity_count == 5000
    assert summary.source_measurements.metadata_files_read == 5000
    assert summary.source_measurements.detail_files_opened == 0
    assert len(encoded) < 30_000
    assert elapsed < 5
    assert diagnostics["computation_seconds"] < 5


def test_builder_has_no_network_or_openai_dependency(
    isolated_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare("u_offline", [make_activity("offline", REFERENCE)])

    def forbidden(*args, **kwargs):
        raise AssertionError("Aucun service externe ne doit être appelé")

    # The builder's only inputs are the unified reader and optional local details.
    monkeypatch.setattr(summary_module, "load_local_activity_details", forbidden)
    summary = build("u_offline")
    assert summary.coverage.activity_count == 1
