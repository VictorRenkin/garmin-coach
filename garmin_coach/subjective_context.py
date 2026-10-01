"""Deterministic SubjectiveContext V1 and conservative legacy adapter."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any, Iterable, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


SCHEMA_VERSION = 1
MAX_PAIN_DESCRIPTION_LENGTH = 300
MAX_PREFERENCE_LENGTH = 200
MAX_PREFERENCES = 5
MAX_UNRECORDED_ACTIVITIES = 10

PainStatus = Literal["explicit_no", "explicit_yes", "unknown"]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _validate_clean_text(value: str, *, field: str, maximum: int) -> str:
    if not value or value != _clean_text(value):
        raise ValueError(f"{field} doit être un texte non vide et normalisé.")
    if len(value) > maximum:
        raise ValueError(f"{field} dépasse la longueur maximale de {maximum} caractères.")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{field} contient un caractère de contrôle interdit.")
    return value


class PainOrInjury(StrictContract):
    status: PainStatus
    affecting_training: bool | None
    description: str | None = Field(default=None, max_length=MAX_PAIN_DESCRIPTION_LENGTH)

    @field_validator("affecting_training", mode="before")
    @classmethod
    def validate_explicit_boolean(cls, value: object) -> object:
        if value is not None and not isinstance(value, bool):
            raise ValueError("affecting_training doit être un booléen explicite.")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_clean_text(
            value,
            field="pain_or_injury.description",
            maximum=MAX_PAIN_DESCRIPTION_LENGTH,
        )

    @model_validator(mode="after")
    def validate_status(self) -> "PainOrInjury":
        expected = {
            "explicit_no": False,
            "explicit_yes": True,
            "unknown": None,
        }[self.status]
        if self.affecting_training is not expected:
            raise ValueError("Le statut douleur et affecting_training divergent.")
        if self.status != "explicit_yes" and self.description is not None:
            raise ValueError(
                "Une description est uniquement valide avec explicit_yes."
            )
        return self


class UnrecordedActivity(StrictContract):
    source: Literal["user_reported_unrecorded"] = "user_reported_unrecorded"
    sport: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    sessions_per_week: int = Field(gt=0)
    typical_duration_minutes: int | None = Field(default=None, gt=0)

    @field_validator("sessions_per_week", "typical_duration_minutes", mode="before")
    @classmethod
    def validate_explicit_integer(cls, value: object) -> object:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise ValueError("Les fréquences et durées doivent être des entiers.")
        return value


class SubjectiveContextV1(StrictContract):
    schema_version: Literal[1] = SCHEMA_VERSION
    pain_or_injury: PainOrInjury
    important_preferences: list[str] = Field(
        default_factory=list, max_length=MAX_PREFERENCES
    )
    unrecorded_activities: list[UnrecordedActivity] = Field(
        default_factory=list, max_length=MAX_UNRECORDED_ACTIVITIES
    )
    reported_at: str | None = None

    @field_validator("important_preferences")
    @classmethod
    def validate_preferences(cls, values: list[str]) -> list[str]:
        for value in values:
            _validate_clean_text(
                value,
                field="important_preferences",
                maximum=MAX_PREFERENCE_LENGTH,
            )
        if len(set(values)) != len(values):
            raise ValueError("important_preferences ne peut pas contenir de doublon.")
        return values

    @field_validator("reported_at")
    @classmethod
    def validate_reported_at(cls, value: str | None) -> str | None:
        if value is None:
            return None
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("reported_at doit être au format ISO 8601.") from error
        return parsed.isoformat()


class SubjectiveContextDiagnostic(StrictContract):
    code: str
    field: str


class LegacySubjectiveContextResult(StrictContract):
    context: SubjectiveContextV1
    diagnostics: list[SubjectiveContextDiagnostic]


def _normalize_sport(value: object) -> str:
    raw = _clean_text(value).lower()
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", raw)
        if not unicodedata.combining(character)
    )
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    if not slug:
        raise ValueError("Le sport d'une activité non enregistrée est obligatoire.")
    if len(slug) > 64:
        raise ValueError("Le sport normalisé dépasse 64 caractères.")
    return slug


def _normalize_preferences(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned:
            raise ValueError("Une préférence ne peut pas être vide.")
        _validate_clean_text(
            cleaned,
            field="important_preferences",
            maximum=MAX_PREFERENCE_LENGTH,
        )
        if cleaned not in result:
            result.append(cleaned)
    return result


def _unrecorded_activity(value: object) -> UnrecordedActivity:
    if isinstance(value, UnrecordedActivity):
        return value
    if not isinstance(value, dict):
        raise ValueError("Une activité non enregistrée doit être un objet.")
    return UnrecordedActivity(
        sport=_normalize_sport(value.get("sport")),
        sessions_per_week=value.get("sessions_per_week"),
        typical_duration_minutes=value.get("typical_duration_minutes"),
    )


def build_subjective_context_v1(
    *,
    affecting_training: bool | None,
    pain_description: str | None = None,
    important_preferences: Iterable[object] | None = None,
    unrecorded_activities: Iterable[object] | None = None,
    reported_at: str | None = None,
) -> SubjectiveContextV1:
    """Normalize declared values, without inferring measured or medical facts."""
    if affecting_training is not None and not isinstance(affecting_training, bool):
        raise ValueError("affecting_training doit être true, false ou null.")
    status: PainStatus = (
        "unknown"
        if affecting_training is None
        else "explicit_yes" if affecting_training else "explicit_no"
    )
    cleaned_description = _clean_text(pain_description) or None
    return SubjectiveContextV1(
        pain_or_injury=PainOrInjury(
            status=status,
            affecting_training=affecting_training,
            description=cleaned_description,
        ),
        important_preferences=_normalize_preferences(important_preferences or []),
        unrecorded_activities=[
            _unrecorded_activity(item) for item in (unrecorded_activities or [])
        ],
        reported_at=reported_at,
    )


def _legacy_text(value: object) -> str:
    return _clean_text(value)


def _fold(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value.lower())
        if not unicodedata.combining(character)
    ).rstrip(". !")


_EXPLICIT_NO_VALUES = {
    "non",
    "aucune",
    "aucune douleur",
    "aucune douleur actuelle",
    "aucune blessure",
    "pas de douleur",
    "pas de blessure",
    "rien a signaler",
    "ras",
}


def subjective_context_from_legacy_objective(
    objective: dict[str, Any],
) -> LegacySubjectiveContextResult:
    """Adapt legacy free text conservatively; never mutate the stored profile."""
    if not isinstance(objective, dict):
        raise ValueError("L'objectif legacy doit être un objet.")
    diagnostics: list[SubjectiveContextDiagnostic] = []
    injury = _legacy_text(objective.get("injuryPain"))
    if not injury:
        affecting: bool | None = None
        description = None
        diagnostics.append(
            SubjectiveContextDiagnostic(
                code="reported_context_unknown", field="injuryPain"
            )
        )
    elif _fold(injury) in _EXPLICIT_NO_VALUES:
        affecting = False
        description = None
        diagnostics.append(
            SubjectiveContextDiagnostic(
                code="legacy_explicit_no_recognized", field="injuryPain"
            )
        )
    else:
        affecting = True
        description = injury[:MAX_PAIN_DESCRIPTION_LENGTH].rstrip()
        diagnostics.append(
            SubjectiveContextDiagnostic(
                code="legacy_injury_text_assumed_affecting_training",
                field="injuryPain",
            )
        )
        if len(injury) > MAX_PAIN_DESCRIPTION_LENGTH:
            diagnostics.append(
                SubjectiveContextDiagnostic(
                    code="legacy_injury_description_truncated",
                    field="injuryPain",
                )
            )

    preferences: list[str] = []
    preference = _legacy_text(objective.get("preferences"))
    if preference:
        preferences.append(preference[:MAX_PREFERENCE_LENGTH].rstrip())
        diagnostics.append(
            SubjectiveContextDiagnostic(
                code="legacy_preference_preserved", field="preferences"
            )
        )
        if len(preference) > MAX_PREFERENCE_LENGTH:
            diagnostics.append(
                SubjectiveContextDiagnostic(
                    code="legacy_preference_truncated", field="preferences"
                )
            )

    constraint = _legacy_text(objective.get("constraints"))
    if constraint:
        diagnostics.append(
            SubjectiveContextDiagnostic(
                code="legacy_constraint_unmapped", field="constraints"
            )
        )

    return LegacySubjectiveContextResult(
        context=build_subjective_context_v1(
            affecting_training=affecting,
            pain_description=description,
            important_preferences=preferences,
            unrecorded_activities=[],
            reported_at=None,
        ),
        diagnostics=diagnostics,
    )


def subjective_context_for_ai(context: SubjectiveContextV1) -> dict[str, Any]:
    """Return only the compact declared context needed by future coaching logic."""
    return context.model_dump(mode="json")
