"""Explainable V1 confidence assessment built from TrainingHistorySummary."""
from __future__ import annotations

import math
import time
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from .training_history_summary import (
    MetricCoverage,
    TrainingHistorySummary,
    SUFFICIENT_METRIC_COVERAGE,
)


SCHEMA_VERSION = 1
RULE_SET_VERSION = 1
MEDIUM_METRIC_COVERAGE = 0.5
RECENT_HIGH_MAX_AGE_DAYS = 14
RECENT_STALE_AFTER_DAYS = 28

ConfidenceLevel = Literal["high", "medium", "low", "none", "not_applicable"]
ComponentStatus = Literal[
    "available",
    "partial",
    "unavailable",
    "insufficient_data",
    "stale",
    "not_applicable",
]


class ComponentConfidence(BaseModel):
    confidence: ConfidenceLevel
    status: ComponentStatus
    evidence: dict[str, Any]
    reasons: list[str]


class PrimaryConfidence(BaseModel):
    confidence: ConfidenceLevel
    meaning: str
    evidence: dict[str, Any]
    reasons: list[str]
    limitation: str | None = None


class ConfidenceComponents(BaseModel):
    history_coverage: ComponentConfidence
    recent_activity: ComponentConfidence
    sport_classification: ComponentConfidence
    heart_rate: ComponentConfidence
    heart_rate_zones: ComponentConfidence
    training_effect: ComponentConfidence
    training_load: ComponentConfidence
    performance_evidence: ComponentConfidence
    recovery: ComponentConfidence
    goal_context: ComponentConfidence


class DataIssue(BaseModel):
    code: str
    component: str
    affected_activities: int | None = Field(default=None, ge=0)
    details: dict[str, Any] = Field(default_factory=dict)


class DataConfidence(BaseModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    rule_set_version: Literal[1] = RULE_SET_VERSION
    as_of: str
    training_history_confidence: PrimaryConfidence
    current_state_confidence: PrimaryConfidence
    components: ConfidenceComponents
    missing_data: list[DataIssue]
    stale_data: list[DataIssue]
    warnings: list[DataIssue]


def _coverage_level(coverage: MetricCoverage) -> ConfidenceLevel:
    ratio = coverage.ratio
    if coverage.status == "not_applicable":
        return "not_applicable"
    if coverage.available_count == 0 or ratio is None:
        return "none"
    if ratio >= SUFFICIENT_METRIC_COVERAGE:
        return "high"
    if ratio >= MEDIUM_METRIC_COVERAGE:
        return "medium"
    return "low"


def _coverage_component(
    coverage: MetricCoverage,
    *,
    reason_prefix: str,
) -> ComponentConfidence:
    level = _coverage_level(coverage)
    status: ComponentStatus
    if level == "not_applicable":
        status = "not_applicable"
    elif level == "none":
        status = "unavailable"
    elif level == "high":
        status = "available"
    else:
        status = "partial"
    return ComponentConfidence(
        confidence=level,
        status=status,
        evidence={
            "eligible_count": coverage.eligible_count,
            "covered_count": coverage.available_count,
            "coverage_ratio": coverage.ratio,
        },
        reasons=[f"{reason_prefix}_{level}"],
    )


def _history_coverage(summary: TrainingHistorySummary) -> ComponentConfidence:
    coverage = summary.coverage
    recent = summary.windows["last_84_days"]
    evidence = {
        "scope": summary.scope,
        "first_activity_date": coverage.first_activity_date,
        "last_activity_date": coverage.last_activity_date,
        "covered_days": coverage.covered_days,
        "activity_count": coverage.activity_count,
        "active_days": coverage.active_days,
        "active_weeks": coverage.active_weeks,
        "weeks_without_recorded_activity": coverage.weeks_without_recorded_activity,
        "last_84_days_activity_count": recent.activity_count,
        "last_84_days_active_days": recent.active_days,
    }
    if coverage.activity_count == 0:
        return ComponentConfidence(
            confidence="none",
            status="unavailable",
            evidence=evidence,
            reasons=["history_no_activities"],
        )
    if (
        (coverage.covered_days or 0) >= 56
        and coverage.activity_count >= 12
        and coverage.active_weeks >= 8
    ):
        level: ConfidenceLevel = "high"
    elif (
        (coverage.covered_days or 0) >= 28
        and coverage.activity_count >= 4
        and coverage.active_weeks >= 4
    ):
        level = "medium"
    else:
        level = "low"
    return ComponentConfidence(
        confidence=level,
        status="available" if level == "high" else "partial",
        evidence=evidence,
        reasons=[f"history_coverage_{level}"],
    )


def _recent_activity(
    summary: TrainingHistorySummary, reference: date
) -> tuple[ComponentConfidence, int | None]:
    coverage = summary.coverage
    last_day = (
        date.fromisoformat(coverage.last_activity_date)
        if coverage.last_activity_date
        else None
    )
    age = (reference - last_day).days if last_day else None
    recent = summary.windows["last_28_days"]
    base = summary.windows["last_84_days"]
    evidence = {
        "last_activity_date": coverage.last_activity_date,
        "last_activity_age_days": age,
        "last_28_days_activity_count": recent.activity_count,
        "last_28_days_active_days": recent.active_days,
        "last_84_days_activity_count": base.activity_count,
        "last_84_days_active_days": base.active_days,
    }
    if coverage.activity_count == 0:
        return (
            ComponentConfidence(
                confidence="none",
                status="unavailable",
                evidence=evidence,
                reasons=["recent_activity_unavailable"],
            ),
            age,
        )
    if age is not None and age < 0:
        return (
            ComponentConfidence(
                confidence="low",
                status="insufficient_data",
                evidence=evidence,
                reasons=["last_activity_after_reference_date"],
            ),
            age,
        )
    if (
        age is not None
        and age <= RECENT_HIGH_MAX_AGE_DAYS
        and recent.activity_count >= 4
        and recent.active_days >= 3
        and base.activity_count >= 8
    ):
        level: ConfidenceLevel = "high"
        status: ComponentStatus = "available"
    elif (
        age is not None
        and age <= RECENT_STALE_AFTER_DAYS
        and recent.activity_count >= 1
        and base.activity_count >= 4
    ):
        level = "medium"
        status = "partial"
    else:
        level = "low"
        status = "stale" if age is not None and age > RECENT_STALE_AFTER_DAYS else "insufficient_data"
    return (
        ComponentConfidence(
            confidence=level,
            status=status,
            evidence=evidence,
            reasons=[f"recent_activity_{level}"],
        ),
        age,
    )


def _sport_classification(summary: TrainingHistorySummary) -> ComponentConfidence:
    data = summary.sport_classification
    explicit_or_structured = data.garmin_explicit_count + data.structured_mapping_count
    strong_ratio = (
        explicit_or_structured / data.total_activities
        if data.total_activities
        else None
    )
    evidence = {
        "total_activities": data.total_activities,
        "garmin_explicit_count": data.garmin_explicit_count,
        "structured_mapping_count": data.structured_mapping_count,
        "controlled_name_rule_count": data.controlled_name_rule_count,
        "other_count": data.other_count,
        "unknown_count": data.unknown_count,
        "classified_count": data.classified_count,
        "classification_ratio": data.classification_ratio,
        "explicit_or_structured_ratio": round(strong_ratio, 4) if strong_ratio is not None else None,
    }
    if data.total_activities == 0:
        level: ConfidenceLevel = "not_applicable"
        status: ComponentStatus = "not_applicable"
    elif (
        (data.classification_ratio or 0) >= 0.9
        and (strong_ratio or 0) >= SUFFICIENT_METRIC_COVERAGE
    ):
        level, status = "high", "available"
    elif (data.classification_ratio or 0) >= 0.7:
        level, status = "medium", "partial"
    elif data.classified_count:
        level, status = "low", "partial"
    else:
        level, status = "none", "unavailable"
    return ComponentConfidence(
        confidence=level,
        status=status,
        evidence=evidence,
        reasons=[f"sport_classification_{level}"],
    )


def _heart_rate(summary: TrainingHistorySummary) -> ComponentConfidence:
    coverage = summary.windows["last_84_days"].metric_coverage
    any_hr = coverage["heart_rate_any"]
    average = coverage["heart_rate_average"]
    maximum = coverage["heart_rate_maximum"]
    component = _coverage_component(any_hr, reason_prefix="heart_rate")
    component.evidence.update(
        {
            "average_covered_count": average.available_count,
            "average_coverage_ratio": average.ratio,
            "maximum_covered_count": maximum.available_count,
            "maximum_coverage_ratio": maximum.ratio,
        }
    )
    return component


def _training_effect(summary: TrainingHistorySummary) -> ComponentConfidence:
    intensity = summary.running.intensity_distribution
    aerobic = intensity.aerobic_training_effect_coverage
    anaerobic = intensity.anaerobic_training_effect_coverage
    component = _coverage_component(aerobic, reason_prefix="aerobic_training_effect")
    component.evidence.update(
        {
            "aerobic_eligible_count": aerobic.eligible_count,
            "aerobic_covered_count": aerobic.available_count,
            "aerobic_coverage_ratio": aerobic.ratio,
            "anaerobic_eligible_count": anaerobic.eligible_count,
            "anaerobic_covered_count": anaerobic.available_count,
            "anaerobic_coverage_ratio": anaerobic.ratio,
        }
    )
    return component


def _performance(summary: TrainingHistorySummary, reference: date) -> ComponentConfidence:
    evidence_items = summary.running.performance_evidence
    distances = sorted({item.distance_m for item in evidence_items})
    provenances = sorted({item.provenance for item in evidence_items})
    latest = max((date.fromisoformat(item.date) for item in evidence_items), default=None)
    age = (reference - latest).days if latest else None
    high_quality = sum(item.technical_confidence == "high" for item in evidence_items)
    evidence = {
        "evidence_count": len(evidence_items),
        "high_technical_confidence_count": high_quality,
        "evidence_types": sorted({item.type for item in evidence_items}),
        "distances_m": distances,
        "provenances": provenances,
        "latest_evidence_date": latest.isoformat() if latest else None,
        "latest_evidence_age_days": age,
    }
    if not evidence_items:
        level: ConfidenceLevel = "none"
        status: ComponentStatus = "unavailable"
    elif (
        len(evidence_items) >= 3
        and len(distances) >= 2
        and high_quality == len(evidence_items)
        and age is not None
        and age <= 180
    ):
        level, status = "high", "available"
    elif high_quality and age is not None and age <= 365:
        level, status = "medium", "partial"
    else:
        level, status = "low", "partial"
    return ComponentConfidence(
        confidence=level,
        status=status,
        evidence=evidence,
        reasons=[f"performance_evidence_{level}"],
    )


def _goal_context(
    objective: dict[str, Any] | None, reference: date
) -> tuple[ComponentConfidence, list[DataIssue], list[DataIssue]]:
    missing: list[DataIssue] = []
    stale: list[DataIssue] = []
    if not isinstance(objective, dict):
        return (
            ComponentConfidence(
                confidence="none",
                status="unavailable",
                evidence={"objective_available": False},
                reasons=["goal_context_unavailable"],
            ),
            [DataIssue(code="goal_context_unavailable", component="goal_context")],
            stale,
        )
    goal_type = str(objective.get("goalType") or "other").strip().lower()
    title = str(objective.get("title") or "").strip()
    has_title = bool(title and title.lower() not in {"objectif à définir", "objectif a definir"})
    distance = objective.get("distanceKm")
    has_distance = isinstance(distance, (int, float)) and distance > 0
    race_date_raw = str(objective.get("raceDate") or "").strip()
    try:
        race_date = date.fromisoformat(race_date_raw) if race_date_raw else None
    except ValueError:
        race_date = None
    target_time = str(objective.get("goal_target_time") or "").strip() or None
    evidence = {
        "objective_available": True,
        "goal_type": goal_type,
        "title_available": has_title,
        "race_distance_available": has_distance,
        "race_date_available": race_date is not None,
        "target_time_available": target_time is not None,
    }
    if not has_title:
        level: ConfidenceLevel = "none"
        status: ComponentStatus = "unavailable"
        missing.append(DataIssue(code="goal_title_unavailable", component="goal_context"))
    elif goal_type == "race":
        if not has_distance:
            missing.append(DataIssue(code="race_distance_unavailable", component="goal_context"))
        if race_date is None:
            missing.append(DataIssue(code="race_date_unavailable", component="goal_context"))
        if has_distance and race_date is not None:
            level, status = "high", "available"
        elif has_distance:
            level, status = "medium", "partial"
        else:
            level, status = "low", "insufficient_data"
    elif goal_type in {"performance", "comeback"}:
        level, status = "high", "available"
    else:
        level, status = "medium", "partial"
    if race_date is not None and race_date < reference:
        stale.append(
            DataIssue(
                code="goal_race_date_in_past",
                component="goal_context",
                details={"race_date": race_date.isoformat()},
            )
        )
        level, status = "low", "stale"
    return (
        ComponentConfidence(
            confidence=level,
            status=status,
            evidence=evidence,
            reasons=[f"goal_context_{level}"],
        ),
        missing,
        stale,
    )


def _warning_checks(summary: TrainingHistorySummary, reference: date) -> list[DataIssue]:
    warnings: list[DataIssue] = []
    coverages: list[tuple[str, MetricCoverage]] = []
    for window_name, window in summary.windows.items():
        coverages.extend(
            (f"{window_name}.{name}", coverage)
            for name, coverage in window.metric_coverage.items()
        )
        coverages.append((f"{window_name}.training_load", window.training_load.coverage))
    intensity = summary.running.intensity_distribution
    coverages.extend(
        [
            ("heart_rate_zones", intensity.heart_rate_zone_coverage),
            ("aerobic_training_effect", intensity.aerobic_training_effect_coverage),
            ("anaerobic_training_effect", intensity.anaerobic_training_effect_coverage),
        ]
    )
    for name, coverage in coverages:
        expected = (
            coverage.available_count / coverage.eligible_count
            if coverage.eligible_count
            else None
        )
        if coverage.available_count > coverage.eligible_count or (
            expected is not None
            and coverage.ratio is not None
            and not math.isclose(expected, coverage.ratio, abs_tol=0.0001)
        ):
            warnings.append(
                DataIssue(
                    code="coverage_inconsistent",
                    component=name,
                    details={
                        "eligible_count": coverage.eligible_count,
                        "covered_count": coverage.available_count,
                        "coverage_ratio": coverage.ratio,
                    },
                )
            )
    classification = summary.sport_classification
    source_total = (
        classification.garmin_explicit_count
        + classification.structured_mapping_count
        + classification.controlled_name_rule_count
        + classification.other_count
        + classification.unknown_count
    )
    if source_total != classification.total_activities:
        warnings.append(
            DataIssue(
                code="sport_classification_counts_inconsistent",
                component="sport_classification",
                details={"reported_total": classification.total_activities, "counted_total": source_total},
            )
        )
    if summary.coverage.last_activity_date:
        last_day = date.fromisoformat(summary.coverage.last_activity_date)
        if last_day > reference:
            warnings.append(
                DataIssue(
                    code="activity_after_reference_date",
                    component="recent_activity",
                    details={"last_activity_date": last_day.isoformat()},
                )
            )
    return warnings


def _primary_confidences(
    history: ComponentConfidence,
    recent: ComponentConfidence,
    classification: ComponentConfidence,
) -> tuple[PrimaryConfidence, PrimaryConfidence]:
    if history.confidence == "none":
        training_level: ConfidenceLevel = "none"
    elif history.confidence == "high" and classification.confidence in {"high", "medium"}:
        training_level = "high"
    elif history.confidence in {"high", "medium"} and classification.confidence != "none":
        training_level = "medium"
    else:
        training_level = "low"
    if recent.confidence == "none":
        current_level: ConfidenceLevel = "none"
    elif recent.confidence == "high" and history.confidence in {"high", "medium"}:
        current_level = "high"
    elif recent.confidence in {"high", "medium"} and history.confidence != "none":
        current_level = "medium"
    else:
        current_level = "low"
    return (
        PrimaryConfidence(
            confidence=training_level,
            meaning="Capacité à décrire l'historique enregistré et les habitudes sportives.",
            evidence={
                "history_coverage_confidence": history.confidence,
                "sport_classification_confidence": classification.confidence,
            },
            reasons=[f"training_history_{training_level}"],
        ),
        PrimaryConfidence(
            confidence=current_level,
            meaning="Capacité à décrire l'activité sportive récente à partir des entraînements enregistrés.",
            evidence={
                "recent_activity_confidence": recent.confidence,
                "history_coverage_confidence": history.confidence,
            },
            reasons=[f"current_state_{current_level}"],
            limitation=(
                "Cette confiance ne couvre pas la récupération physiologique : les sources Health/Recovery sont absentes."
            ),
        ),
    )


def build_data_confidence(
    summary: TrainingHistorySummary,
    *,
    objective: dict[str, Any] | None = None,
    reference_date: date | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> DataConfidence:
    """Assess data quality from the compact summary only; no files or network."""
    measured = diagnostics if diagnostics is not None else {}
    started = time.perf_counter()
    reference = reference_date or date.fromisoformat(summary.as_of)
    history = _history_coverage(summary)
    recent, activity_age = _recent_activity(summary, reference)
    classification = _sport_classification(summary)
    heart_rate = _heart_rate(summary)
    zones = _coverage_component(
        summary.running.intensity_distribution.heart_rate_zone_coverage,
        reason_prefix="heart_rate_zones",
    )
    training_effect = _training_effect(summary)
    training_load = _coverage_component(
        summary.windows["historical"].training_load.coverage,
        reason_prefix="training_load",
    )
    performance = _performance(summary, reference)
    recovery_sources = [
        "sleep",
        "hrv",
        "stress",
        "body_battery",
        "recovery_time",
        "training_readiness",
    ]
    recovery = ComponentConfidence(
        confidence="none",
        status="unavailable",
        evidence={"missing_sources": recovery_sources},
        reasons=["recovery_sources_not_imported"],
    )
    goal, goal_missing, goal_stale = _goal_context(objective, reference)
    missing: list[DataIssue] = list(goal_missing)
    stale: list[DataIssue] = list(goal_stale)
    if history.confidence == "none":
        missing.append(DataIssue(code="training_history_unavailable", component="history_coverage"))
    if recent.confidence in {"none", "low"}:
        missing.append(DataIssue(code="recent_activity_insufficient", component="recent_activity"))
    unclassified = summary.sport_classification.other_count + summary.sport_classification.unknown_count
    if unclassified:
        missing.append(
            DataIssue(
                code="sport_classification_partial",
                component="sport_classification",
                affected_activities=unclassified,
            )
        )
    for component_name, component, code in (
        ("heart_rate", heart_rate, "heart_rate_unavailable"),
        ("heart_rate_zones", zones, "heart_rate_zones_unavailable"),
        ("training_effect", training_effect, "training_effect_unavailable"),
        ("training_load", training_load, "training_load_unavailable"),
        ("performance_evidence", performance, "performance_evidence_unavailable"),
    ):
        if component.confidence == "none":
            missing.append(DataIssue(code=code, component=component_name))
        elif component.confidence in {"low", "medium"}:
            missing.append(
                DataIssue(
                    code=f"{component_name}_partial",
                    component=component_name,
                    affected_activities=(
                        component.evidence.get("eligible_count", 0)
                        - component.evidence.get("covered_count", 0)
                        if "eligible_count" in component.evidence
                        else None
                    ),
                )
            )
    anaerobic = summary.running.intensity_distribution.anaerobic_training_effect_coverage
    if anaerobic.available_count == 0 and anaerobic.eligible_count:
        missing.append(
            DataIssue(
                code="anaerobic_training_effect_unavailable",
                component="training_effect",
            )
        )
    missing.append(
        DataIssue(
            code="recovery_sources_unavailable",
            component="recovery",
            details={"missing_sources": recovery_sources},
        )
    )
    if activity_age is not None and activity_age > RECENT_STALE_AFTER_DAYS:
        stale.append(
            DataIssue(
                code="last_activity_stale",
                component="recent_activity",
                details={
                    "age_days": activity_age,
                    "stale_after_days": RECENT_STALE_AFTER_DAYS,
                },
            )
        )
    warnings = _warning_checks(summary, reference)
    training_primary, current_primary = _primary_confidences(
        history, recent, classification
    )
    result = DataConfidence(
        as_of=reference.isoformat(),
        training_history_confidence=training_primary,
        current_state_confidence=current_primary,
        components=ConfidenceComponents(
            history_coverage=history,
            recent_activity=recent,
            sport_classification=classification,
            heart_rate=heart_rate,
            heart_rate_zones=zones,
            training_effect=training_effect,
            training_load=training_load,
            performance_evidence=performance,
            recovery=recovery,
            goal_context=goal,
        ),
        missing_data=sorted(missing, key=lambda item: (item.component, item.code)),
        stale_data=sorted(stale, key=lambda item: (item.component, item.code)),
        warnings=sorted(warnings, key=lambda item: (item.component, item.code)),
    )
    measured["computation_seconds"] = round(time.perf_counter() - started, 6)
    return result


def confidence_for_ai(confidence: DataConfidence) -> dict[str, Any]:
    """Return the compact, private-data-free V1 contract for future use."""
    return confidence.model_dump(mode="json")
