"""Deterministic, versioned Goal V1 contract and legacy adapter."""
from __future__ import annotations

import math
import re
from datetime import date as Date
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = 1
STANDARD_DISTANCES_KM = {
    "5k": 5.0,
    "10k": 10.0,
    "half_marathon": 21.0975,
    "marathon": 42.195,
}
LEGACY_DISTANCE_TOLERANCE_KM = 0.01

GoalIntent = Literal["progress", "race", "beginner", "comeback"]
RaceEventType = Literal[
    "5k", "10k", "half_marathon", "marathon", "trail", "custom"
]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TargetTime(StrictContract):
    canonical: str
    seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_consistency(self) -> "TargetTime":
        canonical, seconds = parse_target_time(self.canonical)
        if self.canonical != canonical or self.seconds != seconds:
            raise ValueError("Le chrono canonique et sa durée en secondes divergent.")
        return self


class RaceGoal(StrictContract):
    event_type: RaceEventType
    distance_km: float | None = Field(default=None, gt=0)
    date: Date | None = None
    target_time: TargetTime | None = None

    @model_validator(mode="after")
    def validate_standard_distance(self) -> "RaceGoal":
        expected = STANDARD_DISTANCES_KM.get(self.event_type)
        if expected is not None and self.distance_km != expected:
            raise ValueError(
                f"La distance de {self.event_type} doit être exactement {expected} km."
            )
        return self


class GoalV1(StrictContract):
    schema_version: Literal[1] = SCHEMA_VERSION
    intent: GoalIntent
    race: RaceGoal | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_optional_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Le timestamp doit être au format ISO 8601.") from error
        return parsed.isoformat()

    @model_validator(mode="after")
    def validate_conditional_race(self) -> "GoalV1":
        if self.intent == "race" and self.race is None:
            raise ValueError("Une intention race exige un bloc race explicite.")
        if self.intent != "race" and self.race is not None:
            raise ValueError("Le bloc race est interdit pour une intention non-race.")
        return self


class GoalAdaptationDiagnostic(StrictContract):
    code: str
    field: str


class GoalDisplaySummary(StrictContract):
    primary_label: str
    secondary_label: str | None = None


def parse_target_time(value: str) -> tuple[str, int]:
    """Validate HH:MM:SS and return its unchanged canonical form and seconds."""
    raw = str(value).strip()
    match = re.fullmatch(r"(\d{2,}):([0-5]\d):([0-5]\d)", raw)
    if not match:
        raise ValueError("Le chrono cible doit utiliser le format HH:MM:SS.")
    hours, minutes, seconds = (int(part) for part in match.groups())
    total = hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        raise ValueError("Le chrono cible doit être strictement positif.")
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}", total


def build_goal_v1(
    intent: GoalIntent,
    *,
    event_type: RaceEventType | None = None,
    distance_km: float | None = None,
    race_date: Date | str | None = None,
    target_time: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> GoalV1:
    """Build a strict Goal V1 from explicit user-declared structured values."""
    if intent != "race":
        if any(value is not None for value in (event_type, distance_km, race_date, target_time)):
            raise ValueError("Les données de course sont interdites pour une intention non-race.")
        return GoalV1(
            intent=intent, created_at=created_at, updated_at=updated_at
        )
    if event_type is None:
        raise ValueError("Le type de course est obligatoire pour l'intention race.")
    expected = STANDARD_DISTANCES_KM.get(event_type)
    if expected is not None:
        if distance_km is not None and not math.isclose(
            float(distance_km), expected, rel_tol=0, abs_tol=1e-9
        ):
            raise ValueError(
                f"La distance déclarée ne correspond pas au type {event_type}."
            )
        normalized_distance = expected
    else:
        normalized_distance = distance_km
    parsed_target = None
    if target_time is not None:
        canonical, seconds = parse_target_time(target_time)
        parsed_target = TargetTime(canonical=canonical, seconds=seconds)
    return GoalV1(
        intent="race",
        race=RaceGoal(
            event_type=event_type,
            distance_km=normalized_distance,
            date=race_date,
            target_time=parsed_target,
        ),
        created_at=created_at,
        updated_at=updated_at,
    )


def _legacy_number(value: object) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _legacy_event_type(distance_km: float | None) -> RaceEventType:
    if distance_km is None:
        return "custom"
    for event_type, standard in STANDARD_DISTANCES_KM.items():
        if math.isclose(
            distance_km,
            standard,
            rel_tol=0,
            abs_tol=LEGACY_DISTANCE_TOLERANCE_KM,
        ):
            return event_type  # type: ignore[return-value]
    return "custom"


def goal_from_legacy_objective(
    objective: dict[str, Any],
    *,
    diagnostics: list[GoalAdaptationDiagnostic] | None = None,
) -> GoalV1:
    """Adapt legacy objective data tolerantly without mutating or migrating it."""
    if not isinstance(objective, dict):
        raise ValueError("L'objectif legacy doit être un objet.")
    issues = diagnostics if diagnostics is not None else []
    legacy_type = str(objective.get("goalType") or "").strip().lower()
    mapping: dict[str, GoalIntent] = {
        "race": "race",
        "performance": "progress",
        "progress": "progress",
        "beginner": "beginner",
        "comeback": "comeback",
    }
    if legacy_type not in mapping:
        raise ValueError(f"Type d'objectif legacy non supporté : {legacy_type or '<vide>'}.")
    intent = mapping[legacy_type]
    if intent != "race":
        if any(
            objective.get(field) not in (None, "")
            for field in ("distanceKm", "raceDate", "goal_target_time")
        ):
            issues.append(
                GoalAdaptationDiagnostic(
                    code="inactive_legacy_race_fields_ignored", field="race"
                )
            )
        return build_goal_v1(intent)

    raw_distance = objective.get("distanceKm")
    distance = _legacy_number(raw_distance)
    if raw_distance not in (None, "") and distance is None:
        issues.append(
            GoalAdaptationDiagnostic(
                code="invalid_legacy_distance_ignored", field="distanceKm"
            )
        )
    event_type = _legacy_event_type(distance)
    if distance is None:
        issues.append(
            GoalAdaptationDiagnostic(
                code="legacy_race_distance_unavailable", field="distanceKm"
            )
        )

    raw_date = str(objective.get("raceDate") or "").strip()
    parsed_date: Date | None = None
    if raw_date:
        try:
            parsed_date = Date.fromisoformat(raw_date)
        except ValueError:
            issues.append(
                GoalAdaptationDiagnostic(
                    code="invalid_legacy_race_date_ignored", field="raceDate"
                )
            )

    raw_target = str(objective.get("goal_target_time") or "").strip()
    parsed_target: str | None = None
    if raw_target:
        try:
            parsed_target = parse_target_time(raw_target)[0]
        except ValueError:
            issues.append(
                GoalAdaptationDiagnostic(
                    code="invalid_legacy_target_time_ignored",
                    field="goal_target_time",
                )
            )
    standard_distance = STANDARD_DISTANCES_KM.get(event_type)
    return build_goal_v1(
        "race",
        event_type=event_type,
        distance_km=standard_distance if standard_distance is not None else distance,
        race_date=parsed_date,
        target_time=parsed_target,
    )


_MONTHS_FR = (
    "",
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def goal_display_summary(goal: GoalV1) -> GoalDisplaySummary:
    """Create deterministic French UI labels without coaching logic."""
    simple_labels = {
        "progress": "Progresser",
        "beginner": "Débuter",
        "comeback": "Reprendre",
    }
    if goal.intent != "race":
        return GoalDisplaySummary(primary_label=simple_labels[goal.intent])
    assert goal.race is not None
    event_labels = {
        "5k": "5 km",
        "10k": "10 km",
        "half_marathon": "Semi-marathon",
        "marathon": "Marathon",
        "trail": "Trail",
        "custom": "Course personnalisée",
    }
    details: list[str] = []
    if goal.race.date is not None:
        day = goal.race.date
        details.append(f"{day.day} {_MONTHS_FR[day.month]} {day.year}")
    if goal.race.target_time is not None:
        details.append(f"objectif {goal.race.target_time.canonical}")
    return GoalDisplaySummary(
        primary_label=event_labels[goal.race.event_type],
        secondary_label=" · ".join(details) or None,
    )


def goal_for_ai(goal: GoalV1) -> dict[str, Any]:
    """Return the compact identity-free Goal V1 contract for future AI use."""
    return goal.model_dump(mode="json")


def goal_context_for_legacy_confidence(goal: GoalV1) -> dict[str, Any]:
    """Temporary pure adapter for a future incremental DataConfidence switch."""
    display = goal_display_summary(goal)
    race = goal.race
    return {
        "goalType": "performance" if goal.intent == "progress" else goal.intent,
        "title": display.primary_label,
        "distanceKm": race.distance_km if race else None,
        "raceDate": race.date.isoformat() if race and race.date else None,
        "goal_target_time": (
            race.target_time.canonical if race and race.target_time else None
        ),
    }
