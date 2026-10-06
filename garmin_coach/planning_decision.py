"""AI frequency recommendation and explicit user planning decision V1.

The AI recommends one weekly frequency from compact factual inputs. Motionest
only enforces hard availability constraints, persists an explicit user choice,
and derives deterministic plan constraints from that confirmed choice.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from . import ai_provider
from .availability import Day
from .config import (
    PLANNING_DECISION_FILE,
    PLANNING_RECOMMENDATION_FILE,
    Settings,
    ensure_directories,
    load_settings,
    user_context,
)
from .goal_assessment import (
    GoalAssessmentError,
    GoalAssessmentStateV1,
    build_goal_assessment_input_v1,
    get_goal_assessment_state,
    goal_assessment_fingerprint,
    validate_goal_assessment,
)
from .runtime_context import RuntimeMotionestContextV1, build_runtime_motionest_context


SCHEMA_VERSION = 1
PROMPT_VERSION = 2
MAX_SUPPORTING_FACTS = 12
MAX_TEXT_ITEMS = 10
MAX_SUMMARY_LENGTH = 240
MAX_EXPLANATION_LENGTH = 1_000
MAX_ITEM_LENGTH = 240

RecommendationStatus = Literal["recommended", "insufficient_context"]
Confidence = Literal["low", "medium", "high"]
SupportingRole = Literal["supports", "challenges", "contextual"]
StoreStatus = Literal["available", "missing", "stale"]
DecisionSource = Literal["accepted_recommendation", "user_override"]

PLANNING_RECOMMENDATION_PROMPT_V1 = """Tu recommandes uniquement un nombre de séances par semaine, jamais un calendrier.
Les faits Motionest fournis sont les seuls faits autoritaires sur cet utilisateur.
Tu peux utiliser des principes généraux de coaching, mais ne les présente jamais comme des faits personnels.
Respecte strictement le nombre de jours disponibles et recommande un entier entre 1 et 7.
La préférence utilisateur n'est pas une contrainte absolue ; explique brièvement un écart important.
La fréquence observée est une preuve, ni une cible imposée, ni un minimum, ni un maximum.
GoalAssessment est une interprétation structurée, jamais une garantie de résultat.
N'invente aucune donnée personnelle, Recovery, fatigue, douleur, blessure, activité ou performance.
Ne diagnostique pas. Une douleur ou blessure déclarée est une contrainte de coaching à prendre en compte,
pas un motif automatique pour refuser toute recommandation. Lorsque les faits le permettent, recommande
une fréquence adaptée et explique clairement les adaptations, les signaux d'arrêt et les limites.
La description est une déclaration utilisateur, pas un diagnostic médical ni un fait mesuré.
Retourne insufficient_context uniquement si une donnée indispensable manque, si les faits se contredisent,
ou si la déclaration décrit une contre-indication explicite ou un risque médical qui empêche réellement
de proposer une fréquence. L'insistance de l'utilisateur ne permet jamais d'ignorer un risque grave.
Ne génère aucune séance, ne choisis aucun jour précis et ne construis aucun plan.
Les supporting_facts doivent référencer exclusivement des paths présents dans facts.
L'explication doit être concise et distinguer principes généraux et faits propres à l'utilisateur."""


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PlanningRecommendationInputV1(StrictContract):
    schema_version: Literal[1] = SCHEMA_VERSION
    prompt_version: Literal[2] = PROMPT_VERSION
    as_of: str
    facts: dict[str, Any]
    limitations: list[str]


class SupportingFact(StrictContract):
    path: str = Field(min_length=1, max_length=160)
    role: SupportingRole


def _normalized_text(value: str, *, maximum: int, field: str) -> str:
    normalized = " ".join(str(value).strip().split())
    if not normalized or normalized != value:
        raise ValueError(f"{field} doit être un texte non vide et normalisé.")
    if len(normalized) > maximum:
        raise ValueError(f"{field} dépasse {maximum} caractères.")
    if any(ord(character) < 32 for character in normalized):
        raise ValueError(f"{field} contient un caractère de contrôle.")
    return normalized


class PlanningRecommendationV1(StrictContract):
    schema_version: Literal[1] = SCHEMA_VERSION
    prompt_version: Literal[2] = PROMPT_VERSION
    status: RecommendationStatus
    recommended_sessions_per_week: int | None = Field(default=None, ge=1, le=7)
    confidence: Confidence
    summary: str = Field(max_length=MAX_SUMMARY_LENGTH)
    explanation: str = Field(max_length=MAX_EXPLANATION_LENGTH)
    supporting_facts: list[SupportingFact] = Field(
        min_length=1, max_length=MAX_SUPPORTING_FACTS
    )
    uncertainties: list[str] = Field(default_factory=list, max_length=MAX_TEXT_ITEMS)
    caveats: list[str] = Field(default_factory=list, max_length=MAX_TEXT_ITEMS)

    @field_validator("recommended_sessions_per_week", mode="before")
    @classmethod
    def validate_explicit_integer(cls, value: object) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError("recommended_sessions_per_week doit être un entier.")
        return value

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _normalized_text(value, maximum=MAX_SUMMARY_LENGTH, field="summary")

    @field_validator("explanation")
    @classmethod
    def validate_explanation(cls, value: str) -> str:
        return _normalized_text(
            value, maximum=MAX_EXPLANATION_LENGTH, field="explanation"
        )

    @field_validator("uncertainties", "caveats")
    @classmethod
    def validate_items(cls, values: list[str]) -> list[str]:
        normalized = [
            _normalized_text(value, maximum=MAX_ITEM_LENGTH, field="item")
            for value in values
        ]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Les éléments doivent être uniques.")
        return normalized

    @model_validator(mode="after")
    def validate_status_value(self) -> "PlanningRecommendationV1":
        if self.status == "recommended" and self.recommended_sessions_per_week is None:
            raise ValueError("Une recommandation doit contenir une fréquence.")
        if self.status == "insufficient_context" and self.recommended_sessions_per_week is not None:
            raise ValueError("insufficient_context interdit une fréquence recommandée.")
        return self


class PlanningRecommendationStoreV1(StrictContract):
    schema_version: Literal[1] = SCHEMA_VERSION
    prompt_version: Literal[2] = PROMPT_VERSION
    prompt_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    recommendation: PlanningRecommendationV1
    generated_at: str
    provider: str
    model: str


class PlanningRecommendationStateV1(StrictContract):
    status: StoreStatus
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    recommendation: PlanningRecommendationV1 | None = None
    cache_hit: bool = False
    provider: str | None = None
    model: str | None = None
    diagnostics: list[str] = Field(default_factory=list)


class PlanningDecisionV1(StrictContract):
    schema_version: Literal[1] = SCHEMA_VERSION
    confirmed_sessions_per_week: int = Field(ge=1, le=7)
    decision_source: DecisionSource
    recommendation_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision_context_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed_at: str

    @field_validator("confirmed_sessions_per_week", mode="before")
    @classmethod
    def validate_explicit_integer(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("confirmed_sessions_per_week doit être un entier.")
        return value

    @field_validator("confirmed_at")
    @classmethod
    def validate_confirmed_at(cls, value: str) -> str:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError as exc:
            raise ValueError("confirmed_at doit être au format ISO 8601.") from exc


class PlanConstraintsV1(StrictContract):
    schema_version: Literal[1] = SCHEMA_VERSION
    confirmed_sessions_per_week: int = Field(ge=1, le=7)
    available_days: list[Day] = Field(min_length=1)
    long_session_preferred_days: list[Day]
    goal_intent: Literal["progress", "race", "beginner", "comeback"]
    source: Literal["user_confirmed"] = "user_confirmed"


class PlanningDecisionStateV1(StrictContract):
    status: StoreStatus
    decision_context_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: PlanningDecisionV1 | None = None
    plan_constraints: PlanConstraintsV1 | None = None
    diagnostics: list[str] = Field(default_factory=list)


class PlanningDecisionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _LockEntry:
    lock: threading.Lock
    users: int = 0


_LOCK_REGISTRY_GUARD = threading.Lock()
_LOCK_REGISTRY: dict[str, _LockEntry] = {}


@contextmanager
def _target_lock(target: Path) -> Iterator[None]:
    """Mono-process lock scoped to one user's target file."""
    key = str(target.resolve())
    with _LOCK_REGISTRY_GUARD:
        entry = _LOCK_REGISTRY.get(key)
        if entry is None:
            entry = _LockEntry(lock=threading.Lock())
            _LOCK_REGISTRY[key] = entry
        entry.users += 1
    try:
        with entry.lock:
            yield
    finally:
        with _LOCK_REGISTRY_GUARD:
            entry.users -= 1
            if entry.users == 0 and _LOCK_REGISTRY.get(key) is entry:
                del _LOCK_REGISTRY[key]


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prompt_digest(system_prompt: str = PLANNING_RECOMMENDATION_PROMPT_V1) -> str:
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()


def _unrecorded_facts(runtime: RuntimeMotionestContextV1) -> dict[str, Any]:
    activities = runtime.context.subjective_context.unrecorded_activities
    total_sessions = sum(item.sessions_per_week for item in activities)
    total_duration = (
        sum(
            item.sessions_per_week * item.typical_duration_minutes
            for item in activities
            if item.typical_duration_minutes is not None
        )
        if activities
        and all(item.typical_duration_minutes is not None for item in activities)
        else None
    )
    return {
        "unrecorded_activities.count": len(activities),
        "unrecorded_activities.total_reported_sessions_per_week": total_sessions,
        "unrecorded_activities.total_typical_duration_minutes_per_week": total_duration,
        "unrecorded_activities.provenance": "user_reported_unrecorded",
    }


def _goal_assessment_facts(
    user_id: str,
    runtime: RuntimeMotionestContextV1,
    assessment_state: GoalAssessmentStateV1 | None,
) -> dict[str, Any]:
    goal = runtime.context.goal
    if goal is None:
        raise PlanningDecisionError("goal_unavailable", "Aucun objectif structuré n'est disponible.")
    if goal.intent != "race":
        return {
            "goal_assessment.assessment_scope": "not_applicable",
            "goal_assessment.assessment": "not_applicable",
            "goal_assessment.confidence": None,
            "goal_assessment.supporting_fact_paths": [],
        }
    expected_source = build_goal_assessment_input_v1(runtime)
    expected_fingerprint = goal_assessment_fingerprint(expected_source)
    state = assessment_state or get_goal_assessment_state(user_id, runtime=runtime)
    if state.status == "stale" or state.input_fingerprint != expected_fingerprint:
        raise PlanningDecisionError(
            "goal_assessment_stale",
            "GoalAssessment doit être régénéré pour les faits actuels.",
        )
    if state.status != "available" or state.assessment is None:
        raise PlanningDecisionError(
            "goal_assessment_required",
            "GoalAssessment doit être généré explicitement avant la recommandation.",
        )
    try:
        assessment = validate_goal_assessment(state.assessment, expected_source)
    except GoalAssessmentError as exc:
        raise PlanningDecisionError(
            "goal_assessment_required",
            "GoalAssessment courant est invalide et doit être régénéré.",
        ) from exc
    return {
        "goal_assessment.assessment_scope": assessment.assessment_scope,
        "goal_assessment.assessment": assessment.assessment,
        "goal_assessment.confidence": assessment.confidence,
        "goal_assessment.supporting_fact_paths": [
            item.path for item in assessment.supporting_facts
        ],
    }


def build_planning_recommendation_input_v1(
    user_id: str,
    runtime: RuntimeMotionestContextV1,
    *,
    goal_assessment_state: GoalAssessmentStateV1 | None = None,
) -> PlanningRecommendationInputV1:
    goal = runtime.context.goal
    availability = runtime.context.availability
    evidence = runtime.goal_evidence
    if goal is None or evidence is None:
        raise PlanningDecisionError("goal_unavailable", "Aucun objectif structuré n'est disponible.")
    if availability is None:
        raise PlanningDecisionError(
            "availability_unavailable", "Aucune disponibilité structurée n'est disponible."
        )
    race = evidence.race
    support = evidence.training_support
    pattern = runtime.training_pattern
    safety = runtime.context.integration.safety_context
    confidence = runtime.context.data_confidence
    facts: dict[str, Any] = {
        "goal.intent": goal.intent,
        "goal.race_event_type": race.event_type,
        "goal.race_distance_km": race.distance_km,
        "goal.days_to_event": race.days_to_event,
        "availability.available_day_count": len(availability.available_days),
        "availability.preferred_sessions_per_week": availability.preferred_sessions_per_week,
        "availability.long_session_preferred_day_count": len(availability.long_session_preferred_days),
        "current_training.observed_sessions_per_week_28d": runtime.context.athlete_state.observed_sessions_per_week_last_28_days,
        "current_training.running_sessions_28d": support.running_sessions_last_28_days,
        "current_training.running_sessions_84d": support.running_sessions_last_84_days,
        "current_training.observed_running_sessions_per_week_84d": support.observed_frequency_last_84_days,
        "current_training.running_distance_28d_km": support.running_distance_last_28_days_km,
        "current_training.running_distance_84d_km": support.running_distance_last_84_days_km,
        "training_pattern.structured_intervals": pattern.session_patterns.structured_intervals.model_dump(mode="json"),
        "training_pattern.relative_long_run": pattern.session_patterns.relative_long_run.model_dump(mode="json"),
        "training_pattern.race": pattern.session_patterns.race.model_dump(mode="json"),
        "training_pattern.pattern_coverage": pattern.pattern_coverage.model_dump(mode="json"),
        "training_pattern.confidence": pattern.confidence.model_dump(mode="json"),
        "safety.pain_status": safety.pain_status,
        "safety.pain_or_injury_affecting_training": safety.pain_or_injury_affecting_training,
        "safety.pain_description_present": safety.pain_description_present,
        "safety.user_reported_pain_or_injury": runtime.context.subjective_context.pain_or_injury.description,
        "confidence.training_history": confidence.training_history,
        "confidence.current_state": confidence.current_state,
        "confidence.training_load": confidence.training_load,
        "confidence.recovery": confidence.recovery,
        **_unrecorded_facts(runtime),
        **_goal_assessment_facts(user_id, runtime, goal_assessment_state),
    }
    limitations = {
        *evidence.limitations,
        *pattern.limitations,
        *(item.code for item in runtime.context.integration.limitations),
    }
    return PlanningRecommendationInputV1(
        as_of=runtime.reference_date,
        facts=facts,
        limitations=sorted(limitations),
    )


def planning_recommendation_fingerprint(
    value: PlanningRecommendationInputV1,
    *,
    prompt_version: int | None = None,
    system_prompt: str | None = None,
) -> str:
    resolved_version = PROMPT_VERSION if prompt_version is None else prompt_version
    if resolved_version < 1:
        raise ValueError("prompt_version doit être positif.")
    return _canonical_digest(
        {
            "schema_version": SCHEMA_VERSION,
            "prompt_version": resolved_version,
            "prompt_digest": _prompt_digest(
                PLANNING_RECOMMENDATION_PROMPT_V1
                if system_prompt is None
                else system_prompt
            ),
            "input": value.model_dump(mode="json"),
        }
    )


def validate_planning_recommendation(
    value: PlanningRecommendationV1,
    source: PlanningRecommendationInputV1,
) -> PlanningRecommendationV1:
    available = source.facts["availability.available_day_count"]
    recommended = value.recommended_sessions_per_week
    if recommended is not None and recommended > available:
        raise PlanningDecisionError(
            "recommendation_outside_availability",
            "La recommandation dépasse le nombre de jours disponibles.",
        )
    allowed = set(source.facts)
    paths = [item.path for item in value.supporting_facts]
    unknown = sorted(set(paths) - allowed)
    if unknown:
        raise PlanningDecisionError(
            "unknown_supporting_fact",
            "La recommandation référence un fait absent du payload autorisé.",
        )
    if len(paths) != len(set(paths)):
        raise PlanningDecisionError(
            "duplicate_supporting_fact",
            "Un fait ne peut être référencé qu'une seule fois.",
        )
    return value


def _provider_failure_code(error: Exception) -> str:
    name = type(error).__name__.lower()
    message = str(error).lower()
    if isinstance(error, TimeoutError) or "timeout" in name or "timed out" in message:
        return "provider_timeout"
    if "quota" in name or "quota" in message or "insufficient_quota" in message:
        return "provider_quota_exhausted"
    if "ratelimit" in name or "rate limit" in message or "status 429" in message:
        return "provider_rate_limited"
    return "provider_unavailable"


def _is_invalid_provider_response(error: Exception) -> bool:
    if isinstance(error, (ValidationError, json.JSONDecodeError)):
        return True
    message = str(error)
    return (
        isinstance(error, ValueError)
        and message == "L'IA n'a pas renvoyé de JSON exploitable."
    ) or (
        isinstance(error, RuntimeError)
        and message == "L'IA n'a pas produit de réponse structurée."
    )


def _read_recommendation_store() -> tuple[PlanningRecommendationStoreV1 | None, list[str]]:
    target = Path(os.fspath(PLANNING_RECOMMENDATION_FILE))
    if not target.is_file():
        return None, []
    try:
        return PlanningRecommendationStoreV1.model_validate_json(
            target.read_text(encoding="utf-8")
        ), []
    except (OSError, UnicodeDecodeError, ValueError, ValidationError):
        return None, ["planning_recommendation_store_invalid"]


def _atomic_write(target: Path, model: BaseModel, model_type: type[BaseModel]) -> None:
    ensure_directories()
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2)
    try:
        temporary.write_text(encoded, encoding="utf-8")
        model_type.model_validate_json(temporary.read_text(encoding="utf-8"))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _current_recommendation_state(
    source: PlanningRecommendationInputV1,
) -> PlanningRecommendationStateV1:
    fingerprint = planning_recommendation_fingerprint(source)
    store, diagnostics = _read_recommendation_store()
    if store is None:
        return PlanningRecommendationStateV1(
            status="missing", input_fingerprint=fingerprint, diagnostics=diagnostics
        )
    if store.input_fingerprint != fingerprint:
        return PlanningRecommendationStateV1(
            status="stale",
            input_fingerprint=fingerprint,
            provider=store.provider,
            model=store.model,
        )
    try:
        recommendation = validate_planning_recommendation(store.recommendation, source)
    except PlanningDecisionError:
        return PlanningRecommendationStateV1(
            status="missing",
            input_fingerprint=fingerprint,
            diagnostics=["planning_recommendation_store_semantically_invalid"],
        )
    return PlanningRecommendationStateV1(
        status="available",
        input_fingerprint=fingerprint,
        recommendation=recommendation,
        cache_hit=True,
        provider=store.provider,
        model=store.model,
    )


def get_planning_recommendation_state(
    user_id: str,
    *,
    runtime: RuntimeMotionestContextV1 | None = None,
    goal_assessment_state: GoalAssessmentStateV1 | None = None,
) -> PlanningRecommendationStateV1:
    with user_context(user_id):
        factual_runtime = runtime or build_runtime_motionest_context(user_id)
        source = build_planning_recommendation_input_v1(
            user_id,
            factual_runtime,
            goal_assessment_state=goal_assessment_state,
        )
        return _current_recommendation_state(source)


def generate_planning_recommendation(
    user_id: str,
    *,
    runtime: RuntimeMotionestContextV1 | None = None,
    goal_assessment_state: GoalAssessmentStateV1 | None = None,
    settings: Settings | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> PlanningRecommendationStateV1:
    measured = diagnostics if diagnostics is not None else {}
    started = time.perf_counter()
    with user_context(user_id):
        factual_runtime = runtime or build_runtime_motionest_context(user_id)
        source = build_planning_recommendation_input_v1(
            user_id,
            factual_runtime,
            goal_assessment_state=goal_assessment_state,
        )
        encoded_input = json.dumps(
            source.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        fingerprint = planning_recommendation_fingerprint(source)
        target = Path(os.fspath(PLANNING_RECOMMENDATION_FILE))
        with _target_lock(target):
            current = _current_recommendation_state(source)
            if current.status == "available":
                measured.update(
                    {
                        "cache": "hit",
                        "input_bytes": len(encoded_input),
                        "local_seconds": round(time.perf_counter() - started, 6),
                    }
                )
                return current
            resolved_settings = settings or load_settings()
            if not resolved_settings.ai_api_key:
                raise PlanningDecisionError(
                    "ai_not_configured", "Aucune clé API d'IA n'est configurée."
                )
            provider_name = resolved_settings.ai_provider
            model_name = ai_provider.routed_openai_model(resolved_settings, "balanced")
            try:
                candidate = ai_provider.structured_completion(
                    resolved_settings,
                    instructions=PLANNING_RECOMMENDATION_PROMPT_V1,
                    payload=source.model_dump(mode="json"),
                    result_type=PlanningRecommendationV1,
                    reasoning_effort="medium",
                    category="planning_recommendation",
                    max_output_tokens=1_000,
                    quality="balanced",
                )
            except PlanningDecisionError:
                raise
            except Exception as exc:
                if _is_invalid_provider_response(exc):
                    raise PlanningDecisionError(
                        "invalid_provider_response",
                        "La réponse structurée de l'IA est invalide.",
                    ) from exc
                raise PlanningDecisionError(
                    _provider_failure_code(exc),
                    "Le fournisseur d'IA est indisponible.",
                ) from exc
            recommendation = validate_planning_recommendation(candidate, source)
            store = PlanningRecommendationStoreV1(
                prompt_digest=_prompt_digest(),
                input_fingerprint=fingerprint,
                recommendation=recommendation,
                generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                provider=provider_name,
                model=model_name,
            )
            _atomic_write(target, store, PlanningRecommendationStoreV1)
            response_bytes = len(
                json.dumps(
                    recommendation.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            measured.update(
                {
                    "cache": "miss",
                    "input_bytes": len(encoded_input),
                    "response_bytes": response_bytes,
                    "local_seconds": round(time.perf_counter() - started, 6),
                }
            )
            return PlanningRecommendationStateV1(
                status="available",
                input_fingerprint=fingerprint,
                recommendation=recommendation,
                cache_hit=False,
                provider=provider_name,
                model=model_name,
            )


def _decision_context_payload(runtime: RuntimeMotionestContextV1) -> dict[str, Any]:
    goal = runtime.context.goal
    availability = runtime.context.availability
    if goal is None:
        raise PlanningDecisionError("goal_unavailable", "Aucun objectif structuré n'est disponible.")
    if availability is None:
        raise PlanningDecisionError(
            "availability_unavailable", "Aucune disponibilité structurée n'est disponible."
        )
    semantic_goal = {
        "intent": goal.intent,
        "race": goal.race.model_dump(mode="json") if goal.race is not None else None,
    }
    safety = runtime.context.integration.safety_context
    return {
        "schema_version": SCHEMA_VERSION,
        "goal": semantic_goal,
        "availability": availability.model_dump(mode="json"),
        "safety": {
            "pain_status": safety.pain_status,
            "pain_or_injury_affecting_training": safety.pain_or_injury_affecting_training,
            "pain_description_present": safety.pain_description_present,
            "user_reported_pain_or_injury": runtime.context.subjective_context.pain_or_injury.description,
        },
        "unrecorded_activities": _unrecorded_facts(runtime),
    }


def decision_context_fingerprint(runtime: RuntimeMotionestContextV1) -> str:
    """Fingerprint declarations that materially invalidate a human decision.

    Volatile Garmin history is deliberately excluded.
    """
    return _canonical_digest(_decision_context_payload(runtime))


def build_plan_constraints_v1(
    decision: PlanningDecisionV1,
    runtime: RuntimeMotionestContextV1,
) -> PlanConstraintsV1:
    goal = runtime.context.goal
    availability = runtime.context.availability
    if goal is None or availability is None:
        raise PlanningDecisionError(
            "decision_invalid", "Les déclarations requises ne sont pas disponibles."
        )
    if decision.decision_context_fingerprint != decision_context_fingerprint(runtime):
        raise PlanningDecisionError(
            "decision_stale",
            "La décision confirmée ne correspond plus aux déclarations actuelles.",
        )
    if decision.confirmed_sessions_per_week > len(availability.available_days):
        raise PlanningDecisionError(
            "decision_stale", "La fréquence confirmée dépasse la disponibilité actuelle."
        )
    return PlanConstraintsV1(
        confirmed_sessions_per_week=decision.confirmed_sessions_per_week,
        available_days=availability.available_days,
        long_session_preferred_days=availability.long_session_preferred_days,
        goal_intent=goal.intent,
    )


def _read_decision() -> tuple[PlanningDecisionV1 | None, list[str]]:
    target = Path(os.fspath(PLANNING_DECISION_FILE))
    if not target.is_file():
        return None, []
    try:
        return PlanningDecisionV1.model_validate_json(
            target.read_text(encoding="utf-8")
        ), []
    except (OSError, UnicodeDecodeError, ValueError, ValidationError):
        return None, ["planning_decision_store_invalid"]


def _current_decision_state(
    runtime: RuntimeMotionestContextV1,
) -> PlanningDecisionStateV1:
    fingerprint = decision_context_fingerprint(runtime)
    decision, diagnostics = _read_decision()
    if decision is None:
        return PlanningDecisionStateV1(
            status="missing",
            decision_context_fingerprint=fingerprint,
            diagnostics=diagnostics,
        )
    if decision.decision_context_fingerprint != fingerprint:
        return PlanningDecisionStateV1(
            status="stale",
            decision_context_fingerprint=fingerprint,
            diagnostics=["decision_context_changed"],
        )
    try:
        constraints = build_plan_constraints_v1(decision, runtime)
    except PlanningDecisionError as exc:
        return PlanningDecisionStateV1(
            status="stale",
            decision_context_fingerprint=fingerprint,
            diagnostics=[exc.code],
        )
    return PlanningDecisionStateV1(
        status="available",
        decision_context_fingerprint=fingerprint,
        decision=decision,
        plan_constraints=constraints,
    )


def get_planning_decision_state(
    user_id: str,
    *,
    runtime: RuntimeMotionestContextV1 | None = None,
) -> PlanningDecisionStateV1:
    with user_context(user_id):
        factual_runtime = runtime or build_runtime_motionest_context(user_id)
        return _current_decision_state(factual_runtime)


def confirm_planning_decision(
    user_id: str,
    *,
    confirmed_sessions_per_week: int,
    recommendation_fingerprint: str,
    runtime: RuntimeMotionestContextV1 | None = None,
    goal_assessment_state: GoalAssessmentStateV1 | None = None,
) -> PlanningDecisionStateV1:
    if isinstance(confirmed_sessions_per_week, bool) or not isinstance(
        confirmed_sessions_per_week, int
    ):
        raise PlanningDecisionError(
            "decision_invalid", "La fréquence confirmée doit être un entier."
        )
    with user_context(user_id):
        factual_runtime = runtime or build_runtime_motionest_context(user_id)
        availability = factual_runtime.context.availability
        if availability is None:
            raise PlanningDecisionError(
                "availability_unavailable", "Aucune disponibilité structurée n'est disponible."
            )
        if not 1 <= confirmed_sessions_per_week <= 7:
            raise PlanningDecisionError(
                "decision_invalid", "La fréquence confirmée doit être comprise entre 1 et 7."
            )
        if confirmed_sessions_per_week > len(availability.available_days):
            raise PlanningDecisionError(
                "decision_invalid", "La fréquence confirmée dépasse les jours disponibles."
            )
        source = build_planning_recommendation_input_v1(
            user_id,
            factual_runtime,
            goal_assessment_state=goal_assessment_state,
        )
        current = _current_recommendation_state(source)
        if (
            current.status != "available"
            or current.recommendation is None
            or recommendation_fingerprint != current.input_fingerprint
        ):
            raise PlanningDecisionError(
                "recommendation_stale",
                "La recommandation affichée n'est plus la recommandation actuelle.",
            )
        recommended = current.recommendation.recommended_sessions_per_week
        if (
            current.recommendation.status != "recommended"
            or recommended is None
        ):
            raise PlanningDecisionError(
                "recommendation_insufficient_context",
                "Aucune recommandation numérique ne peut encore être confirmée.",
            )
        decision = PlanningDecisionV1(
            confirmed_sessions_per_week=confirmed_sessions_per_week,
            decision_source=(
                "accepted_recommendation"
                if recommended == confirmed_sessions_per_week
                else "user_override"
            ),
            recommendation_fingerprint=current.input_fingerprint,
            decision_context_fingerprint=decision_context_fingerprint(factual_runtime),
            confirmed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        target = Path(os.fspath(PLANNING_DECISION_FILE))
        with _target_lock(target):
            _atomic_write(target, decision, PlanningDecisionV1)
        return PlanningDecisionStateV1(
            status="available",
            decision_context_fingerprint=decision.decision_context_fingerprint,
            decision=decision,
            plan_constraints=build_plan_constraints_v1(decision, factual_runtime),
        )
