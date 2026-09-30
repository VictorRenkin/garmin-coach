"""Compact, deterministic AthleteState V1 derived from existing summaries."""
from __future__ import annotations

import time
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from .data_confidence import ConfidenceLevel, DataConfidence
from .training_history_summary import (
    PerformanceEvidence,
    SportSummary,
    TrainingHistorySummary,
    TrendValue,
)


SCHEMA_VERSION = 1
RULE_SET_VERSION = 1
TREND_STABLE_THRESHOLD_PCT = 5.0
RECENT_PERFORMANCE_MAX_AGE_DAYS = 180

StateStatus = Literal["available", "partial", "insufficient_data"]
AvailabilityStatus = Literal[
    "available", "partial", "unavailable", "insufficient_data", "not_applicable"
]
ChangeDirection = Literal[
    "increased", "decreased", "stable", "newly_observed", "unavailable"
]


class MetricAvailability(BaseModel):
    status: AvailabilityStatus
    confidence: ConfidenceLevel
    covered_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)


class SportMixFact(BaseModel):
    sport: str
    activities_last_28_days: int = Field(ge=0)
    activities_last_84_days: int = Field(ge=0)
    observed_duration_last_28_days_seconds: float | None = Field(default=None, ge=0)
    observed_duration_last_84_days_seconds: float | None = Field(default=None, ge=0)
    duration_share_last_84_days_pct: float | None = Field(default=None, ge=0, le=100)
    observed_distance_last_28_days_km: float | None = Field(default=None, ge=0)
    observed_distance_last_84_days_km: float | None = Field(default=None, ge=0)
    distance_relevant: bool
    last_activity_date: str


class SportProfile(BaseModel):
    sports_observed: list[str]
    recent_sport_mix: list[SportMixFact]
    running_present: bool
    primary_sport_status: Literal["not_inferred"] = "not_inferred"


class CurrentTraining(BaseModel):
    observed_sessions_per_week_last_28_days: float = Field(ge=0)
    observed_sessions_per_week_last_84_days: float = Field(ge=0)
    active_days_last_28_days: int = Field(ge=0)
    active_days_last_84_days: int = Field(ge=0)
    observed_duration_last_28_days_seconds: float | None = Field(default=None, ge=0)
    observed_duration_last_84_days_seconds: float | None = Field(default=None, ge=0)
    last_activity_date: str | None
    last_activity_age_days: int | None = Field(default=None, ge=0)
    sports_practiced_last_28_days: int = Field(ge=0)
    running_distance_last_28_days_km: float | None = Field(default=None, ge=0)
    distance_by_sport_last_28_days_km: dict[str, float]


class LongRunProfile(BaseModel):
    basis: Literal["personal_distance_distribution_last_84_days"]
    longest_recent_km: float | None = Field(default=None, ge=0)
    personal_p75_km: float | None = Field(default=None, ge=0)
    top_recent_runs_km: list[float]


class RunningState(BaseModel):
    status: AvailabilityStatus
    sessions_last_28_days: int = Field(ge=0)
    sessions_last_84_days: int = Field(ge=0)
    observed_running_sessions_per_week_last_28_days: float = Field(ge=0)
    observed_running_sessions_per_week_last_84_days: float = Field(ge=0)
    distance_last_28_days_km: float | None = Field(default=None, ge=0)
    distance_last_84_days_km: float | None = Field(default=None, ge=0)
    observed_duration_last_28_days_seconds: float | None = Field(default=None, ge=0)
    observed_duration_last_84_days_seconds: float | None = Field(default=None, ge=0)
    average_distance_last_84_days_km: float | None = Field(default=None, ge=0)
    weighted_pace_last_84_days_seconds_per_km: float | None = Field(default=None, ge=0)
    observed_elevation_last_84_days_m: float | None = Field(default=None, ge=0)
    long_run_profile: LongRunProfile
    heart_rate: MetricAvailability
    heart_rate_zones: MetricAvailability
    aerobic_training_effect: MetricAvailability
    anaerobic_training_effect: MetricAvailability
    training_load_status: Literal["unavailable", "available"]


class PerformanceReference(BaseModel):
    distance_m: float = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    date: str
    age_days: int = Field(ge=0)
    recency: Literal["recent", "historical"]
    source: Literal["garmin_pr_activity", "garmin_fastest_split"]
    provenance: str
    confidence: Literal["high", "medium"]


class CurrentCapability(BaseModel):
    status: Literal["supported", "partial", "insufficient_data"]
    reference_performances: list[PerformanceReference]
    prediction_status: Literal["not_computed"] = "not_computed"


class ChangeFact(BaseModel):
    current: float | None
    previous: float | None
    absolute_change: float | None
    relative_change_pct: float | None
    direction: ChangeDirection


class RecentChanges(BaseModel):
    comparison: Literal["last_28_days_vs_previous_28_days"]
    stable_threshold_pct: float
    activity_count: ChangeFact
    total_duration_seconds: ChangeFact
    running_distance_km: ChangeFact
    sport_duration_seconds: dict[str, ChangeFact]


class Continuity(BaseModel):
    definition: str
    last_activity_date: str | None
    last_activity_age_days: int | None = Field(default=None, ge=0)
    active_weeks_recorded: int = Field(ge=0)
    weeks_without_recorded_activity: int | None = Field(default=None, ge=0)
    recorded_interruptions: list[dict[str, Any]]


class UnavailableState(BaseModel):
    status: Literal["unavailable"] = "unavailable"
    missing_sources: list[str] = Field(default_factory=list)


class InterpretationBoundaries(BaseModel):
    experience_state: Literal["unknown"] = "unknown"
    load_tolerance: Literal["unknown"] = "unknown"
    primary_sport: Literal["not_inferred"] = "not_inferred"


class AthleteLimitation(BaseModel):
    code: str
    affects: list[str]
    affected_activities: int | None = Field(default=None, ge=0)


class AthleteStateConfidence(BaseModel):
    training_history: ConfidenceLevel
    current_state: ConfidenceLevel
    sport_classification: ConfidenceLevel
    heart_rate: ConfidenceLevel
    heart_rate_zones: ConfidenceLevel
    training_effect: ConfidenceLevel
    training_load: ConfidenceLevel
    performance_evidence: ConfidenceLevel
    recovery: ConfidenceLevel


class AthleteState(BaseModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    rule_set_version: Literal[1] = RULE_SET_VERSION
    as_of: str
    status: StateStatus
    sport_profile: SportProfile
    current_training: CurrentTraining
    running_state: RunningState
    cross_training: list[SportMixFact]
    current_capability: CurrentCapability
    recent_changes: RecentChanges
    continuity: Continuity
    recovery_state: UnavailableState
    interpretation_boundaries: InterpretationBoundaries
    limitations: list[AthleteLimitation]
    confidence: AthleteStateConfidence


def _metric(component: Any) -> MetricAvailability:
    evidence = component.evidence
    return MetricAvailability(
        status=component.status,
        confidence=component.confidence,
        covered_count=int(evidence.get("covered_count", 0)),
        eligible_count=int(evidence.get("eligible_count", 0)),
    )


def _sport_mix(summary: TrainingHistorySummary) -> list[SportMixFact]:
    recent = summary.windows["last_28_days"]
    base = summary.windows["last_84_days"]
    trend_durations = summary.trends.observed_duration_by_sport_seconds
    result: list[SportMixFact] = []
    for sport in summary.sports:
        result.append(
            SportMixFact(
                sport=sport.sport,
                activities_last_28_days=recent.sport_activity_counts.get(sport.sport, 0),
                activities_last_84_days=base.sport_activity_counts.get(sport.sport, 0),
                observed_duration_last_28_days_seconds=(
                    trend_durations[sport.sport].current
                    if sport.sport in trend_durations
                    else None
                ),
                observed_duration_last_84_days_seconds=sport.observed_duration_last_84_days_seconds,
                duration_share_last_84_days_pct=sport.recent_duration_share_pct,
                observed_distance_last_28_days_km=recent.observed_distance_by_sport_km.get(sport.sport),
                observed_distance_last_84_days_km=base.observed_distance_by_sport_km.get(sport.sport),
                distance_relevant=sport.distance_relevant,
                last_activity_date=sport.last_activity_date,
            )
        )
    return sorted(
        (item for item in result if item.activities_last_84_days > 0),
        key=lambda item: (
            -(item.observed_duration_last_84_days_seconds or 0),
            -item.activities_last_84_days,
            item.sport,
        ),
    )


def _direction(trend: TrendValue) -> ChangeDirection:
    if trend.status == "previous_window_empty" or trend.current is None:
        return "unavailable"
    if trend.status == "previous_zero":
        return "newly_observed" if trend.current > 0 else "stable"
    if trend.relative_change_pct is None:
        return "unavailable"
    if trend.relative_change_pct > TREND_STABLE_THRESHOLD_PCT:
        return "increased"
    if trend.relative_change_pct < -TREND_STABLE_THRESHOLD_PCT:
        return "decreased"
    return "stable"


def _change(trend: TrendValue) -> ChangeFact:
    return ChangeFact(
        current=trend.current,
        previous=trend.previous,
        absolute_change=trend.absolute_change,
        relative_change_pct=trend.relative_change_pct,
        direction=_direction(trend),
    )


def _capability(
    summary: TrainingHistorySummary, confidence: DataConfidence, reference: date
) -> CurrentCapability:
    level = confidence.components.performance_evidence.confidence
    best: dict[float, PerformanceEvidence] = {}
    for evidence in summary.running.performance_evidence:
        current = best.get(evidence.distance_m)
        if current is None or (evidence.duration_seconds, evidence.date, evidence.provenance) < (
            current.duration_seconds,
            current.date,
            current.provenance,
        ):
            best[evidence.distance_m] = evidence
    references = []
    for distance_m, evidence in sorted(best.items()):
        age = max(0, (reference - date.fromisoformat(evidence.date)).days)
        references.append(
            PerformanceReference(
                distance_m=distance_m,
                duration_seconds=evidence.duration_seconds,
                date=evidence.date,
                age_days=age,
                recency=(
                    "recent" if age <= RECENT_PERFORMANCE_MAX_AGE_DAYS else "historical"
                ),
                source=evidence.type,
                provenance=evidence.provenance,
                confidence=evidence.technical_confidence,
            )
        )
    if level == "none" or not references:
        status = "insufficient_data"
    elif level == "high":
        status = "supported"
    else:
        status = "partial"
    return CurrentCapability(status=status, reference_performances=references)


def _limitations(
    summary: TrainingHistorySummary, confidence: DataConfidence
) -> list[AthleteLimitation]:
    result: list[AthleteLimitation] = []
    components = confidence.components
    if components.recovery.confidence == "none":
        result.append(AthleteLimitation(code="recovery_unavailable", affects=["recovery", "readiness"]))
    if components.training_load.confidence == "none":
        result.append(AthleteLimitation(code="training_load_unavailable", affects=["load_interpretation"]))
    if components.heart_rate.confidence == "none":
        result.append(AthleteLimitation(code="heart_rate_unavailable", affects=["heart_rate_interpretation"]))
    if components.heart_rate_zones.confidence == "none":
        result.append(AthleteLimitation(code="heart_rate_zones_unavailable", affects=["zone_interpretation"]))
    anaerobic = summary.running.intensity_distribution.anaerobic_training_effect_coverage
    if anaerobic.available_count == 0 and anaerobic.eligible_count:
        result.append(AthleteLimitation(code="anaerobic_training_effect_unavailable", affects=["anaerobic_effect_interpretation"]))
    if components.performance_evidence.confidence == "none":
        result.append(AthleteLimitation(code="performance_evidence_unavailable", affects=["current_capability"]))
    unclassified = summary.sport_classification.other_count + summary.sport_classification.unknown_count
    if unclassified:
        result.append(
            AthleteLimitation(
                code="sport_classification_partial",
                affects=["sport_profile"],
                affected_activities=unclassified,
            )
        )
    return sorted(result, key=lambda item: item.code)


def build_athlete_state(
    summary: TrainingHistorySummary,
    confidence: DataConfidence,
    *,
    reference_date: date | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> AthleteState:
    """Build AthleteState solely from compact deterministic inputs."""
    measured = diagnostics if diagnostics is not None else {}
    started = time.perf_counter()
    reference = reference_date or date.fromisoformat(summary.as_of)
    if confidence.as_of != reference.isoformat() or summary.as_of != reference.isoformat():
        raise ValueError("AthleteState exige des entrées alignées sur la même reference_date.")

    recent = summary.windows["last_28_days"]
    base = summary.windows["last_84_days"]
    last_day = date.fromisoformat(summary.coverage.last_activity_date) if summary.coverage.last_activity_date else None
    last_age = max(0, (reference - last_day).days) if last_day else None
    mix = _sport_mix(summary)
    running = summary.running
    intensity = running.intensity_distribution
    current_level = confidence.current_state_confidence.confidence
    if summary.coverage.activity_count == 0 or current_level == "none":
        state_status: StateStatus = "insufficient_data"
    elif current_level in {"high", "medium"}:
        state_status = "available"
    else:
        state_status = "partial"
    if running.sessions_last_84_days == 0:
        running_status: AvailabilityStatus = "not_applicable"
    elif confidence.components.recent_activity.confidence in {"high", "medium"}:
        running_status = "available"
    else:
        running_status = "partial"

    recovery_sources = confidence.components.recovery.evidence.get("missing_sources", [])
    result = AthleteState(
        as_of=reference.isoformat(),
        status=state_status,
        sport_profile=SportProfile(
            sports_observed=sorted(item.sport for item in summary.sports),
            recent_sport_mix=mix,
            running_present=any(item.sport == "running" for item in summary.sports),
        ),
        current_training=CurrentTraining(
            observed_sessions_per_week_last_28_days=recent.average_weekly_frequency,
            observed_sessions_per_week_last_84_days=base.average_weekly_frequency,
            active_days_last_28_days=recent.active_days,
            active_days_last_84_days=base.active_days,
            observed_duration_last_28_days_seconds=recent.observed_duration_seconds,
            observed_duration_last_84_days_seconds=base.observed_duration_seconds,
            last_activity_date=summary.coverage.last_activity_date,
            last_activity_age_days=last_age,
            sports_practiced_last_28_days=len(recent.sport_activity_counts),
            running_distance_last_28_days_km=recent.observed_distance_by_sport_km.get("running"),
            distance_by_sport_last_28_days_km=recent.observed_distance_by_sport_km,
        ),
        running_state=RunningState(
            status=running_status,
            sessions_last_28_days=running.sessions_last_28_days,
            sessions_last_84_days=running.sessions_last_84_days,
            observed_running_sessions_per_week_last_28_days=running.average_weekly_frequency_last_28_days,
            observed_running_sessions_per_week_last_84_days=running.average_weekly_frequency_last_84_days,
            distance_last_28_days_km=running.distance_last_28_days_km,
            distance_last_84_days_km=running.distance_last_84_days_km,
            observed_duration_last_28_days_seconds=running.observed_duration_last_28_days_seconds,
            observed_duration_last_84_days_seconds=running.observed_duration_last_84_days_seconds,
            average_distance_last_84_days_km=running.average_distance_last_84_days_km,
            weighted_pace_last_84_days_seconds_per_km=running.weighted_pace_last_84_days_seconds_per_km,
            observed_elevation_last_84_days_m=running.observed_elevation_last_84_days_m,
            long_run_profile=LongRunProfile(
                basis=running.long_runs.basis,
                longest_recent_km=running.long_runs.longest_distance_km,
                personal_p75_km=running.long_runs.distance_percentile_75_km,
                top_recent_runs_km=[item.distance_km for item in running.long_runs.top_three],
            ),
            heart_rate=_metric(confidence.components.heart_rate),
            heart_rate_zones=_metric(confidence.components.heart_rate_zones),
            aerobic_training_effect=_metric(confidence.components.training_effect),
            anaerobic_training_effect=MetricAvailability(
                status=intensity.anaerobic_training_effect_coverage.status,
                confidence=(
                    "high" if intensity.anaerobic_training_effect_coverage.status == "available"
                    else "none" if intensity.anaerobic_training_effect_coverage.available_count == 0
                    else "low"
                ),
                covered_count=intensity.anaerobic_training_effect_coverage.available_count,
                eligible_count=intensity.anaerobic_training_effect_coverage.eligible_count,
            ),
            training_load_status=(
                "available" if confidence.components.training_load.confidence != "none" else "unavailable"
            ),
        ),
        cross_training=[item for item in mix if item.sport != "running"],
        current_capability=_capability(summary, confidence, reference),
        recent_changes=RecentChanges(
            comparison="last_28_days_vs_previous_28_days",
            stable_threshold_pct=TREND_STABLE_THRESHOLD_PCT,
            activity_count=_change(summary.trends.activity_count),
            total_duration_seconds=_change(summary.trends.observed_duration_seconds),
            running_distance_km=_change(summary.trends.running_distance_km),
            sport_duration_seconds={
                sport: _change(trend)
                for sport, trend in sorted(summary.trends.observed_duration_by_sport_seconds.items())
            },
        ),
        continuity=Continuity(
            definition=summary.interruptions.definition,
            last_activity_date=summary.coverage.last_activity_date,
            last_activity_age_days=last_age,
            active_weeks_recorded=summary.coverage.active_weeks,
            weeks_without_recorded_activity=summary.coverage.weeks_without_recorded_activity,
            recorded_interruptions=[item.model_dump(mode="json") for item in summary.interruptions.items],
        ),
        recovery_state=UnavailableState(
            missing_sources=sorted(str(item) for item in recovery_sources)
        ),
        interpretation_boundaries=InterpretationBoundaries(),
        limitations=_limitations(summary, confidence),
        confidence=AthleteStateConfidence(
            training_history=confidence.training_history_confidence.confidence,
            current_state=confidence.current_state_confidence.confidence,
            sport_classification=confidence.components.sport_classification.confidence,
            heart_rate=confidence.components.heart_rate.confidence,
            heart_rate_zones=confidence.components.heart_rate_zones.confidence,
            training_effect=confidence.components.training_effect.confidence,
            training_load=confidence.components.training_load.confidence,
            performance_evidence=confidence.components.performance_evidence.confidence,
            recovery=confidence.components.recovery.confidence,
        ),
    )
    measured["computation_seconds"] = round(time.perf_counter() - started, 6)
    return result


def athlete_state_for_ai(state: AthleteState) -> dict[str, Any]:
    """Return the exact compact, privacy-safe V1 contract for future AI use."""
    return state.model_dump(mode="json")
