"""Deterministic GoalEvidence V1 facts from existing V1 contracts.

The result calculates and structures evidence for a future assessment.  It
does not judge feasibility, promise performance, or recommend a plan.
"""
from __future__ import annotations

import math
import time
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .athlete_state import (
    RECENT_PERFORMANCE_MAX_AGE_DAYS,
    AthleteState,
    PerformanceReference,
)
from .data_confidence import ConfidenceLevel, DataConfidence
from .goal import GoalV1, RaceEventType
from .training_history_summary import TrainingHistorySummary


SCHEMA_VERSION = 1
RULE_SET_VERSION = 1

# Riegel is used only as a comparison between observed road-running evidence
# and a standard road target.  It is not a guarantee or future prediction.
RIEGEL_EXPONENT_V1 = 1.06
STANDARD_ROAD_EVENTS = frozenset({"5k", "10k", "half_marathon", "marathon"})

EvidenceStatus = Literal[
    "available",
    "partial",
    "insufficient_data",
    "not_applicable",
]
AssessmentScope = Literal[
    "race_target_time",
    "race_without_target_time",
    "race_without_date",
    "non_race",
]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RaceAssessment(StrictContract):
    event_type: RaceEventType | None
    distance_km: float | None = Field(default=None, gt=0)
    date: str | None
    days_to_event: int | None
    target_time_seconds: int | None = Field(default=None, gt=0)
    target_pace_seconds_per_km: float | None = Field(default=None, gt=0)


class SelectedPerformanceReference(StrictContract):
    distance_m: float = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    date: str
    age_days: int = Field(ge=0)
    recency: Literal["recent", "historical"]
    source: Literal["garmin_pr_activity", "garmin_fastest_split"]
    provenance: str
    confidence: Literal["high", "medium"]


class PerformanceComparison(StrictContract):
    status: EvidenceStatus
    reference: SelectedPerformanceReference | None
    equivalent_target_distance_seconds: float | None = Field(default=None, gt=0)
    target_vs_equivalent_seconds: float | None = None
    target_vs_equivalent_pct: float | None = None
    method: Literal["riegel_v1"] | None
    exponent: float | None = Field(default=None, gt=0)


class TrainingSupport(StrictContract):
    running_sessions_last_28_days: int = Field(ge=0)
    running_sessions_last_84_days: int = Field(ge=0)
    running_distance_last_28_days_km: float | None = Field(default=None, ge=0)
    running_distance_last_84_days_km: float | None = Field(default=None, ge=0)
    longest_run_last_84_days_km: float | None = Field(default=None, ge=0)
    personal_long_run_p75_km: float | None = Field(default=None, ge=0)
    long_run_ratio_to_event: float | None = Field(default=None, ge=0)
    observed_frequency_last_84_days: float = Field(ge=0)
    recorded_interruptions_count: int = Field(ge=0)


class GoalEvidenceConfidence(StrictContract):
    training_history: ConfidenceLevel
    current_state: ConfidenceLevel
    performance_evidence: ConfidenceLevel


class GoalEvidenceItem(StrictContract):
    code: str
    details: dict[str, Any] = Field(default_factory=dict)


class GoalEvidenceV1(StrictContract):
    schema_version: Literal[1] = SCHEMA_VERSION
    rule_set_version: Literal[1] = RULE_SET_VERSION
    as_of: str
    goal_intent: Literal["progress", "race", "beginner", "comeback"]
    assessment_scope: AssessmentScope
    status: EvidenceStatus
    race: RaceAssessment
    performance_comparison: PerformanceComparison
    training_support: TrainingSupport
    confidence: GoalEvidenceConfidence
    limitations: list[str]
    evidence_used: list[GoalEvidenceItem]


def _training_support(
    goal: GoalV1, athlete: AthleteState, summary: TrainingHistorySummary
) -> TrainingSupport:
    running = summary.running
    event_distance = goal.race.distance_km if goal.race is not None else None
    longest = running.long_runs.longest_distance_km
    ratio = (
        round(longest / event_distance, 4)
        if longest is not None and event_distance is not None
        else None
    )
    return TrainingSupport(
        running_sessions_last_28_days=running.sessions_last_28_days,
        running_sessions_last_84_days=running.sessions_last_84_days,
        running_distance_last_28_days_km=running.distance_last_28_days_km,
        running_distance_last_84_days_km=running.distance_last_84_days_km,
        longest_run_last_84_days_km=longest,
        personal_long_run_p75_km=running.long_runs.distance_percentile_75_km,
        long_run_ratio_to_event=ratio,
        observed_frequency_last_84_days=(
            athlete.running_state.observed_running_sessions_per_week_last_84_days
        ),
        recorded_interruptions_count=len(athlete.continuity.recorded_interruptions),
    )


def _limitations(
    athlete: AthleteState,
    confidence: DataConfidence,
    support: TrainingSupport,
) -> list[str]:
    result: set[str] = set()
    if confidence.training_history_confidence.confidence in {"none", "low"}:
        result.add("limited_training_history")
    if confidence.current_state_confidence.confidence in {"none", "low"}:
        result.add("current_state_limited")
    if any(item.code == "last_activity_stale" for item in confidence.stale_data):
        result.add("current_state_stale")
    if confidence.components.training_load.confidence == "none":
        result.add("training_load_unavailable")
    if confidence.components.recovery.confidence == "none":
        result.add("recovery_unavailable")
    if confidence.components.performance_evidence.confidence == "none":
        result.add("performance_evidence_insufficient")
    elif confidence.components.performance_evidence.confidence == "low":
        result.add("performance_evidence_low_confidence")
    if support.longest_run_last_84_days_km is None:
        result.add("long_run_unavailable")
    return sorted(result)


def _reference_key(
    reference: PerformanceReference, target_distance_m: float
) -> tuple[Any, ...]:
    """Order by recency class, confidence, distance relevance, then stable ties."""
    return (
        0 if reference.recency == "recent" else 1,
        0 if reference.confidence == "high" else 1,
        abs(math.log(reference.distance_m / target_distance_m)),
        reference.age_days,
        reference.date,
        reference.source,
        reference.provenance,
        reference.duration_seconds,
        reference.distance_m,
    )


def _select_reference(
    summary: TrainingHistorySummary,
    reference_date: date,
    target_distance_m: float,
) -> PerformanceReference | None:
    references: list[PerformanceReference] = []
    for item in summary.running.performance_evidence:
        evidence_date = date.fromisoformat(item.date)
        age_days = max(0, (reference_date - evidence_date).days)
        references.append(
            PerformanceReference(
                distance_m=item.distance_m,
                duration_seconds=item.duration_seconds,
                date=item.date,
                age_days=age_days,
                recency=(
                    "recent"
                    if age_days <= RECENT_PERFORMANCE_MAX_AGE_DAYS
                    else "historical"
                ),
                source=item.type,
                provenance=item.provenance,
                confidence=item.technical_confidence,
            )
        )
    return min(references, key=lambda item: _reference_key(item, target_distance_m)) if references else None


def _selected(reference: PerformanceReference) -> SelectedPerformanceReference:
    return SelectedPerformanceReference.model_validate(reference.model_dump(mode="json"))


def _empty_comparison(status: EvidenceStatus) -> PerformanceComparison:
    return PerformanceComparison(
        status=status,
        reference=None,
        equivalent_target_distance_seconds=None,
        target_vs_equivalent_seconds=None,
        target_vs_equivalent_pct=None,
        method=None,
        exponent=None,
    )


def _evidence(support: TrainingSupport) -> list[GoalEvidenceItem]:
    return [
        GoalEvidenceItem(
            code="running_history_28_84_days",
            details={
                "sessions_28_days": support.running_sessions_last_28_days,
                "sessions_84_days": support.running_sessions_last_84_days,
                "distance_28_days_km": support.running_distance_last_28_days_km,
                "distance_84_days_km": support.running_distance_last_84_days_km,
            },
        ),
        GoalEvidenceItem(
            code="long_run_context",
            details={
                "longest_84_days_km": support.longest_run_last_84_days_km,
                "personal_p75_km": support.personal_long_run_p75_km,
                "ratio_to_event": support.long_run_ratio_to_event,
            },
        ),
        GoalEvidenceItem(
            code="continuity_context",
            details={"recorded_interruptions": support.recorded_interruptions_count},
        ),
    ]


def build_goal_evidence_v1(
    goal: GoalV1,
    athlete_state: AthleteState,
    training_history_summary: TrainingHistorySummary,
    data_confidence: DataConfidence,
    *,
    reference_date: date,
    diagnostics: dict[str, Any] | None = None,
) -> GoalEvidenceV1:
    """Build goal facts from existing contracts, without I/O or hidden time."""
    measured = diagnostics if diagnostics is not None else {}
    started = time.perf_counter()
    expected_as_of = reference_date.isoformat()
    if {
        athlete_state.as_of,
        training_history_summary.as_of,
        data_confidence.as_of,
    } != {expected_as_of}:
        raise ValueError(
            "GoalEvidence exige des contrats alignés sur reference_date."
        )

    support = _training_support(goal, athlete_state, training_history_summary)
    confidence = GoalEvidenceConfidence(
        training_history=data_confidence.training_history_confidence.confidence,
        current_state=data_confidence.current_state_confidence.confidence,
        performance_evidence=data_confidence.components.performance_evidence.confidence,
    )
    limitations = _limitations(athlete_state, data_confidence, support)
    evidence = _evidence(support)

    race = goal.race
    race_assessment = RaceAssessment(
        event_type=race.event_type if race else None,
        distance_km=race.distance_km if race else None,
        date=race.date.isoformat() if race and race.date else None,
        days_to_event=(race.date - reference_date).days if race and race.date else None,
        target_time_seconds=(race.target_time.seconds if race and race.target_time else None),
        target_pace_seconds_per_km=(
            round(race.target_time.seconds / race.distance_km, 3)
            if race and race.target_time and race.distance_km
            else None
        ),
    )

    if goal.intent != "race":
        result = GoalEvidenceV1(
            as_of=expected_as_of,
            goal_intent=goal.intent,
            assessment_scope="non_race",
            status="not_applicable",
            race=race_assessment,
            performance_comparison=_empty_comparison("not_applicable"),
            training_support=support,
            confidence=confidence,
            limitations=limitations,
            evidence_used=evidence,
        )
        measured["computation_seconds"] = round(time.perf_counter() - started, 6)
        return result

    assert race is not None
    if race.target_time is None:
        result = GoalEvidenceV1(
            as_of=expected_as_of,
            goal_intent="race",
            assessment_scope="race_without_target_time",
            status="partial",
            race=race_assessment,
            performance_comparison=_empty_comparison("not_applicable"),
            training_support=support,
            confidence=confidence,
            limitations=sorted({*limitations, "race_target_time_missing"}),
            evidence_used=evidence,
        )
        measured["computation_seconds"] = round(time.perf_counter() - started, 6)
        return result

    scope: AssessmentScope = "race_target_time" if race.date else "race_without_date"
    if race.date is None:
        limitations = sorted({*limitations, "race_date_missing"})
    elif race.date < reference_date:
        limitations = sorted({*limitations, "race_date_passed"})

    if race.event_type not in STANDARD_ROAD_EVENTS or race.distance_km is None:
        limitations = sorted({*limitations, "course_specific_comparison_unavailable"})
        result = GoalEvidenceV1(
            as_of=expected_as_of,
            goal_intent="race",
            assessment_scope=scope,
            status="partial",
            race=race_assessment,
            performance_comparison=_empty_comparison("insufficient_data"),
            training_support=support,
            confidence=confidence,
            limitations=limitations,
            evidence_used=evidence,
        )
        measured["computation_seconds"] = round(time.perf_counter() - started, 6)
        return result

    performance_confidence = data_confidence.components.performance_evidence.confidence
    reference = _select_reference(
        training_history_summary, reference_date, race.distance_km * 1000
    )
    if reference is None:
        result = GoalEvidenceV1(
            as_of=expected_as_of,
            goal_intent="race",
            assessment_scope=scope,
            status="insufficient_data",
            race=race_assessment,
            performance_comparison=_empty_comparison("insufficient_data"),
            training_support=support,
            confidence=confidence,
            limitations=limitations,
            evidence_used=evidence,
        )
        measured["computation_seconds"] = round(time.perf_counter() - started, 6)
        return result

    target_distance_m = race.distance_km * 1000
    equivalent = reference.duration_seconds * (
        target_distance_m / reference.distance_m
    ) ** RIEGEL_EXPONENT_V1
    target_seconds = float(race.target_time.seconds)
    delta_seconds = target_seconds - equivalent
    delta_pct = delta_seconds / equivalent * 100
    comparison_evidence = GoalEvidenceItem(
        code="riegel_road_comparison_v1",
        details={
            "exponent": RIEGEL_EXPONENT_V1,
            "target_distance_m": round(target_distance_m, 3),
        },
    )
    evidence.append(comparison_evidence)
    evidence.append(
        GoalEvidenceItem(
            code="selected_performance_reference",
            details={
                "distance_m": reference.distance_m,
                "duration_seconds": reference.duration_seconds,
                "date": reference.date,
                "age_days": reference.age_days,
                "confidence": reference.confidence,
                "source": reference.source,
            },
        )
    )
    if reference.recency == "historical":
        limitations = sorted({*limitations, "historical_performance_reference"})

    comparison_status: EvidenceStatus = (
        "partial" if performance_confidence == "low" else "available"
    )
    result = GoalEvidenceV1(
        as_of=expected_as_of,
        goal_intent="race",
        assessment_scope=scope,
        status=(
            "partial"
            if (
                comparison_status == "partial"
                or race.date is None
                or race.date < reference_date
            )
            else "available"
        ),
        race=race_assessment,
        performance_comparison=PerformanceComparison(
            status=comparison_status,
            reference=_selected(reference),
            equivalent_target_distance_seconds=round(equivalent, 2),
            target_vs_equivalent_seconds=round(delta_seconds, 2),
            target_vs_equivalent_pct=round(delta_pct, 3),
            method="riegel_v1",
            exponent=RIEGEL_EXPONENT_V1,
        ),
        training_support=support,
        confidence=confidence,
        limitations=limitations,
        evidence_used=evidence,
    )
    measured["computation_seconds"] = round(time.perf_counter() - started, 6)
    return result


def goal_evidence_for_ai(evidence: GoalEvidenceV1) -> dict[str, Any]:
    """Return the compact, identity-free facts without invoking an AI."""
    return evidence.model_dump(mode="json")
