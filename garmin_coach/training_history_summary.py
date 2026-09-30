"""Deterministic, privacy-safe V1 summary of the local multisport history."""
from __future__ import annotations

import math
import re
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from .local_activity_history import (
    load_local_activity_details,
    load_local_activity_history,
    local_history_scope,
)


SCHEMA_VERSION = 1
RECENT_DAYS = 28
BASE_DAYS = 84
INTERRUPTION_MIN_DAYS = 7
MAX_INTERRUPTION_ITEMS = 20
SUFFICIENT_METRIC_COVERAGE = 0.8


class MetricCoverage(BaseModel):
    available_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    ratio: float | None = Field(default=None, ge=0, le=1)
    status: Literal["available", "partial", "unavailable", "not_applicable"]


class ObservedMetricAggregate(BaseModel):
    status: Literal["available", "partial", "unavailable", "not_applicable"]
    observed_total: float | None = None
    observed_average: float | None = None
    coverage: MetricCoverage


class CoverageSummary(BaseModel):
    first_activity_date: str | None
    last_activity_date: str | None
    activity_count: int = Field(ge=0)
    active_days: int = Field(ge=0)
    covered_days: int | None = Field(default=None, ge=1)
    active_weeks: int = Field(ge=0)
    weeks_without_recorded_activity: int | None = Field(default=None, ge=0)
    status: Literal["available", "no_activities"]


class WindowSummary(BaseModel):
    start_date: str
    end_date: str
    status: Literal["available", "no_activities"]
    activity_count: int = Field(ge=0)
    active_days: int = Field(ge=0)
    observed_duration_seconds: float | None = Field(default=None, ge=0)
    observed_distance_by_sport_km: dict[str, float]
    observed_elevation_gain_m: float | None = Field(default=None, ge=0)
    average_weekly_frequency: float = Field(ge=0)
    sport_activity_counts: dict[str, int]
    training_load: ObservedMetricAggregate
    metric_coverage: dict[str, MetricCoverage]


class SportSummary(BaseModel):
    sport: str
    source_types: list[str]
    activity_count: int = Field(ge=0)
    observed_duration_seconds: float | None = Field(default=None, ge=0)
    average_weekly_frequency: float = Field(ge=0)
    observed_distance_km: float | None = Field(default=None, ge=0)
    distance_relevant: bool
    training_load: ObservedMetricAggregate
    last_activity_date: str
    recent_activity_share_pct: float = Field(ge=0, le=100)
    recent_duration_share_pct: float | None = Field(default=None, ge=0, le=100)


class SportClassificationSummary(BaseModel):
    total_activities: int = Field(ge=0)
    garmin_explicit_count: int = Field(ge=0)
    structured_mapping_count: int = Field(ge=0)
    controlled_name_rule_count: int = Field(ge=0)
    other_count: int = Field(ge=0)
    unknown_count: int = Field(ge=0)
    classified_count: int = Field(ge=0)
    classification_ratio: float | None = Field(default=None, ge=0, le=1)


class LongRunFact(BaseModel):
    date: str
    distance_km: float
    duration_seconds: float | None = None


class LongRunsSummary(BaseModel):
    basis: Literal["personal_distance_distribution_last_84_days"]
    distance_percentile_75_km: float | None
    longest_distance_km: float | None
    top_three: list[LongRunFact] = Field(max_length=3)


class PerformanceEvidence(BaseModel):
    type: Literal["garmin_pr_activity", "garmin_fastest_split"]
    distance_m: float
    duration_seconds: float
    date: str
    provenance: str
    technical_confidence: Literal["high", "medium"]


class IntensityDistribution(BaseModel):
    status: Literal["available", "partial", "insufficient_data", "unavailable"]
    heart_rate_zone_seconds: dict[str, float]
    heart_rate_zone_coverage: MetricCoverage
    aerobic_training_effect_counts: dict[str, int]
    aerobic_training_effect_coverage: MetricCoverage
    anaerobic_training_effect_coverage: MetricCoverage
    details_used: int = Field(ge=0)


class RunningSummary(BaseModel):
    sessions_last_28_days: int = Field(ge=0)
    sessions_last_84_days: int = Field(ge=0)
    distance_last_28_days_km: float | None
    distance_last_84_days_km: float | None
    observed_duration_last_28_days_seconds: float | None
    observed_duration_last_84_days_seconds: float | None
    average_weekly_frequency_last_28_days: float = Field(ge=0)
    average_weekly_frequency_last_84_days: float = Field(ge=0)
    average_distance_last_84_days_km: float | None
    maximum_distance_last_84_days_km: float | None
    maximum_duration_last_84_days_seconds: float | None
    observed_elevation_last_84_days_m: float | None
    weighted_pace_last_84_days_seconds_per_km: float | None
    long_runs: LongRunsSummary
    intensity_distribution: IntensityDistribution
    performance_evidence: list[PerformanceEvidence]


class TrendValue(BaseModel):
    current: float | None
    previous: float | None
    absolute_change: float | None
    relative_change_pct: float | None
    status: Literal["available", "previous_zero", "previous_window_empty"]


class TrendsSummary(BaseModel):
    current_window: dict[str, str]
    previous_window: dict[str, str]
    activity_count: TrendValue
    observed_duration_seconds: TrendValue
    running_distance_km: TrendValue
    observed_duration_by_sport_seconds: dict[str, TrendValue]


class InterruptionFact(BaseModel):
    start_date: str
    end_date: str
    days_without_recorded_activity: int = Field(ge=INTERRUPTION_MIN_DAYS)


class InterruptionsSummary(BaseModel):
    definition: str
    minimum_days: int
    total_detected: int = Field(ge=0)
    items: list[InterruptionFact]


class SourceMeasurements(BaseModel):
    metadata_files_read: int = Field(ge=0)
    in_scope_activities: int = Field(ge=0)
    detail_files_opened: int = Field(ge=0)


class TrainingHistorySummary(BaseModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    as_of: str
    source: Literal["multisport", "legacy"]
    scope: str | None
    scope_cutoff_date: str | None
    coverage: CoverageSummary
    windows: dict[str, WindowSummary]
    sports: list[SportSummary]
    sport_classification: SportClassificationSummary
    running: RunningSummary
    cross_training: list[SportSummary]
    trends: TrendsSummary
    interruptions: InterruptionsSummary
    source_measurements: SourceMeasurements


def _number(activity: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = activity.get(key)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
    return None


def _activity_date(activity: dict[str, Any]) -> date | None:
    raw = activity.get("startTimeLocal") or activity.get("startTimeGMT")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _duration(activity: dict[str, Any]) -> float | None:
    value = _number(activity, "movingDuration", "duration", "elapsedDuration")
    return value if value is not None and value >= 0 else None


def _distance(activity: dict[str, Any]) -> float | None:
    value = _number(activity, "distance")
    return value if value is not None and value >= 0 else None


def _slug(value: object) -> str:
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", str(value or "").lower())
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9_-]+", "_", normalized).strip("_")[:64] or "unknown"


def _raw_sport(activity: dict[str, Any]) -> str:
    activity_type = activity.get("activityType")
    value = (
        activity_type.get("typeKey")
        if isinstance(activity_type, dict)
        else activity_type or activity.get("activityTypeDTO")
    )
    return _slug(value)


def sport_classification(activity: dict[str, Any]) -> tuple[str, str]:
    """Return the conservative sport and its non-sensitive classification source."""
    raw = _raw_sport(activity)
    if "run" in raw:
        return "running", "garmin_explicit" if raw == "running" else "structured_mapping"
    if any(marker in raw for marker in ("cycl", "bike", "biking")):
        return "cycling", "garmin_explicit" if raw == "cycling" else "structured_mapping"
    if "swim" in raw:
        return "swimming", "garmin_explicit" if raw == "swimming" else "structured_mapping"
    if "hiking" in raw or raw == "hike":
        return "hiking", "garmin_explicit" if raw == "hiking" else "structured_mapping"
    if "walk" in raw:
        return "walking", "garmin_explicit" if raw == "walking" else "structured_mapping"
    if any(marker in raw for marker in ("strength", "weight_training")):
        return "strength", "garmin_explicit" if raw == "strength" else "structured_mapping"
    if raw in {"padel", "tennis"}:
        return raw, "garmin_explicit"
    if raw not in {"other", "unknown"}:
        return raw, "garmin_explicit"
    # Garmin sometimes exposes racket sports as `other`. Only explicit sport
    # words are recognized; the free activity name is never copied downstream.
    name = _slug(activity.get("activityName"))
    for known in ("padel", "tennis"):
        if re.search(rf"(?:^|_){known}(?:_|$)", name):
            return known, "controlled_name_rule"
    return raw, "unknown"


def normalize_sport(activity: dict[str, Any]) -> str:
    return sport_classification(activity)[0]


def _distance_relevant(sport: str) -> bool:
    return sport in {
        "running",
        "cycling",
        "swimming",
        "walking",
        "hiking",
        "rowing",
        "cross_country_skiing",
    }


def _coverage(available: int, eligible: int, *, applicable: bool = True) -> MetricCoverage:
    if not applicable:
        return MetricCoverage(
            available_count=0, eligible_count=0, ratio=None, status="not_applicable"
        )
    ratio = round(available / eligible, 4) if eligible else None
    if available == 0:
        status = "unavailable"
    elif ratio is not None and ratio >= SUFFICIENT_METRIC_COVERAGE:
        status = "available"
    else:
        status = "partial"
    return MetricCoverage(
        available_count=available,
        eligible_count=eligible,
        ratio=ratio,
        status=status,
    )


def _aggregate_metric(values: list[float], eligible: int) -> ObservedMetricAggregate:
    coverage = _coverage(len(values), eligible)
    return ObservedMetricAggregate(
        status=coverage.status,
        observed_total=round(sum(values), 2) if values else None,
        observed_average=round(sum(values) / len(values), 2) if values else None,
        coverage=coverage,
    )


def _window(
    activities: list[dict[str, Any]], start: date, end: date
) -> WindowSummary:
    selected = [
        item
        for item in activities
        if (day := _activity_date(item)) is not None and start <= day <= end
    ]
    durations = [value for item in selected if (value := _duration(item)) is not None]
    elevations = [
        value
        for item in selected
        if (value := _number(item, "elevationGain", "totalAscent")) is not None
        and value >= 0
    ]
    average_hr_count = sum(
        _number(item, "averageHR", "averageHeartRate") is not None for item in selected
    )
    max_hr_count = sum(
        _number(item, "maxHR", "maxHeartRate") is not None for item in selected
    )
    any_hr_count = sum(
        _number(item, "averageHR", "averageHeartRate", "maxHR", "maxHeartRate")
        is not None
        for item in selected
    )
    loads = [
        value
        for item in selected
        if (value := _number(item, "activityTrainingLoad")) is not None and value >= 0
    ]
    sport_counts = Counter(normalize_sport(item) for item in selected)
    relevant = [item for item in selected if _distance_relevant(normalize_sport(item))]
    distances: dict[str, float] = defaultdict(float)
    distance_count = 0
    for item in relevant:
        value = _distance(item)
        if value is None:
            continue
        distance_count += 1
        distances[normalize_sport(item)] += value / 1000
    days = (end - start).days + 1
    return WindowSummary(
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        status="available" if selected else "no_activities",
        activity_count=len(selected),
        active_days=len({_activity_date(item) for item in selected}),
        observed_duration_seconds=round(sum(durations), 2) if durations else (0.0 if not selected else None),
        observed_distance_by_sport_km={
            key: round(value, 2) for key, value in sorted(distances.items())
        },
        observed_elevation_gain_m=round(sum(elevations), 2) if elevations else None,
        average_weekly_frequency=round(len(selected) / (days / 7), 2),
        sport_activity_counts=dict(sorted(sport_counts.items())),
        training_load=_aggregate_metric(loads, len(selected)),
        metric_coverage={
            "duration": _coverage(len(durations), len(selected)),
            "distance_when_relevant": _coverage(
                distance_count, len(relevant), applicable=bool(relevant)
            ),
            "elevation_gain": _coverage(len(elevations), len(selected)),
            "heart_rate": _coverage(any_hr_count, len(selected)),
            "heart_rate_any": _coverage(any_hr_count, len(selected)),
            "heart_rate_average": _coverage(average_hr_count, len(selected)),
            "heart_rate_maximum": _coverage(max_hr_count, len(selected)),
            "training_load": _coverage(len(loads), len(selected)),
        },
    )


def _coverage_summary(activities: list[dict[str, Any]]) -> CoverageSummary:
    days = sorted({_activity_date(item) for item in activities if _activity_date(item)})
    if not days:
        return CoverageSummary(
            first_activity_date=None,
            last_activity_date=None,
            activity_count=0,
            active_days=0,
            covered_days=None,
            active_weeks=0,
            weeks_without_recorded_activity=None,
            status="no_activities",
        )
    first, last = days[0], days[-1]
    active_week_starts = {day - timedelta(days=day.weekday()) for day in days}
    first_week = first - timedelta(days=first.weekday())
    last_week = last - timedelta(days=last.weekday())
    total_weeks = ((last_week - first_week).days // 7) + 1
    return CoverageSummary(
        first_activity_date=first.isoformat(),
        last_activity_date=last.isoformat(),
        activity_count=len(activities),
        active_days=len(days),
        covered_days=(last - first).days + 1,
        active_weeks=len(active_week_starts),
        weeks_without_recorded_activity=total_weeks - len(active_week_starts),
        status="available",
    )


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 2)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 2)


def _heart_rate_zones_from_metadata(activity: dict[str, Any]) -> dict[str, float]:
    zones: dict[str, float] = {}
    for number in range(1, 6):
        value = _number(activity, f"hrTimeInZone_{number}")
        if value is not None and value >= 0:
            zones[f"zone_{number}"] = value
    return zones


def _heart_rate_zones_from_details(details: dict[str, Any] | None) -> dict[str, float]:
    if not details:
        return {}
    endpoint = (details.get("endpoints") or {}).get("get_activity_hr_in_timezones")
    raw = endpoint.get("data") if isinstance(endpoint, dict) else None
    zones: dict[str, float] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        number, seconds = item.get("zoneNumber"), item.get("secsInZone")
        if isinstance(number, int) and isinstance(seconds, (int, float)) and seconds >= 0:
            zones[f"zone_{number}"] = float(seconds)
    return zones


def _intensity(
    running: list[dict[str, Any]], diagnostics: dict[str, Any]
) -> IntensityDistribution:
    totals: dict[str, float] = defaultdict(float)
    zone_activities = 0
    details_used = 0
    training_effects: list[float] = []
    anaerobic_effects: list[float] = []
    for item in running:
        zones = _heart_rate_zones_from_metadata(item)
        if not zones and _number(item, "averageHR", "averageHeartRate") is not None:
            diagnostics["detail_files_requested"] = diagnostics.get("detail_files_requested", 0) + 1
            details = load_local_activity_details(str(item.get("activityId") or ""))
            if details is not None:
                details_used += 1
                zones = _heart_rate_zones_from_details(details)
        if zones:
            zone_activities += 1
            for key, seconds in zones.items():
                totals[key] += seconds
        effect = _number(item, "aerobicTrainingEffect")
        if effect is not None:
            training_effects.append(effect)
        anaerobic_effect = _number(item, "anaerobicTrainingEffect")
        if anaerobic_effect is not None:
            anaerobic_effects.append(anaerobic_effect)
    zone_coverage = _coverage(zone_activities, len(running))
    effect_coverage = _coverage(len(training_effects), len(running))
    effect_counts = Counter()
    for effect in training_effects:
        if effect < 2:
            effect_counts["below_2"] += 1
        elif effect < 3:
            effect_counts["2_to_below_3"] += 1
        elif effect < 4:
            effect_counts["3_to_below_4"] += 1
        else:
            effect_counts["4_or_more"] += 1
    if zone_coverage.status == "available":
        status = "available"
    elif zone_activities or training_effects:
        status = "partial"
    elif running:
        status = "insufficient_data"
    else:
        status = "unavailable"
    diagnostics["detail_files_opened"] = diagnostics.get("detail_files_opened", 0) + details_used
    return IntensityDistribution(
        status=status,
        heart_rate_zone_seconds={key: round(value, 2) for key, value in sorted(totals.items())},
        heart_rate_zone_coverage=zone_coverage,
        aerobic_training_effect_counts=dict(sorted(effect_counts.items())),
        aerobic_training_effect_coverage=effect_coverage,
        anaerobic_training_effect_coverage=_coverage(
            len(anaerobic_effects), len(running)
        ),
        details_used=details_used,
    )


def _sport_classification_summary(
    activities: list[dict[str, Any]],
) -> SportClassificationSummary:
    sources = Counter()
    other_count = 0
    unknown_count = 0
    for activity in activities:
        sport, source = sport_classification(activity)
        sources[source] += 1
        other_count += sport == "other"
        unknown_count += sport == "unknown"
    classified = (
        sources["garmin_explicit"]
        + sources["structured_mapping"]
        + sources["controlled_name_rule"]
    )
    return SportClassificationSummary(
        total_activities=len(activities),
        garmin_explicit_count=sources["garmin_explicit"],
        structured_mapping_count=sources["structured_mapping"],
        controlled_name_rule_count=sources["controlled_name_rule"],
        other_count=other_count,
        unknown_count=unknown_count,
        classified_count=classified,
        classification_ratio=(
            round(classified / len(activities), 4) if activities else None
        ),
    )


def _performance_evidence(running: list[dict[str, Any]]) -> list[PerformanceEvidence]:
    evidence: list[PerformanceEvidence] = []
    for item in running:
        day = _activity_date(item)
        if day is None:
            continue
        distance, duration = _distance(item), _duration(item)
        if item.get("isPR") is True and distance and duration:
            evidence.append(
                PerformanceEvidence(
                    type="garmin_pr_activity",
                    distance_m=round(distance, 2),
                    duration_seconds=round(duration, 2),
                    date=day.isoformat(),
                    provenance="activity_history.isPR",
                    technical_confidence="high",
                )
            )
    # Keep the best explicit Garmin split for each supported distance.
    for key, distance_m in (
        ("fastestSplit_1000", 1000.0),
        ("fastestSplit_1609", 1609.0),
        ("fastestSplit_5000", 5000.0),
        ("fastestSplit_10000", 10000.0),
    ):
        candidates = [
            (value, _activity_date(item))
            for item in running
            if (value := _number(item, key)) is not None
            and value > 0
            and _activity_date(item) is not None
        ]
        if candidates:
            duration, day = min(candidates, key=lambda value: value[0])
            evidence.append(
                PerformanceEvidence(
                    type="garmin_fastest_split",
                    distance_m=distance_m,
                    duration_seconds=round(duration, 2),
                    date=day.isoformat(),
                    provenance=f"activity_history.{key}",
                    technical_confidence="high",
                )
            )
    prs = sorted(
        (item for item in evidence if item.type == "garmin_pr_activity"),
        key=lambda item: item.date,
        reverse=True,
    )[:5]
    splits = [item for item in evidence if item.type == "garmin_fastest_split"]
    return sorted(prs + splits, key=lambda item: (item.date, item.distance_m), reverse=True)


def _running_summary(
    activities: list[dict[str, Any]], reference: date, diagnostics: dict[str, Any]
) -> RunningSummary:
    running = [item for item in activities if normalize_sport(item) == "running"]
    recent_start = reference - timedelta(days=RECENT_DAYS - 1)
    base_start = reference - timedelta(days=BASE_DAYS - 1)
    recent = [item for item in running if (day := _activity_date(item)) and day >= recent_start]
    base = [item for item in running if (day := _activity_date(item)) and day >= base_start]

    def distances(items: list[dict[str, Any]]) -> list[float]:
        return [value for item in items if (value := _distance(item)) is not None]

    def durations(items: list[dict[str, Any]]) -> list[float]:
        return [value for item in items if (value := _duration(item)) is not None]

    recent_distances, base_distances = distances(recent), distances(base)
    recent_durations, base_durations = durations(recent), durations(base)
    elevation = [
        value
        for item in base
        if (value := _number(item, "elevationGain", "totalAscent")) is not None
        and value >= 0
    ]
    paired = [
        (distance, duration)
        for item in base
        if (distance := _distance(item)) is not None
        and distance > 0
        and (duration := _duration(item)) is not None
    ]
    ranked = sorted(
        (
            LongRunFact(
                date=_activity_date(item).isoformat(),
                distance_km=round(distance / 1000, 2),
                duration_seconds=round(_duration(item), 2) if _duration(item) is not None else None,
            )
            for item in base
            if _activity_date(item) is not None and (distance := _distance(item)) is not None
        ),
        key=lambda item: (item.distance_km, item.date),
        reverse=True,
    )
    return RunningSummary(
        sessions_last_28_days=len(recent),
        sessions_last_84_days=len(base),
        distance_last_28_days_km=round(sum(recent_distances) / 1000, 2) if recent_distances else None,
        distance_last_84_days_km=round(sum(base_distances) / 1000, 2) if base_distances else None,
        observed_duration_last_28_days_seconds=round(sum(recent_durations), 2) if recent_durations else None,
        observed_duration_last_84_days_seconds=round(sum(base_durations), 2) if base_durations else None,
        average_weekly_frequency_last_28_days=round(len(recent) / 4, 2),
        average_weekly_frequency_last_84_days=round(len(base) / 12, 2),
        average_distance_last_84_days_km=round(sum(base_distances) / len(base_distances) / 1000, 2) if base_distances else None,
        maximum_distance_last_84_days_km=round(max(base_distances) / 1000, 2) if base_distances else None,
        maximum_duration_last_84_days_seconds=round(max(base_durations), 2) if base_durations else None,
        observed_elevation_last_84_days_m=round(sum(elevation), 2) if elevation else None,
        weighted_pace_last_84_days_seconds_per_km=(
            round(sum(duration for _, duration in paired) / (sum(distance for distance, _ in paired) / 1000), 2)
            if paired and sum(distance for distance, _ in paired) > 0
            else None
        ),
        long_runs=LongRunsSummary(
            basis="personal_distance_distribution_last_84_days",
            distance_percentile_75_km=(
                round(_percentile([value / 1000 for value in base_distances], 0.75), 2)
                if base_distances
                else None
            ),
            longest_distance_km=round(max(base_distances) / 1000, 2) if base_distances else None,
            top_three=ranked[:3],
        ),
        intensity_distribution=_intensity(base, diagnostics),
        performance_evidence=_performance_evidence(running),
    )


def _sport_summaries(
    activities: list[dict[str, Any]], reference: date, covered_days: int | None
) -> list[SportSummary]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in activities:
        grouped[normalize_sport(item)].append(item)
    recent_start = reference - timedelta(days=BASE_DAYS - 1)
    recent = [item for item in activities if (day := _activity_date(item)) and day >= recent_start]
    recent_counts = Counter(normalize_sport(item) for item in recent)
    recent_durations = defaultdict(float)
    for item in recent:
        if (duration := _duration(item)) is not None:
            recent_durations[normalize_sport(item)] += duration
    total_recent_duration = sum(recent_durations.values())
    summaries: list[SportSummary] = []
    for sport, items in sorted(grouped.items()):
        durations = [value for item in items if (value := _duration(item)) is not None]
        loads = [
            value
            for item in items
            if (value := _number(item, "activityTrainingLoad")) is not None and value >= 0
        ]
        relevant = _distance_relevant(sport)
        distances = [value for item in items if (value := _distance(item)) is not None] if relevant else []
        last_day = max(_activity_date(item) for item in items if _activity_date(item) is not None)
        summaries.append(
            SportSummary(
                sport=sport,
                source_types=sorted({_raw_sport(item) for item in items}),
                activity_count=len(items),
                observed_duration_seconds=round(sum(durations), 2) if durations else None,
                average_weekly_frequency=round(len(items) / ((covered_days or 7) / 7), 2),
                observed_distance_km=round(sum(distances) / 1000, 2) if distances else None,
                distance_relevant=relevant,
                training_load=_aggregate_metric(loads, len(items)),
                last_activity_date=last_day.isoformat(),
                recent_activity_share_pct=round(recent_counts[sport] / len(recent) * 100, 2) if recent else 0.0,
                recent_duration_share_pct=(
                    round(recent_durations[sport] / total_recent_duration * 100, 2)
                    if total_recent_duration
                    else None
                ),
            )
        )
    return summaries


def _trend(current: float | None, previous: float | None, previous_empty: bool) -> TrendValue:
    if previous_empty:
        return TrendValue(
            current=current,
            previous=previous,
            absolute_change=None,
            relative_change_pct=None,
            status="previous_window_empty",
        )
    if current is None or previous is None:
        return TrendValue(
            current=current,
            previous=previous,
            absolute_change=None,
            relative_change_pct=None,
            status="previous_zero" if previous == 0 else "available",
        )
    absolute = current - previous
    return TrendValue(
        current=round(current, 2),
        previous=round(previous, 2),
        absolute_change=round(absolute, 2),
        relative_change_pct=round(absolute / previous * 100, 2) if previous != 0 else None,
        status="available" if previous != 0 else "previous_zero",
    )


def _duration_by_sport(items: list[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for item in items:
        if (duration := _duration(item)) is not None:
            totals[normalize_sport(item)] += duration
    return totals


def _trends(activities: list[dict[str, Any]], reference: date) -> TrendsSummary:
    current_start = reference - timedelta(days=27)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=27)
    current = [item for item in activities if (day := _activity_date(item)) and current_start <= day <= reference]
    previous = [item for item in activities if (day := _activity_date(item)) and previous_start <= day <= previous_end]
    previous_empty = not previous
    current_duration = [value for item in current if (value := _duration(item)) is not None]
    previous_duration = [value for item in previous if (value := _duration(item)) is not None]
    current_running = [value for item in current if normalize_sport(item) == "running" and (value := _distance(item)) is not None]
    previous_running = [value for item in previous if normalize_sport(item) == "running" and (value := _distance(item)) is not None]
    current_sports, previous_sports = _duration_by_sport(current), _duration_by_sport(previous)
    return TrendsSummary(
        current_window={"start_date": current_start.isoformat(), "end_date": reference.isoformat()},
        previous_window={"start_date": previous_start.isoformat(), "end_date": previous_end.isoformat()},
        activity_count=_trend(float(len(current)), float(len(previous)), previous_empty),
        observed_duration_seconds=_trend(
            sum(current_duration) if current_duration else (0.0 if not current else None),
            sum(previous_duration) if previous_duration else (0.0 if not previous else None),
            previous_empty,
        ),
        running_distance_km=_trend(
            sum(current_running) / 1000 if current_running else None,
            sum(previous_running) / 1000 if previous_running else None,
            previous_empty,
        ),
        observed_duration_by_sport_seconds={
            sport: _trend(current_sports.get(sport, 0.0), previous_sports.get(sport, 0.0), previous_empty)
            for sport in sorted(set(current_sports) | set(previous_sports))
        },
    )


def _interruptions(activities: list[dict[str, Any]]) -> InterruptionsSummary:
    days = sorted({_activity_date(item) for item in activities if _activity_date(item)})
    facts: list[InterruptionFact] = []
    for before, after in zip(days, days[1:]):
        missing = (after - before).days - 1
        if missing >= INTERRUPTION_MIN_DAYS:
            facts.append(
                InterruptionFact(
                    start_date=(before + timedelta(days=1)).isoformat(),
                    end_date=(after - timedelta(days=1)).isoformat(),
                    days_without_recorded_activity=missing,
                )
            )
    total = len(facts)
    return InterruptionsSummary(
        definition="Périodes internes à la couverture avec au moins 7 jours sans activité Garmin enregistrée.",
        minimum_days=INTERRUPTION_MIN_DAYS,
        total_detected=total,
        items=sorted(facts, key=lambda item: item.end_date, reverse=True)[:MAX_INTERRUPTION_ITEMS],
    )


def build_training_history_summary(
    *,
    reference_date: date | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> TrainingHistorySummary:
    """Build V1 on demand from local files only; no network and no AI."""
    measured = diagnostics if diagnostics is not None else {}
    started = time.perf_counter()
    reader_diagnostics: dict[str, int] = {}
    scope = local_history_scope()
    frozen_reference = (
        date.fromisoformat(scope["reference_date"])
        if scope.get("reference_date")
        else None
    )
    provisional_reference = reference_date or frozen_reference
    activities = load_local_activity_history(
        end_date=provisional_reference,
        diagnostics=reader_diagnostics,
    )
    available_days = [day for item in activities if (day := _activity_date(item))]
    reference = provisional_reference or (max(available_days) if available_days else None)
    if reference is None:
        raise ValueError(
            "Une reference_date déterministe est nécessaire pour résumer un historique vide."
        )
    # A supplied reference can be earlier than the frozen import reference.
    activities = [item for item in activities if (_activity_date(item) or date.max) <= reference]
    coverage = _coverage_summary(activities)
    recent_start = reference - timedelta(days=RECENT_DAYS - 1)
    base_start = reference - timedelta(days=BASE_DAYS - 1)
    historical_start = (
        date.fromisoformat(coverage.first_activity_date)
        if coverage.first_activity_date
        else reference
    )
    windows = {
        "last_28_days": _window(activities, recent_start, reference),
        "last_84_days": _window(activities, base_start, reference),
        "historical": _window(activities, historical_start, reference),
    }
    sports = _sport_summaries(activities, reference, coverage.covered_days)
    running = _running_summary(activities, reference, measured)
    summary = TrainingHistorySummary(
        as_of=reference.isoformat(),
        source=scope["source"],
        scope=scope.get("scope"),
        scope_cutoff_date=scope.get("cutoff_date"),
        coverage=coverage,
        windows=windows,
        sports=sports,
        sport_classification=_sport_classification_summary(activities),
        running=running,
        cross_training=[item for item in sports if item.sport != "running"],
        trends=_trends(activities, reference),
        interruptions=_interruptions(activities),
        source_measurements=SourceMeasurements(
            metadata_files_read=reader_diagnostics.get("metadata_files_read", 0),
            in_scope_activities=len(activities),
            detail_files_opened=int(measured.get("detail_files_opened", 0)),
        ),
    )
    measured["metadata_files_read"] = reader_diagnostics.get("metadata_files_read", 0)
    measured["in_scope_activities"] = len(activities)
    measured["computation_seconds"] = round(time.perf_counter() - started, 6)
    return summary


def summary_for_ai(summary: TrainingHistorySummary) -> dict[str, Any]:
    """Return the already compact, identifier-free contract for future wiring."""
    return summary.model_dump(mode="json")
