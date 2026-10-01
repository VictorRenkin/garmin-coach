"""Compact factual TrainingPatternEvidence V1 from existing local evidence."""
from __future__ import annotations

import math
import time
from datetime import date, datetime, timedelta
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .data_confidence import ConfidenceLevel, DataConfidence
from .local_activity_history import (
    load_local_activity_details,
    load_local_activity_history,
)
from .training_history_summary import TrainingHistorySummary, normalize_sport


SCHEMA_VERSION = 1
RULE_SET_VERSION = 1
RECENT_DAYS = 28
BASE_DAYS = 84
MIN_PERSONAL_DISTANCE_SAMPLES = 4
MAX_PAST_RACE_EVIDENCE = 5
SUFFICIENT_SIGNAL_COVERAGE = 0.8
MEDIUM_SIGNAL_COVERAGE = 0.5

ACTIVE_SPLIT = "INTERVAL_ACTIVE"
RECOVERY_SPLITS = frozenset({"INTERVAL_RECOVERY", "INTERVAL_REST"})
EXPLICIT_RACE_EVENT_TYPES = frozenset({"race", "competition"})
UNINFORMATIVE_EVENT_TYPES = frozenset({"uncategorized", "unknown", "other"})

PatternStatus = Literal[
    "observed", "not_observed", "partial", "insufficient_data", "not_applicable"
]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TrainingPatternSessionInput(StrictContract):
    """Identity-free session facts used only while constructing the aggregate."""

    date: date
    distance_m: float | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    typed_split_types: tuple[str, ...] = ()
    typed_splits_inspected: bool = False
    event_type: str | None = None

    @field_validator("typed_split_types")
    @classmethod
    def normalize_split_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(str(item).strip().upper() for item in value if str(item).strip())

    @field_validator("event_type")
    @classmethod
    def normalize_event_type(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip().lower()
        return normalized or None


class RunningPatternWindow(StrictContract):
    sessions_last_28_days: int = Field(ge=0)
    sessions_last_84_days: int = Field(ge=0)


class EventTypeCoverage(StrictContract):
    inspected_sessions: int = Field(ge=0)
    informative_sessions: int = Field(ge=0)
    explicit_race_sessions: int = Field(ge=0)


class PatternCoverage(StrictContract):
    eligible_sessions: int = Field(ge=0)
    sessions_with_observed_pattern: int = Field(ge=0)
    sessions_without_observed_pattern: int = Field(ge=0)
    coverage_ratio: float | None = Field(default=None, ge=0, le=1)
    typed_splits_inspected_sessions: int = Field(ge=0)
    distance_observed_sessions: int = Field(ge=0)
    event_type_coverage: EventTypeCoverage


class PatternEvidenceSummary(StrictContract):
    status: PatternStatus
    count_28_days: int = Field(ge=0)
    count_84_days: int = Field(ge=0)
    provenance: list[str]
    confidence: ConfidenceLevel


class SessionPatterns(StrictContract):
    structured_intervals: PatternEvidenceSummary
    relative_long_run: PatternEvidenceSummary
    race: PatternEvidenceSummary


class MetricCoverageEvidence(StrictContract):
    available_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    ratio: float | None = Field(default=None, ge=0, le=1)
    status: Literal["available", "partial", "unavailable", "not_applicable"]


class IntensityEvidence(StrictContract):
    heart_rate_zone_coverage: MetricCoverageEvidence
    heart_rate_zone_seconds: dict[str, float]
    aerobic_training_effect_coverage: MetricCoverageEvidence
    aerobic_training_effect_counts: dict[str, int]
    anaerobic_training_effect_coverage: MetricCoverageEvidence


class PastRaceEvidence(StrictContract):
    event_distance_km: float = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    date: date
    source: Literal["garmin_event_type"] = "garmin_event_type"
    confidence: Literal["high", "medium"]
    identification: Literal["explicit", "strongly_structured"]


class TrainingPatternConfidence(StrictContract):
    training_history: ConfidenceLevel
    typed_splits: ConfidenceLevel
    personal_distance_distribution: ConfidenceLevel
    race_evidence: ConfidenceLevel


class TrainingPatternEvidenceV1(StrictContract):
    schema_version: Literal[1] = SCHEMA_VERSION
    rule_set_version: Literal[1] = RULE_SET_VERSION
    as_of: date
    running: RunningPatternWindow
    pattern_coverage: PatternCoverage
    session_patterns: SessionPatterns
    intensity_evidence: IntensityEvidence
    past_race_evidence: list[PastRaceEvidence] = Field(
        default_factory=list, max_length=MAX_PAST_RACE_EVIDENCE
    )
    limitations: list[str]
    confidence: TrainingPatternConfidence


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _activity_date(activity: dict[str, Any]) -> date | None:
    raw = (
        activity.get("startTimeLocal")
        or activity.get("startTimeGMT")
        or activity.get("date")
    )
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _event_type(activity: dict[str, Any]) -> str | None:
    raw = activity.get("eventType")
    if not isinstance(raw, dict):
        return None
    value = str(raw.get("typeKey") or "").strip().lower()
    return value or None


def _typed_split_facts(details: dict[str, Any] | None) -> tuple[bool, tuple[str, ...]]:
    if not isinstance(details, dict):
        return False, ()
    endpoint = (details.get("endpoints") or {}).get("get_activity_typed_splits")
    if not isinstance(endpoint, dict):
        return False, ()
    status = endpoint.get("status")
    if status not in {"success", "empty"}:
        return False, ()
    data = endpoint.get("data")
    splits = data.get("splits") if isinstance(data, dict) else None
    types = tuple(
        str(item.get("type") or "").strip().upper()
        for item in splits if isinstance(item, dict)
    ) if isinstance(splits, list) else ()
    return True, tuple(item for item in types if item)


def collect_local_training_pattern_inputs(
    *, diagnostics: dict[str, Any] | None = None
) -> list[TrainingPatternSessionInput]:
    """Read existing local history/details and return only identity-free facts."""
    measured = diagnostics if diagnostics is not None else {}
    started = time.perf_counter()
    activities = load_local_activity_history()
    sessions: list[TrainingPatternSessionInput] = []
    details_opened = 0
    for activity in activities:
        if normalize_sport(activity) != "running":
            continue
        activity_day = _activity_date(activity)
        if activity_day is None:
            continue
        details = load_local_activity_details(str(activity.get("activityId") or ""))
        if details is not None:
            details_opened += 1
        inspected, split_types = _typed_split_facts(details)
        sessions.append(
            TrainingPatternSessionInput(
                date=activity_day,
                distance_m=_number(activity.get("distance")),
                duration_seconds=_number(
                    activity.get("movingDuration")
                    or activity.get("duration")
                    or activity.get("elapsedDuration")
                ),
                typed_split_types=split_types,
                typed_splits_inspected=inspected,
                event_type=_event_type(activity),
            )
        )
    measured.update(
        {
            "activity_records_read": len(activities),
            "running_records_collected": len(sessions),
            "detail_files_opened": details_opened,
            "collection_seconds": round(time.perf_counter() - started, 6),
        }
    )
    return sessions


def _signal_confidence(observed: int, eligible: int) -> ConfidenceLevel:
    if eligible == 0 or observed == 0:
        return "none"
    ratio = observed / eligible
    if ratio >= SUFFICIENT_SIGNAL_COVERAGE:
        return "high"
    if ratio >= MEDIUM_SIGNAL_COVERAGE:
        return "medium"
    return "low"


def _pattern_status(
    count: int, *, eligible: int, observed_signal: int
) -> PatternStatus:
    if eligible == 0:
        return "not_applicable"
    if count:
        return "observed"
    if observed_signal == eligible:
        return "not_observed"
    if observed_signal:
        return "partial"
    return "insufficient_data"


def _structured_intervals(types: Sequence[str]) -> bool:
    """Require explicit repeated Garmin active/recovery typed split structure."""
    relevant = [
        item for item in types if item == ACTIVE_SPLIT or item in RECOVERY_SPLITS
    ]
    active_count = sum(item == ACTIVE_SPLIT for item in relevant)
    recovery_count = sum(item in RECOVERY_SPLITS for item in relevant)
    transitions = sum(
        (left == ACTIVE_SPLIT) != (right == ACTIVE_SPLIT)
        for left, right in zip(relevant, relevant[1:])
    )
    return active_count >= 2 and recovery_count >= 1 and transitions >= 2


def _coverage(value: Any) -> MetricCoverageEvidence:
    return MetricCoverageEvidence(
        available_count=value.available_count,
        eligible_count=value.eligible_count,
        ratio=value.ratio,
        status=value.status,
    )


def build_training_pattern_evidence_v1(
    training_history_summary: TrainingHistorySummary,
    data_confidence: DataConfidence,
    sessions: Sequence[TrainingPatternSessionInput],
    *,
    reference_date: date,
    diagnostics: dict[str, Any] | None = None,
) -> TrainingPatternEvidenceV1:
    """Aggregate identity-free session facts without I/O or inferred intent."""
    measured = diagnostics if diagnostics is not None else {}
    started = time.perf_counter()
    expected = reference_date.isoformat()
    if training_history_summary.as_of != expected or data_confidence.as_of != expected:
        raise ValueError(
            "TrainingPatternEvidence exige des contrats alignés sur reference_date."
        )

    start_28 = reference_date - timedelta(days=RECENT_DAYS - 1)
    start_84 = reference_date - timedelta(days=BASE_DAYS - 1)
    in_84 = [item for item in sessions if start_84 <= item.date <= reference_date]
    in_28 = [item for item in in_84 if item.date >= start_28]
    typed_inspected = sum(item.typed_splits_inspected for item in in_84)
    distance_sessions = [item for item in in_84 if item.distance_m is not None]
    event_inspected = sum(item.event_type is not None for item in in_84)
    informative_event_types = sum(
        item.event_type is not None
        and item.event_type not in UNINFORMATIVE_EVENT_TYPES
        for item in in_84
    )

    interval_84 = [item for item in in_84 if _structured_intervals(item.typed_split_types)]
    interval_28 = [item for item in interval_84 if item.date >= start_28]

    p75 = training_history_summary.running.long_runs.distance_percentile_75_km
    distance_distribution_ready = (
        p75 is not None and len(distance_sessions) >= MIN_PERSONAL_DISTANCE_SAMPLES
    )
    relative_long_84 = (
        [
            item
            for item in distance_sessions
            if item.distance_m is not None and item.distance_m / 1000 >= p75
        ]
        if distance_distribution_ready and p75 is not None
        else []
    )
    relative_long_28 = [item for item in relative_long_84 if item.date >= start_28]

    explicit_races_all = [
        item
        for item in sessions
        if item.date <= reference_date
        and item.event_type in EXPLICIT_RACE_EVENT_TYPES
    ]
    explicit_races_84 = [item for item in explicit_races_all if item.date >= start_84]
    explicit_races_28 = [item for item in explicit_races_84 if item.date >= start_28]

    observed_pattern_indexes = {
        index
        for index, item in enumerate(in_84)
        if _structured_intervals(item.typed_split_types)
        or (
            distance_distribution_ready
            and item.distance_m is not None
            and p75 is not None
            and item.distance_m / 1000 >= p75
        )
        or item.event_type in EXPLICIT_RACE_EVENT_TYPES
    }
    eligible = len(in_84)
    sessions_with_pattern = len(observed_pattern_indexes)

    limitations: set[str] = set()
    if eligible == 0:
        limitations.add("running_history_unavailable")
    if typed_inspected == 0 and eligible:
        limitations.add("typed_splits_unavailable")
    elif typed_inspected < eligible:
        limitations.add("typed_splits_partial_coverage")
    if eligible and not distance_distribution_ready:
        limitations.add("personal_distance_distribution_insufficient")
    if event_inspected == 0 and eligible:
        limitations.add("event_type_unavailable")
    elif event_inspected < eligible:
        limitations.add("event_type_partial_coverage")
    if informative_event_types == 0 and eligible:
        limitations.add("informative_event_type_unavailable")

    past_races: list[PastRaceEvidence] = []
    missing_race_metrics = 0
    for item in sorted(explicit_races_all, key=lambda value: value.date, reverse=True):
        if item.distance_m is None or item.duration_seconds is None:
            missing_race_metrics += 1
            continue
        if len(past_races) < MAX_PAST_RACE_EVIDENCE:
            past_races.append(
                PastRaceEvidence(
                    event_distance_km=round(item.distance_m / 1000, 3),
                    duration_seconds=round(item.duration_seconds, 2),
                    date=item.date,
                    confidence="high",
                    identification="explicit",
                )
            )
    if missing_race_metrics:
        limitations.add("explicit_race_missing_metrics")
    if len(explicit_races_all) > MAX_PAST_RACE_EVIDENCE:
        limitations.add("past_race_evidence_truncated")
    if not past_races:
        limitations.add("past_race_evidence_unavailable")
        limitations.add("explicit_race_signal_unavailable")

    intensity = training_history_summary.running.intensity_distribution
    for code, coverage in (
        ("heart_rate_zones_unavailable", intensity.heart_rate_zone_coverage),
        (
            "aerobic_training_effect_unavailable",
            intensity.aerobic_training_effect_coverage,
        ),
        (
            "anaerobic_training_effect_unavailable",
            intensity.anaerobic_training_effect_coverage,
        ),
    ):
        if coverage.available_count == 0 and coverage.eligible_count:
            limitations.add(code)

    result = TrainingPatternEvidenceV1(
        as_of=reference_date,
        running=RunningPatternWindow(
            sessions_last_28_days=training_history_summary.running.sessions_last_28_days,
            sessions_last_84_days=training_history_summary.running.sessions_last_84_days,
        ),
        pattern_coverage=PatternCoverage(
            eligible_sessions=eligible,
            sessions_with_observed_pattern=sessions_with_pattern,
            sessions_without_observed_pattern=eligible - sessions_with_pattern,
            coverage_ratio=(
                round(sessions_with_pattern / eligible, 4) if eligible else None
            ),
            typed_splits_inspected_sessions=typed_inspected,
            distance_observed_sessions=len(distance_sessions),
            event_type_coverage=EventTypeCoverage(
                inspected_sessions=event_inspected,
                informative_sessions=informative_event_types,
                explicit_race_sessions=len(explicit_races_84),
            ),
        ),
        session_patterns=SessionPatterns(
            structured_intervals=PatternEvidenceSummary(
                status=_pattern_status(
                    len(interval_84), eligible=eligible, observed_signal=typed_inspected
                ),
                count_28_days=len(interval_28),
                count_84_days=len(interval_84),
                provenance=(
                    ["garmin_typed_splits"] if interval_84 else []
                ),
                confidence=("high" if interval_84 else _signal_confidence(typed_inspected, eligible)),
            ),
            relative_long_run=PatternEvidenceSummary(
                status=(
                    _pattern_status(
                        len(relative_long_84),
                        eligible=eligible,
                        observed_signal=eligible,
                    )
                    if eligible and distance_distribution_ready
                    else "insufficient_data"
                    if eligible
                    else "not_applicable"
                ),
                count_28_days=len(relative_long_28),
                count_84_days=len(relative_long_84),
                provenance=(
                    ["deterministic_personal_distance_distribution"]
                    if relative_long_84
                    else []
                ),
                confidence=("medium" if distance_distribution_ready else "none"),
            ),
            race=PatternEvidenceSummary(
                status=(
                    "observed"
                    if explicit_races_84
                    else "insufficient_data"
                    if eligible
                    else "not_applicable"
                ),
                count_28_days=len(explicit_races_28),
                count_84_days=len(explicit_races_84),
                provenance=["garmin_explicit_race"] if explicit_races_all else [],
                confidence=(
                    "high"
                    if explicit_races_all
                    else "none"
                ),
            ),
        ),
        intensity_evidence=IntensityEvidence(
            heart_rate_zone_coverage=_coverage(intensity.heart_rate_zone_coverage),
            heart_rate_zone_seconds=intensity.heart_rate_zone_seconds,
            aerobic_training_effect_coverage=_coverage(
                intensity.aerobic_training_effect_coverage
            ),
            aerobic_training_effect_counts=intensity.aerobic_training_effect_counts,
            anaerobic_training_effect_coverage=_coverage(
                intensity.anaerobic_training_effect_coverage
            ),
        ),
        past_race_evidence=past_races,
        limitations=sorted(limitations),
        confidence=TrainingPatternConfidence(
            training_history=data_confidence.training_history_confidence.confidence,
            typed_splits=_signal_confidence(typed_inspected, eligible),
            personal_distance_distribution=(
                "medium"
                if distance_distribution_ready
                else "low" if distance_sessions else "none"
            ),
            race_evidence="high" if explicit_races_all else "none",
        ),
    )
    measured.update(
        {
            "eligible_sessions": eligible,
            "session_facts_processed": len(sessions),
            "computation_seconds": round(time.perf_counter() - started, 6),
        }
    )
    return result


def training_pattern_for_ai(pattern: TrainingPatternEvidenceV1) -> dict[str, Any]:
    """Return compact, identity-free facts without invoking an AI."""
    return pattern.model_dump(mode="json")
