"""Compact deterministic integration of Motionest's six V1 contracts."""
from __future__ import annotations

import math
import time
from datetime import date as Date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .athlete_state import AthleteState, PerformanceReference
from .availability import AvailabilityV1
from .data_confidence import ConfidenceLevel, DataConfidence
from .goal import GoalV1
from .subjective_context import PainStatus, SubjectiveContextV1
from .training_history_summary import TrainingHistorySummary


SCHEMA_VERSION = 1
IntegrationStatus = Literal[
    "ready", "ready_with_limitations", "partial", "insufficient"
]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextIssue(StrictContract):
    code: str
    source: str
    blocking: bool


class SafetyContext(StrictContract):
    pain_status: PainStatus
    pain_or_injury_affecting_training: bool | None
    pain_description_present: bool
    unrecorded_activities_count: int = Field(ge=0)


class FrequencyContext(StrictContract):
    available_day_count: int | None = Field(default=None, ge=1, le=7)
    preferred_sessions_per_week: int | None = Field(default=None, ge=1, le=7)
    observed_sessions_per_week_last_28_days: float = Field(ge=0)
    preferred_minus_observed_sessions: float | None = None


class AthleteContextSnapshot(StrictContract):
    status: str
    sports_observed: list[str]
    last_activity_date: str | None
    recent_activity_count: int = Field(ge=0)
    observed_sessions_per_week_last_28_days: float = Field(ge=0)
    running_sessions_last_28_days: int = Field(ge=0)
    running_distance_last_28_days_km: float | None = Field(default=None, ge=0)
    capability_status: str
    capability_references: list[PerformanceReference]
    recent_change_directions: dict[str, str]


class TrainingHistoryContextSnapshot(StrictContract):
    status: str
    source: str
    scope: str | None
    activity_count: int = Field(ge=0)
    covered_days: int | None = Field(default=None, ge=1)
    activity_count_last_28_days: int = Field(ge=0)
    activity_count_last_84_days: int = Field(ge=0)
    active_days_last_28_days: int = Field(ge=0)
    last_activity_date: str | None
    recorded_interruptions_count: int = Field(ge=0)


class ConfidenceContextSnapshot(StrictContract):
    training_history: ConfidenceLevel
    current_state: ConfidenceLevel
    sport_classification: ConfidenceLevel
    heart_rate: ConfidenceLevel
    heart_rate_zones: ConfidenceLevel
    training_effect: ConfidenceLevel
    training_load: ConfidenceLevel
    performance_evidence: ConfidenceLevel
    recovery: ConfidenceLevel
    missing_data_codes: list[str]
    stale_data_codes: list[str]
    warning_codes: list[str]


class IntegrationAssessment(StrictContract):
    status: IntegrationStatus
    status_reasons: list[str]
    blocking_issues: list[ContextIssue]
    limitations: list[ContextIssue]
    diagnostics: list[ContextIssue]
    safety_context: SafetyContext
    frequency_context: FrequencyContext


class MotionestContextV1(StrictContract):
    schema_version: Literal[1] = SCHEMA_VERSION
    as_of: str
    goal: GoalV1 | None
    availability: AvailabilityV1 | None
    subjective_context: SubjectiveContextV1
    athlete_state: AthleteContextSnapshot
    training_history_summary: TrainingHistoryContextSnapshot
    data_confidence: ConfidenceContextSnapshot
    integration: IntegrationAssessment


def _issue(code: str, source: str, *, blocking: bool = False) -> ContextIssue:
    return ContextIssue(code=code, source=source, blocking=blocking)


def _unique_issues(issues: list[ContextIssue]) -> list[ContextIssue]:
    unique: dict[tuple[str, str, bool], ContextIssue] = {}
    for issue in issues:
        unique[(issue.code, issue.source, issue.blocking)] = issue
    return sorted(
        unique.values(), key=lambda item: (item.source, item.code, item.blocking)
    )


def _cross_contract_diagnostics(
    summary: TrainingHistorySummary,
    confidence: DataConfidence,
    athlete: AthleteState,
    reference: Date,
) -> list[ContextIssue]:
    diagnostics: list[ContextIssue] = []
    expected_as_of = reference.isoformat()
    if {summary.as_of, confidence.as_of, athlete.as_of} != {expected_as_of}:
        diagnostics.append(
            _issue("source_reference_date_mismatch", "integration", blocking=True)
        )
    if (
        athlete.confidence.training_history
        != confidence.training_history_confidence.confidence
        or athlete.confidence.current_state
        != confidence.current_state_confidence.confidence
    ):
        diagnostics.append(
            _issue("athlete_state_confidence_mismatch", "integration", blocking=True)
        )
    recent = summary.windows["last_28_days"]
    if (
        not math.isclose(
            athlete.current_training.observed_sessions_per_week_last_28_days,
            recent.average_weekly_frequency,
            abs_tol=0.001,
        )
        or athlete.running_state.sessions_last_28_days
        != summary.running.sessions_last_28_days
    ):
        diagnostics.append(
            _issue("athlete_state_summary_mismatch", "integration", blocking=True)
        )
    diagnostics.extend(
        _issue(item.code, f"data_confidence.{item.component}")
        for item in confidence.warnings
    )
    diagnostics.extend(
        _issue(item.code, f"data_confidence.{item.component}")
        for item in confidence.stale_data
    )
    return _unique_issues(diagnostics)


def _limitations(
    athlete: AthleteState,
    confidence: DataConfidence,
    subjective: SubjectiveContextV1,
) -> list[ContextIssue]:
    limitations = [
        _issue(item.code, "athlete_state") for item in athlete.limitations
    ]
    existing_codes = {item.code for item in limitations}
    for item in confidence.missing_data:
        if item.component == "goal_context" or item.code in existing_codes:
            continue
        limitations.append(_issue(item.code, f"data_confidence.{item.component}"))
        existing_codes.add(item.code)
    pain = subjective.pain_or_injury
    if pain.status == "explicit_yes":
        limitations.append(
            _issue("pain_or_injury_affecting_training", "subjective_context")
        )
    elif pain.status == "unknown":
        limitations.append(
            _issue("pain_or_injury_unknown", "subjective_context")
        )
    return _unique_issues(limitations)


def build_motionest_context_v1(
    *,
    goal: GoalV1 | None,
    availability: AvailabilityV1 | None,
    subjective_context: SubjectiveContextV1,
    training_history_summary: TrainingHistorySummary,
    data_confidence: DataConfidence,
    athlete_state: AthleteState,
    reference_date: Date,
    diagnostics: dict[str, Any] | None = None,
) -> MotionestContextV1:
    """Assemble compact existing facts; perform no coaching inference."""
    measured = diagnostics if diagnostics is not None else {}
    started = time.perf_counter()
    blockers: list[ContextIssue] = []
    if goal is None:
        blockers.append(_issue("goal_absent", "goal", blocking=True))
    if availability is None:
        blockers.append(
            _issue("availability_absent", "availability", blocking=True)
        )
    if athlete_state.status == "insufficient_data":
        blockers.append(
            _issue("athlete_state_insufficient", "athlete_state", blocking=True)
        )
    cross_diagnostics = _cross_contract_diagnostics(
        training_history_summary, data_confidence, athlete_state, reference_date
    )
    blockers.extend(item for item in cross_diagnostics if item.blocking)
    blockers = _unique_issues(blockers)
    nonblocking_diagnostics = [
        item for item in cross_diagnostics if not item.blocking
    ]
    limitations = _limitations(
        athlete_state, data_confidence, subjective_context
    )

    pain = subjective_context.pain_or_injury
    if blockers:
        status: IntegrationStatus = "insufficient"
        reasons = [item.code for item in blockers]
    elif athlete_state.status == "partial" or pain.status == "unknown":
        status = "partial"
        reasons = []
        if athlete_state.status == "partial":
            reasons.append("athlete_state_partial")
        if pain.status == "unknown":
            reasons.append("pain_or_injury_unknown")
    elif limitations:
        status = "ready_with_limitations"
        reasons = ["non_blocking_limitations_present"]
    else:
        status = "ready"
        reasons = ["all_required_context_available"]

    recent = training_history_summary.windows["last_28_days"]
    base = training_history_summary.windows["last_84_days"]
    observed_frequency = (
        athlete_state.current_training.observed_sessions_per_week_last_28_days
    )
    preferred = (
        availability.preferred_sessions_per_week if availability else None
    )
    frequency_difference = (
        round(preferred - observed_frequency, 2)
        if preferred is not None
        else None
    )
    change_directions = {
        "activity_count": athlete_state.recent_changes.activity_count.direction,
        "total_duration": athlete_state.recent_changes.total_duration_seconds.direction,
        "running_distance": athlete_state.recent_changes.running_distance_km.direction,
        **{
            f"sport_duration.{sport}": change.direction
            for sport, change in sorted(
                athlete_state.recent_changes.sport_duration_seconds.items()
            )
        },
    }
    confidence = data_confidence.components
    result = MotionestContextV1(
        as_of=reference_date.isoformat(),
        goal=goal,
        availability=availability,
        subjective_context=subjective_context,
        athlete_state=AthleteContextSnapshot(
            status=athlete_state.status,
            sports_observed=athlete_state.sport_profile.sports_observed,
            last_activity_date=athlete_state.current_training.last_activity_date,
            recent_activity_count=recent.activity_count,
            observed_sessions_per_week_last_28_days=observed_frequency,
            running_sessions_last_28_days=athlete_state.running_state.sessions_last_28_days,
            running_distance_last_28_days_km=athlete_state.running_state.distance_last_28_days_km,
            capability_status=athlete_state.current_capability.status,
            capability_references=athlete_state.current_capability.reference_performances,
            recent_change_directions=change_directions,
        ),
        training_history_summary=TrainingHistoryContextSnapshot(
            status=training_history_summary.coverage.status,
            source=training_history_summary.source,
            scope=training_history_summary.scope,
            activity_count=training_history_summary.coverage.activity_count,
            covered_days=training_history_summary.coverage.covered_days,
            activity_count_last_28_days=recent.activity_count,
            activity_count_last_84_days=base.activity_count,
            active_days_last_28_days=recent.active_days,
            last_activity_date=training_history_summary.coverage.last_activity_date,
            recorded_interruptions_count=training_history_summary.interruptions.total_detected,
        ),
        data_confidence=ConfidenceContextSnapshot(
            training_history=data_confidence.training_history_confidence.confidence,
            current_state=data_confidence.current_state_confidence.confidence,
            sport_classification=confidence.sport_classification.confidence,
            heart_rate=confidence.heart_rate.confidence,
            heart_rate_zones=confidence.heart_rate_zones.confidence,
            training_effect=confidence.training_effect.confidence,
            training_load=confidence.training_load.confidence,
            performance_evidence=confidence.performance_evidence.confidence,
            recovery=confidence.recovery.confidence,
            missing_data_codes=sorted(
                {
                    item.code
                    for item in data_confidence.missing_data
                    if item.component != "goal_context"
                }
            ),
            stale_data_codes=sorted(
                {item.code for item in data_confidence.stale_data}
            ),
            warning_codes=sorted(
                {item.code for item in data_confidence.warnings}
            ),
        ),
        integration=IntegrationAssessment(
            status=status,
            status_reasons=sorted(reasons),
            blocking_issues=blockers,
            limitations=limitations,
            diagnostics=nonblocking_diagnostics,
            safety_context=SafetyContext(
                pain_status=pain.status,
                pain_or_injury_affecting_training=pain.affecting_training,
                pain_description_present=pain.description is not None,
                unrecorded_activities_count=len(
                    subjective_context.unrecorded_activities
                ),
            ),
            frequency_context=FrequencyContext(
                available_day_count=(
                    len(availability.available_days) if availability else None
                ),
                preferred_sessions_per_week=preferred,
                observed_sessions_per_week_last_28_days=observed_frequency,
                preferred_minus_observed_sessions=frequency_difference,
            ),
        ),
    )
    measured["computation_seconds"] = round(time.perf_counter() - started, 6)
    return result


def motionest_context_for_ai(context: MotionestContextV1) -> dict[str, Any]:
    """Return the already compact and identity-free integrated context."""
    return context.model_dump(mode="json")
