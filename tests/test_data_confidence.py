from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from garmin_coach import config, data_confidence, history_import, history_storage
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
    **extra: Any,
) -> dict[str, Any]:
    return {
        "activityId": activity_id,
        "activityType": {"typeKey": sport},
        "activityName": f"Private activity {activity_id}",
        "startTimeLocal": f"{day.isoformat()}T08:00:00",
        "distance": 5000,
        "movingDuration": 1800,
        **extra,
    }


def prepare(
    user_id: str,
    activities: list[dict[str, Any]],
    *,
    scope: str = "all",
) -> None:
    with config.user_context(user_id):
        history_import.initialize_history_import(scope, reference_date=REFERENCE)
        for item in activities:
            history_storage.save_activity_metadata(item)


def assess(
    user_id: str,
    *,
    objective: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> data_confidence.DataConfidence:
    with config.user_context(user_id):
        summary = summary_module.build_training_history_summary(reference_date=REFERENCE)
    return data_confidence.build_data_confidence(
        summary,
        objective=objective,
        reference_date=REFERENCE,
        diagnostics=diagnostics,
    )


def rich_history(*, metrics: bool = False) -> list[dict[str, Any]]:
    items = []
    for index in range(16):
        extra: dict[str, Any] = {}
        if metrics:
            extra.update(
                averageHR=145,
                maxHR=172,
                hrTimeInZone_1=200,
                hrTimeInZone_2=1200,
                aerobicTrainingEffect=3.1,
                anaerobicTrainingEffect=0.5,
                activityTrainingLoad=75,
            )
        items.append(
            activity(
                f"a{index}",
                REFERENCE - timedelta(days=index * 5),
                **extra,
            )
        )
    return items


def test_empty_history_has_independent_none_components(isolated_data: Path) -> None:
    prepare("u_empty_confidence", [])
    result = assess("u_empty_confidence")

    assert result.training_history_confidence.confidence == "none"
    assert result.current_state_confidence.confidence == "none"
    assert result.components.history_coverage.confidence == "none"
    assert result.components.recovery.confidence == "none"
    assert {item.code for item in result.missing_data} >= {
        "training_history_unavailable",
        "recovery_sources_unavailable",
    }


def test_one_activity_and_short_history_are_low(isolated_data: Path) -> None:
    prepare("u_one", [activity("one", REFERENCE)])
    one = assess("u_one")
    prepare(
        "u_short",
        [activity(f"s{i}", REFERENCE - timedelta(days=i * 3)) for i in range(3)],
    )
    short = assess("u_short")

    assert one.components.history_coverage.confidence == "low"
    assert one.components.recent_activity.confidence == "low"
    assert short.components.history_coverage.confidence == "low"


def test_rich_history_is_high_without_requiring_recovery(isolated_data: Path) -> None:
    prepare("u_rich", rich_history(metrics=True))
    result = assess("u_rich")

    assert result.training_history_confidence.confidence == "high"
    assert result.current_state_confidence.confidence == "high"
    assert result.components.history_coverage.confidence == "high"
    assert result.components.recovery.confidence == "none"


def test_voluntary_three_month_scope_can_have_high_recent_confidence(
    isolated_data: Path,
) -> None:
    prepare("u_three_months", rich_history(), scope="3_months")
    result = assess("u_three_months")

    assert result.components.history_coverage.evidence["scope"] == "3_months"
    assert result.components.history_coverage.confidence == "high"
    assert result.components.recent_activity.confidence == "high"


def test_all_scope_is_reported_as_evidence(isolated_data: Path) -> None:
    prepare("u_all_scope", rich_history(), scope="all")
    result = assess("u_all_scope")
    assert result.components.history_coverage.evidence["scope"] == "all"


def test_rich_28_days_but_poor_84_days_is_not_high(isolated_data: Path) -> None:
    items = [activity(f"r{i}", REFERENCE - timedelta(days=i * 4)) for i in range(5)]
    prepare("u_recent_only", items)
    result = assess("u_recent_only")

    assert result.components.recent_activity.confidence == "medium"
    assert result.current_state_confidence.confidence == "medium"


def test_many_historical_activities_with_none_recent_are_stale_not_empty(
    isolated_data: Path,
) -> None:
    items = [
        activity(f"old{i}", REFERENCE - timedelta(days=200 + i * 7))
        for i in range(20)
    ]
    prepare("u_old_history", items)
    result = assess("u_old_history")

    assert result.components.history_coverage.confidence == "high"
    assert result.components.recent_activity.confidence == "low"
    assert result.components.recent_activity.status == "stale"
    assert result.current_state_confidence.confidence == "low"
    assert any(item.code == "last_activity_stale" for item in result.stale_data)


def test_well_classified_multisport_is_high(isolated_data: Path) -> None:
    sports = ["running", "cycling", "lap_swimming", "strength_training"] * 4
    items = [
        activity(f"m{i}", REFERENCE - timedelta(days=i * 5), sport)
        for i, sport in enumerate(sports)
    ]
    prepare("u_multisport_conf", items)
    classification = assess("u_multisport_conf").components.sport_classification

    assert classification.confidence == "high"
    assert classification.evidence["classification_ratio"] == 1


def test_many_other_and_unknown_reduce_classification_confidence(
    isolated_data: Path,
) -> None:
    items = [
        activity(f"known{i}", REFERENCE - timedelta(days=i), "running")
        for i in range(2)
    ] + [
        activity(f"other{i}", REFERENCE - timedelta(days=3 + i), "other")
        for i in range(8)
    ]
    prepare("u_unknown_sports", items)
    result = assess("u_unknown_sports")

    assert result.components.sport_classification.confidence == "low"
    partial = next(item for item in result.missing_data if item.code == "sport_classification_partial")
    assert partial.affected_activities == 8


def test_padel_controlled_rule_has_distinct_evidence(isolated_data: Path) -> None:
    items = [
        activity(
            f"padel{i}",
            REFERENCE - timedelta(days=i),
            "other",
            activityName="Padel",
        )
        for i in range(4)
    ]
    prepare("u_padel_conf", items)
    evidence = assess("u_padel_conf").components.sport_classification.evidence

    assert evidence["controlled_name_rule_count"] == 4
    assert evidence["garmin_explicit_count"] == 0
    assert evidence["classification_ratio"] == 1


@pytest.mark.parametrize(
    ("covered", "expected"),
    [(10, "high"), (6, "medium"), (2, "low"), (0, "none")],
)
def test_heart_rate_confidence_uses_recent_coverage_ratio(
    isolated_data: Path, covered: int, expected: str
) -> None:
    items = []
    for index in range(10):
        extra = {"averageHR": 145, "maxHR": 170} if index < covered else {}
        items.append(activity(f"hr{index}", REFERENCE - timedelta(days=index * 4), **extra))
    prepare("u_hr_ratio", items)
    heart_rate = assess("u_hr_ratio").components.heart_rate

    assert heart_rate.confidence == expected
    assert heart_rate.evidence["covered_count"] == covered


def test_heart_rate_without_zones_does_not_claim_zone_coverage(
    isolated_data: Path,
) -> None:
    items = [
        activity(f"hr{i}", REFERENCE - timedelta(days=i * 5), averageHR=145, maxHR=170)
        for i in range(10)
    ]
    prepare("u_hr_without_zones", items)
    result = assess("u_hr_without_zones")

    assert result.components.heart_rate.confidence == "high"
    assert result.components.heart_rate_zones.confidence == "none"


def test_complete_heart_rate_zones_are_high(isolated_data: Path) -> None:
    items = [
        activity(
            f"zones{i}",
            REFERENCE - timedelta(days=i * 5),
            averageHR=145,
            hrTimeInZone_1=300,
            hrTimeInZone_2=1200,
        )
        for i in range(10)
    ]
    prepare("u_complete_zones", items)
    zones = assess("u_complete_zones").components.heart_rate_zones

    assert zones.confidence == "high"
    assert zones.evidence["coverage_ratio"] == 1


@pytest.mark.parametrize(
    ("covered", "expected"),
    [(10, "high"), (6, "medium"), (2, "low"), (0, "none")],
)
def test_training_effect_confidence_is_coverage_only(
    isolated_data: Path, covered: int, expected: str
) -> None:
    items = []
    for index in range(10):
        extra = {"aerobicTrainingEffect": 3.2} if index < covered else {}
        items.append(activity(f"te{index}", REFERENCE - timedelta(days=index * 4), **extra))
    prepare("u_te_ratio", items)
    effect = assess("u_te_ratio").components.training_effect

    assert effect.confidence == expected
    assert effect.evidence["aerobic_covered_count"] == covered


@pytest.mark.parametrize(
    ("covered", "expected"),
    [(0, "none"), (2, "low"), (6, "medium"), (8, "high")],
)
def test_training_load_reuses_summary_coverage_semantics(
    isolated_data: Path, covered: int, expected: str
) -> None:
    items = []
    for index in range(10):
        extra = {"activityTrainingLoad": 70} if index < covered else {}
        items.append(activity(f"load{index}", REFERENCE - timedelta(days=index * 4), **extra))
    prepare("u_load_ratio", items)
    load = assess("u_load_ratio").components.training_load

    assert load.confidence == expected
    assert load.evidence["coverage_ratio"] == covered / 10


def test_performance_evidence_none_and_multiple(isolated_data: Path) -> None:
    prepare("u_no_perf", rich_history())
    no_perf = assess("u_no_perf").components.performance_evidence
    items = rich_history()
    items[0].update(isPR=True, fastestSplit_1000=250, fastestSplit_5000=1300)
    items[1].update(fastestSplit_10000=2800)
    prepare("u_many_perf", items)
    many = assess("u_many_perf").components.performance_evidence

    assert no_perf.confidence == "none"
    assert many.confidence == "high"
    assert many.evidence["evidence_count"] >= 3


def test_recovery_is_explicitly_unavailable(isolated_data: Path) -> None:
    prepare("u_recovery_none", rich_history())
    recovery = assess("u_recovery_none").components.recovery

    assert recovery.status == "unavailable"
    assert recovery.confidence == "none"
    assert recovery.evidence["missing_sources"] == [
        "sleep",
        "hrv",
        "stress",
        "body_battery",
        "recovery_time",
        "training_readiness",
    ]


def test_goal_context_understands_current_general_and_race_formats(
    isolated_data: Path,
) -> None:
    prepare("u_goal", rich_history())
    general = assess(
        "u_goal",
        objective={"goalType": "performance", "title": "Progresser"},
    )
    race = assess(
        "u_goal",
        objective={
            "goalType": "race",
            "title": "Marathon",
            "distanceKm": 42.195,
            "raceDate": "2027-04-18",
            "goal_target_time": None,
        },
    )

    assert general.components.goal_context.confidence == "high"
    assert race.components.goal_context.confidence == "high"
    assert not any("target_time" in item.code for item in race.missing_data)


def test_race_date_in_past_is_stale(isolated_data: Path) -> None:
    prepare("u_stale_goal", rich_history())
    result = assess(
        "u_stale_goal",
        objective={
            "goalType": "race",
            "title": "Ancienne course",
            "distanceKm": 10,
            "raceDate": "2026-01-01",
        },
    )

    assert result.components.goal_context.status == "stale"
    assert any(item.code == "goal_race_date_in_past" for item in result.stale_data)


def test_inconsistent_coverage_produces_warning_not_missing_data(
    isolated_data: Path,
) -> None:
    prepare("u_warning", rich_history(metrics=True))
    with config.user_context("u_warning"):
        summary = summary_module.build_training_history_summary(reference_date=REFERENCE)
    coverage = summary.windows["last_84_days"].metric_coverage["heart_rate_any"]
    coverage.ratio = 0.1
    result = data_confidence.build_data_confidence(summary, reference_date=REFERENCE)

    assert any(item.code == "coverage_inconsistent" for item in result.warnings)


def test_determinism_and_confidentiality(isolated_data: Path) -> None:
    items = rich_history(metrics=True)
    items[0].update(
        activityName="Home run with Victor",
        latitude=50.0,
        longitude=4.0,
        token="private-token",
    )
    prepare("u_private_confidence", items)
    first = assess("u_private_confidence").model_dump(mode="json")
    second = assess("u_private_confidence").model_dump(mode="json")
    serialized = json.dumps(first).lower()

    assert first == second
    for forbidden in ("victor", "home run", "latitude", "longitude", "token", "private"):
        assert forbidden not in serialized


def test_5000_activity_confidence_is_lightweight_and_does_not_rescan(
    isolated_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    activities = [
        activity(
            f"bulk{index}",
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
    summary_started = time.perf_counter()
    summary = summary_module.build_training_history_summary(reference_date=REFERENCE)
    summary_elapsed = time.perf_counter() - summary_started
    diagnostics: dict[str, Any] = {}
    started = time.perf_counter()
    result = data_confidence.build_data_confidence(
        summary, reference_date=REFERENCE, diagnostics=diagnostics
    )
    elapsed = time.perf_counter() - started

    assert result.training_history_confidence.confidence == "high"
    assert summary.coverage.activity_count == 5000
    assert elapsed < 0.05
    assert elapsed < summary_elapsed
    assert diagnostics["computation_seconds"] < 0.05


def test_isolation_is_inherited_from_each_summary(isolated_data: Path) -> None:
    prepare("u_conf_alpha", rich_history(metrics=True))
    prepare("u_conf_beta", [activity("beta", REFERENCE, "other")])

    alpha = assess("u_conf_alpha")
    beta = assess("u_conf_beta")

    assert alpha.training_history_confidence.confidence == "high"
    assert beta.training_history_confidence.confidence == "low"
    assert beta.components.sport_classification.confidence == "none"


def test_component_has_no_network_or_openai_dependency(isolated_data: Path) -> None:
    prepare("u_pure_confidence", rich_history(metrics=True))
    with config.user_context("u_pure_confidence"):
        summary = summary_module.build_training_history_summary(reference_date=REFERENCE)

    # This call accepts a value object only; there is no client, settings or I/O hook.
    result = data_confidence.build_data_confidence(summary, reference_date=REFERENCE)
    assert result.schema_version == 1
