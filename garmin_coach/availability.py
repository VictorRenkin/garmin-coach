"""Deterministic Availability V1 contract and tolerant legacy adapter."""
from __future__ import annotations

from datetime import date as Date
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = 1
CANONICAL_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
Day = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AvailabilityV1(StrictContract):
    schema_version: Literal[1] = SCHEMA_VERSION
    available_days: list[Day] = Field(min_length=1)
    preferred_sessions_per_week: int | None = Field(default=None, ge=1, le=7)
    long_session_preferred_days: list[Day] = Field(default_factory=list)
    effective_from: Date | None = None

    @field_validator("preferred_sessions_per_week", mode="before")
    @classmethod
    def validate_explicit_integer(cls, value: object) -> object:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise ValueError("preferred_sessions_per_week doit être un entier.")
        return value

    @model_validator(mode="after")
    def validate_week(self) -> "AvailabilityV1":
        if len(set(self.available_days)) != len(self.available_days):
            raise ValueError("available_days ne peut pas contenir de doublon.")
        expected_available = [
            day for day in CANONICAL_DAYS if day in self.available_days
        ]
        if self.available_days != expected_available:
            raise ValueError("available_days doit suivre l'ordre canonique de la semaine.")
        if len(set(self.long_session_preferred_days)) != len(
            self.long_session_preferred_days
        ):
            raise ValueError(
                "long_session_preferred_days ne peut pas contenir de doublon."
            )
        expected_long = [
            day for day in CANONICAL_DAYS if day in self.long_session_preferred_days
        ]
        if self.long_session_preferred_days != expected_long:
            raise ValueError(
                "long_session_preferred_days doit suivre l'ordre canonique."
            )
        unavailable = set(self.long_session_preferred_days) - set(self.available_days)
        if unavailable:
            raise ValueError(
                "Chaque jour préféré pour une séance longue doit être disponible."
            )
        if (
            self.preferred_sessions_per_week is not None
            and self.preferred_sessions_per_week > len(self.available_days)
        ):
            raise ValueError(
                "Le nombre de séances préférées dépasse le nombre de jours disponibles."
            )
        return self


class AvailabilityDiagnostic(StrictContract):
    code: str
    field: str
    details: dict[str, Any] = Field(default_factory=dict)


class LegacyAvailabilityResult(StrictContract):
    availability: AvailabilityV1 | None
    diagnostics: list[AvailabilityDiagnostic]


def _normalized_day(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in CANONICAL_DAYS else None


def _normalize_days_strict(values: Iterable[object], *, field: str) -> list[Day]:
    normalized: set[str] = set()
    for value in values:
        day = _normalized_day(value)
        if day is None:
            raise ValueError(f"Jour invalide dans {field} : {value!r}.")
        normalized.add(day)
    return [day for day in CANONICAL_DAYS if day in normalized]  # type: ignore[misc]


def build_availability_v1(
    available_days: Iterable[object],
    *,
    preferred_sessions_per_week: int | None = None,
    long_session_preferred_days: Iterable[object] | None = None,
    effective_from: Date | str | None = None,
) -> AvailabilityV1:
    """Normalize UI values, then construct the strict Availability V1 contract."""
    normalized_available = _normalize_days_strict(
        available_days, field="available_days"
    )
    normalized_long = _normalize_days_strict(
        long_session_preferred_days or [], field="long_session_preferred_days"
    )
    return AvailabilityV1(
        available_days=normalized_available,
        preferred_sessions_per_week=preferred_sessions_per_week,
        long_session_preferred_days=normalized_long,
        effective_from=effective_from,
    )


def _legacy_days(
    value: object,
    *,
    field: str,
    diagnostics: list[AvailabilityDiagnostic],
    required: bool = True,
) -> list[Day] | None:
    if not isinstance(value, list):
        diagnostics.append(
            AvailabilityDiagnostic(
                code=("available_days_missing" if required else "invalid_available_day"),
                field=field,
            )
        )
        return None
    valid: list[str] = []
    invalid_count = 0
    for item in value:
        day = _normalized_day(item)
        if day is None:
            invalid_count += 1
        else:
            valid.append(day)
    if invalid_count:
        diagnostics.append(
            AvailabilityDiagnostic(
                code="invalid_available_day",
                field=field,
                details={"affected_values": invalid_count},
            )
        )
    if len(set(valid)) != len(valid):
        diagnostics.append(
            AvailabilityDiagnostic(code="duplicate_available_day", field=field)
        )
    normalized = [day for day in CANONICAL_DAYS if day in set(valid)]
    if not normalized and required:
        diagnostics.append(
            AvailabilityDiagnostic(code="available_days_missing", field=field)
        )
        return None
    return normalized  # type: ignore[return-value]


def _legacy_sessions(
    value: object, diagnostics: list[AvailabilityDiagnostic]
) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        parsed = None
    elif isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        parsed = None
    if parsed is None or not 1 <= parsed <= 7:
        diagnostics.append(
            AvailabilityDiagnostic(
                code="invalid_sessions_per_week", field="sessionsPerWeek"
            )
        )
        return None
    return parsed


def availability_from_legacy_objective(
    objective: dict[str, Any],
) -> LegacyAvailabilityResult:
    """Adapt legacy declared availability without mutating or inventing data."""
    if not isinstance(objective, dict):
        raise ValueError("L'objectif legacy doit être un objet.")
    diagnostics: list[AvailabilityDiagnostic] = []
    available = _legacy_days(
        objective.get("availableDays"),
        field="availableDays",
        diagnostics=diagnostics,
    )
    preferred = _legacy_sessions(objective.get("sessionsPerWeek"), diagnostics)
    if available is None:
        return LegacyAvailabilityResult(
            availability=None, diagnostics=diagnostics
        )
    if preferred is not None and preferred > len(available):
        diagnostics.append(
            AvailabilityDiagnostic(
                code="sessions_exceed_available_days",
                field="sessionsPerWeek",
                details={
                    "preferred_sessions_per_week": preferred,
                    "available_day_count": len(available),
                },
            )
        )
        preferred = None

    long_days: list[Day] = []
    raw_long = objective.get("longSessionPreferredDays")
    if raw_long not in (None, []):
        parsed_long = _legacy_days(
            raw_long,
            field="longSessionPreferredDays",
            diagnostics=diagnostics,
            required=False,
        )
        for day in parsed_long or []:
            if day not in available:
                diagnostics.append(
                    AvailabilityDiagnostic(
                        code="long_session_day_not_available",
                        field="longSessionPreferredDays",
                    )
                )
            else:
                long_days.append(day)

    return LegacyAvailabilityResult(
        availability=AvailabilityV1(
            available_days=available,
            preferred_sessions_per_week=preferred,
            long_session_preferred_days=long_days,
            effective_from=None,
        ),
        diagnostics=diagnostics,
    )


def availability_for_ai(availability: AvailabilityV1) -> dict[str, Any]:
    """Return the compact identity-free Availability V1 contract."""
    return availability.model_dump(mode="json")
