"""Grounded AI interpretation of Motionest's factual V1 contracts.

The local layer selects and fingerprints authoritative facts.  The provider may
interpret them, but it may neither recalculate metrics nor introduce evidence.
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

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import ai_provider
from .athlete_memory import decision_memory_context
from .config import (
    DATA_DIR,
    GOAL_ASSESSMENT_FILE,
    Settings,
    ensure_directories,
    load_settings,
    user_context,
)
from .runtime_context import RuntimeMotionestContextV1, build_runtime_motionest_context


SCHEMA_VERSION = 1
PROMPT_VERSION = 2
MAX_SUPPORTING_FACTS = 12
MAX_TEXT_ITEMS = 10
MAX_SUMMARY_LENGTH = 240
MAX_EXPLANATION_LENGTH = 1_000
MAX_ITEM_LENGTH = 240

AssessmentScope = Literal["timed_race", "event_completion", "not_applicable"]
Assessment = Literal[
    "realistic",
    "ambitious",
    "very_ambitious",
    "insufficient_evidence",
    "not_applicable",
]
AssessmentConfidence = Literal["low", "medium", "high"]
SupportingRole = Literal["supports", "challenges", "contextual"]
StoreStatus = Literal["available", "missing", "stale"]
GenerationSource = Literal["ai", "local_scope_rule"]

SYSTEM_PROMPT_V1 = """Tu évalues un objectif sportif uniquement à partir des faits Motionest fournis.
Les faits Motionest sont autoritaires : interprète-les sans recalculer de métrique et sans créer de preuve.
Riegel est une comparaison factuelle, jamais une prédiction ni une garantie.
Un record Garmin ou un fastest split n'est pas nécessairement une compétition.
relative_long_run décrit une position dans la distribution personnelle, pas l'intention de la séance.
structured_intervals provient d'une structure Garmin explicite.
L'absence de race evidence ne prouve pas l'absence de compétition réelle.
N'invente jamais Recovery, fatigue, douleur, blessure, kilométrage, chrono ou course passée.
Une donnée faible, partielle ou stale doit réduire la confiance et apparaître dans les incertitudes.
Pour une course sans date, évalue uniquement l'écart actuel : ne conclus rien sur la faisabilité à une échéance.
Une date de course absente doit apparaître comme incertitude ou réserve et interdit une confiance élevée artificielle.
very_ambitious ne signifie jamais impossible. Ne promets aucun résultat sportif.
Les supporting_facts doivent référencer exclusivement un path exact présent dans facts.
Si la valeur d'un fait est un objet, cite son path parent exact sans ajouter le nom d'un sous-champ.
N'invente jamais un path parent qui n'apparaît pas lui-même dans facts.
Dans les textes destinés à l'utilisateur, n'affiche jamais un path, un code de limitation ou du jargon interne.
Exprime les constats avec leur période concrète lorsqu'elle est disponible, par exemple
« aucune course enregistrée ces 4 dernières semaines » plutôt que « activité récente insuffisante ».
Si tu mentionnes un temps cible, convertis fidèlement goal_evidence.race.target_time_seconds ;
en cas de doute, ne reformule pas ce temps dans le texte.
L'explication doit être courte, utile et distinguer absence de preuve et preuve d'absence."""


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class GoalAssessmentInputV1(StrictContract):
    schema_version: Literal[1] = SCHEMA_VERSION
    prompt_version: Literal[2] = PROMPT_VERSION
    as_of: str
    assessment_scope: AssessmentScope
    goal_intent: Literal["progress", "race", "beginner", "comeback"]
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


class GoalAssessmentV1(StrictContract):
    schema_version: Literal[1] = SCHEMA_VERSION
    prompt_version: Literal[2] = PROMPT_VERSION
    assessment_scope: AssessmentScope
    assessment: Assessment
    confidence: AssessmentConfidence
    summary: str = Field(max_length=MAX_SUMMARY_LENGTH)
    explanation: str = Field(max_length=MAX_EXPLANATION_LENGTH)
    supporting_facts: list[SupportingFact] = Field(
        default_factory=list, max_length=MAX_SUPPORTING_FACTS
    )
    uncertainties: list[str] = Field(default_factory=list, max_length=MAX_TEXT_ITEMS)
    caveats: list[str] = Field(default_factory=list, max_length=MAX_TEXT_ITEMS)

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
        if len(set(normalized)) != len(normalized):
            raise ValueError("Les éléments doivent être uniques.")
        return normalized


class GoalAssessmentStoreV1(StrictContract):
    schema_version: Literal[1] = SCHEMA_VERSION
    prompt_version: Literal[2] = PROMPT_VERSION
    prompt_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    assessment: GoalAssessmentV1
    generated_at: str
    generation_source: GenerationSource
    provider: str | None = None
    model: str | None = None


class GoalAssessmentStateV1(StrictContract):
    status: StoreStatus
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    assessment: GoalAssessmentV1 | None = None
    cache_hit: bool = False
    generation_source: GenerationSource | None = None
    provider: str | None = None
    model: str | None = None
    diagnostics: list[str] = Field(default_factory=list)


class GoalAssessmentError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _GenerationLockEntry:
    lock: threading.Lock
    users: int = 0


_GENERATION_LOCKS_GUARD = threading.Lock()
_GENERATION_LOCKS: dict[str, _GenerationLockEntry] = {}


@contextmanager
def _generation_lock(store_key: str) -> Iterator[None]:
    """Serialize one user's generation without blocking other users.

    This is deliberately a mono-process duplicate-call guard. A multi-process
    deployment will require shared coordination outside this registry.
    """

    with _GENERATION_LOCKS_GUARD:
        entry = _GENERATION_LOCKS.get(store_key)
        if entry is None:
            entry = _GenerationLockEntry(lock=threading.Lock())
            _GENERATION_LOCKS[store_key] = entry
        entry.users += 1
    try:
        with entry.lock:
            yield
    finally:
        with _GENERATION_LOCKS_GUARD:
            entry.users -= 1
            if entry.users == 0 and _GENERATION_LOCKS.get(store_key) is entry:
                del _GENERATION_LOCKS[store_key]


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


def _scope(runtime: RuntimeMotionestContextV1) -> AssessmentScope:
    evidence = runtime.goal_evidence
    if evidence is None or evidence.goal_intent != "race":
        return "not_applicable"
    return (
        "timed_race"
        if evidence.race.target_time_seconds is not None
        else "event_completion"
    )


def _fact_map(runtime: RuntimeMotionestContextV1) -> dict[str, Any]:
    evidence = runtime.goal_evidence
    context = runtime.context
    pattern = runtime.training_pattern
    facts: dict[str, Any] = {
        "motionest_context.integration.status": context.integration.status,
        "motionest_context.integration.frequency_context": context.integration.frequency_context.model_dump(mode="json"),
        "motionest_context.integration.safety_context": context.integration.safety_context.model_dump(mode="json"),
        "motionest_context.integration.limitation_codes": [
            item.code for item in context.integration.limitations
        ],
        "motionest_context.athlete_state.recent_change_directions": context.athlete_state.recent_change_directions,
        "motionest_context.data_confidence.current_state": context.data_confidence.current_state,
        "motionest_context.data_confidence.recovery": context.data_confidence.recovery,
        "training_pattern.session_patterns.structured_intervals": pattern.session_patterns.structured_intervals.model_dump(mode="json"),
        "training_pattern.session_patterns.relative_long_run": pattern.session_patterns.relative_long_run.model_dump(mode="json"),
        "training_pattern.session_patterns.race": pattern.session_patterns.race.model_dump(mode="json"),
        "training_pattern.pattern_coverage": pattern.pattern_coverage.model_dump(mode="json"),
        "training_pattern.intensity_evidence": pattern.intensity_evidence.model_dump(mode="json"),
        "training_pattern.past_race_evidence": [
            item.model_dump(mode="json") for item in pattern.past_race_evidence
        ],
        "training_pattern.confidence": pattern.confidence.model_dump(mode="json"),
        "training_pattern.limitations": pattern.limitations,
        "conversation.active_declared_context": decision_memory_context(
            "goal_assessment"
        ),
    }
    if evidence is None:
        return facts
    training_support = evidence.training_support.model_dump(mode="json")
    evidence_confidence = evidence.confidence.model_dump(mode="json")
    facts.update(
        {
            "goal_evidence.goal_intent": evidence.goal_intent,
            "goal_evidence.status": evidence.status,
            "goal_evidence.race": evidence.race.model_dump(mode="json"),
            "goal_evidence.race.event_type": evidence.race.event_type,
            "goal_evidence.race.distance_km": evidence.race.distance_km,
            "goal_evidence.race.date": evidence.race.date,
            "goal_evidence.race.days_to_event": evidence.race.days_to_event,
            "goal_evidence.race.target_time_seconds": evidence.race.target_time_seconds,
            "goal_evidence.race.target_pace_seconds_per_km": evidence.race.target_pace_seconds_per_km,
            "goal_evidence.performance_comparison": evidence.performance_comparison.model_dump(mode="json"),
            "goal_evidence.performance_comparison.status": evidence.performance_comparison.status,
            "goal_evidence.performance_comparison.reference": (
                evidence.performance_comparison.reference.model_dump(mode="json")
                if evidence.performance_comparison.reference is not None
                else None
            ),
            "goal_evidence.performance_comparison.equivalent_target_distance_seconds": evidence.performance_comparison.equivalent_target_distance_seconds,
            "goal_evidence.performance_comparison.target_vs_equivalent_seconds": evidence.performance_comparison.target_vs_equivalent_seconds,
            "goal_evidence.performance_comparison.target_vs_equivalent_pct": evidence.performance_comparison.target_vs_equivalent_pct,
            "goal_evidence.performance_comparison.method": evidence.performance_comparison.method,
            **{
                f"goal_evidence.training_support.{key}": value
                for key, value in training_support.items()
            },
            **{
                f"goal_evidence.confidence.{key}": value
                for key, value in evidence_confidence.items()
            },
            "goal_evidence.limitations": evidence.limitations,
        }
    )
    return facts


def build_goal_assessment_input_v1(
    runtime: RuntimeMotionestContextV1,
) -> GoalAssessmentInputV1:
    intent = (
        runtime.goal_evidence.goal_intent
        if runtime.goal_evidence is not None
        else runtime.context.goal.intent
        if runtime.context.goal is not None
        else None
    )
    if intent is None:
        raise GoalAssessmentError(
            "goal_unavailable", "Aucun objectif structuré n'est disponible."
        )
    limitations = {
        *(runtime.goal_evidence.limitations if runtime.goal_evidence else []),
        *runtime.training_pattern.limitations,
        *(item.code for item in runtime.context.integration.limitations),
    }
    return GoalAssessmentInputV1(
        as_of=runtime.reference_date,
        assessment_scope=_scope(runtime),
        goal_intent=intent,
        facts=_fact_map(runtime),
        limitations=sorted(limitations),
    )


def goal_assessment_fingerprint(
    value: GoalAssessmentInputV1,
    *,
    prompt_version: int | None = None,
    system_prompt: str | None = None,
) -> str:
    resolved_prompt_version = PROMPT_VERSION if prompt_version is None else prompt_version
    if resolved_prompt_version < 1:
        raise ValueError("prompt_version doit être positif.")
    prompt_digest = hashlib.sha256(
        (SYSTEM_PROMPT_V1 if system_prompt is None else system_prompt).encode("utf-8")
    ).hexdigest()
    canonical = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "prompt_version": resolved_prompt_version,
            "prompt_digest": prompt_digest,
            "input": value.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _canonicalize_supporting_facts(
    value: GoalAssessmentV1, facts: dict[str, Any]
) -> GoalAssessmentV1:
    """Collapse a valid nested citation to its authoritative fact-map key.

    The AI occasionally cites ``parent.child`` when ``facts`` deliberately
    exposes ``parent`` as one compact object. Only an existing nested value may
    be collapsed; an invented path remains invalid and is rejected below.
    """
    normalized: list[SupportingFact] = []
    seen: set[str] = set()
    for supporting in value.supporting_facts:
        path = supporting.path
        if path not in facts:
            parents = sorted(
                (
                    key
                    for key, fact in facts.items()
                    if isinstance(fact, dict) and path.startswith(f"{key}.")
                ),
                key=len,
                reverse=True,
            )
            for parent in parents:
                nested: Any = facts[parent]
                for part in path[len(parent) + 1 :].split("."):
                    if not isinstance(nested, dict) or part not in nested:
                        break
                    nested = nested[part]
                else:
                    path = parent
                    break
        if path in seen:
            continue
        seen.add(path)
        normalized.append(SupportingFact(path=path, role=supporting.role))
    if normalized == value.supporting_facts:
        return value
    return value.model_copy(update={"supporting_facts": normalized})


def validate_goal_assessment(
    value: GoalAssessmentV1, source: GoalAssessmentInputV1
) -> GoalAssessmentV1:
    value = _canonicalize_supporting_facts(value, source.facts)
    if value.assessment_scope != source.assessment_scope:
        raise GoalAssessmentError(
            "assessment_scope_mismatch", "Le scope retourné ne correspond pas aux faits."
        )
    if source.assessment_scope == "not_applicable" and value.assessment != "not_applicable":
        raise GoalAssessmentError(
            "assessment_scope_incompatible",
            "Un objectif non-course exige assessment=not_applicable.",
        )
    if source.assessment_scope != "not_applicable" and value.assessment == "not_applicable":
        raise GoalAssessmentError(
            "assessment_scope_incompatible",
            "Une course ne peut pas recevoir assessment=not_applicable.",
        )
    if value.assessment == "insufficient_evidence" and value.confidence == "high":
        raise GoalAssessmentError(
            "confidence_incompatible",
            "Une preuve insuffisante ne peut pas avoir une confiance élevée.",
        )
    if (
        source.assessment_scope != "not_applicable"
        and source.facts.get("goal_evidence.race.date") is None
    ):
        if value.confidence == "high":
            raise GoalAssessmentError(
                "confidence_incompatible_missing_date",
                "Une course sans date ne peut pas avoir une confiance élevée.",
            )
        if not value.uncertainties and not value.caveats:
            raise GoalAssessmentError(
                "missing_date_uncertainty_required",
                "L'absence de date doit apparaître comme incertitude ou réserve.",
            )
    if not value.supporting_facts:
        raise GoalAssessmentError(
            "supporting_fact_required",
            "L'évaluation doit référencer au moins un fait autorisé.",
        )
    allowed = set(source.facts)
    unknown = sorted({item.path for item in value.supporting_facts} - allowed)
    if unknown:
        raise GoalAssessmentError(
            "unknown_supporting_fact",
            "La réponse référence un fait absent du payload autorisé.",
        )
    if len({item.path for item in value.supporting_facts}) != len(
        value.supporting_facts
    ):
        raise GoalAssessmentError(
            "duplicate_supporting_fact", "Une preuve ne peut être référencée deux fois."
        )
    return value


def _local_not_applicable(source: GoalAssessmentInputV1) -> GoalAssessmentV1:
    summaries = {
        "beginner": "Tu veux débuter. Le coach va maintenant te proposer un rythme de départ adapté à tes disponibilités et à ton historique.",
        "comeback": "Tu veux reprendre. Le coach va maintenant te proposer un rythme progressif adapté à tes disponibilités et à ton historique.",
        "progress": "Tu veux progresser sans objectif de course chronométré. Le coach va maintenant te proposer un rythme adapté à tes disponibilités et à ton historique.",
    }
    return GoalAssessmentV1(
        assessment_scope="not_applicable",
        assessment="not_applicable",
        confidence="high",
        summary=summaries[source.goal_intent],
        explanation="Aucune faisabilité chronométrique n'est évaluée pour cette intention ; cela ne bloque pas les futures étapes de planification.",
        supporting_facts=[
            SupportingFact(path="goal_evidence.goal_intent", role="contextual")
        ],
        uncertainties=[],
        caveats=["Aucune recommandation d'entraînement n'est produite à cette étape."],
    )


def _read_store() -> tuple[GoalAssessmentStoreV1 | None, list[str]]:
    target = Path(os.fspath(GOAL_ASSESSMENT_FILE))
    if not target.is_file():
        return None, []
    try:
        return GoalAssessmentStoreV1.model_validate_json(
            target.read_text(encoding="utf-8")
        ), []
    except (OSError, UnicodeDecodeError, ValueError, ValidationError):
        return None, ["goal_assessment_store_invalid"]


def _atomic_write_store(store: GoalAssessmentStoreV1) -> None:
    ensure_directories()
    target = Path(os.fspath(GOAL_ASSESSMENT_FILE))
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(store.model_dump(mode="json"), ensure_ascii=False, indent=2)
    try:
        temporary.write_text(encoded, encoding="utf-8")
        GoalAssessmentStoreV1.model_validate_json(
            temporary.read_text(encoding="utf-8")
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _write_ai_attempt(attempt_id: str, payload: dict[str, Any]) -> Path:
    """Persist a local, user-isolated trace before business validation.

    The trace contains only the controlled GoalAssessment payload and provider
    candidate. API keys, Garmin tokens, cookies and raw activity files never
    enter this diagnostic record.
    """
    directory = Path(os.fspath(DATA_DIR / "ai_diagnostics" / "goal_assessment"))
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{attempt_id}.json"
    # Keep the temporary name short enough for Windows test workspaces, whose
    # pytest prefix can otherwise bring an atomic-write path above MAX_PATH.
    temporary = directory / f".{uuid.uuid4().hex[:8]}.tmp"
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        temporary.write_text(encoded, encoding="utf-8")
        json.loads(temporary.read_text(encoding="utf-8"))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _current_state(source: GoalAssessmentInputV1) -> GoalAssessmentStateV1:
    fingerprint = goal_assessment_fingerprint(source)
    store, diagnostics = _read_store()
    if store is None:
        return GoalAssessmentStateV1(
            status="missing",
            input_fingerprint=fingerprint,
            diagnostics=diagnostics,
        )
    if store.input_fingerprint != fingerprint:
        return GoalAssessmentStateV1(
            status="stale",
            input_fingerprint=fingerprint,
            generation_source=store.generation_source,
            provider=store.provider,
            model=store.model,
        )
    try:
        validated = validate_goal_assessment(store.assessment, source)
    except GoalAssessmentError:
        return GoalAssessmentStateV1(
            status="missing",
            input_fingerprint=fingerprint,
            diagnostics=["goal_assessment_store_semantically_invalid"],
        )
    return GoalAssessmentStateV1(
        status="available",
        input_fingerprint=fingerprint,
        assessment=validated,
        cache_hit=True,
        generation_source=store.generation_source,
        provider=store.provider,
        model=store.model,
    )


def get_goal_assessment_state(
    user_id: str,
    *,
    runtime: RuntimeMotionestContextV1 | None = None,
) -> GoalAssessmentStateV1:
    with user_context(user_id):
        factual_runtime = runtime or build_runtime_motionest_context(user_id)
        source = build_goal_assessment_input_v1(factual_runtime)
        return _current_state(source)


def generate_goal_assessment(
    user_id: str,
    *,
    runtime: RuntimeMotionestContextV1 | None = None,
    settings: Settings | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> GoalAssessmentStateV1:
    measured = diagnostics if diagnostics is not None else {}
    started = time.perf_counter()
    with user_context(user_id):
        factual_runtime = runtime or build_runtime_motionest_context(user_id)
        source = build_goal_assessment_input_v1(factual_runtime)
        encoded_input = json.dumps(
            source.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = goal_assessment_fingerprint(source)
        prompt_digest = hashlib.sha256(SYSTEM_PROMPT_V1.encode("utf-8")).hexdigest()
        store_key = str(Path(os.fspath(GOAL_ASSESSMENT_FILE)).resolve())
        with _generation_lock(store_key):
            current = _current_state(source)
            if current.status == "available":
                measured.update(
                    {
                        "cache": "hit",
                        "input_bytes": len(encoded_input),
                        "local_seconds": round(time.perf_counter() - started, 6),
                    }
                )
                return current

            provider_name: str | None = None
            model_name: str | None = None
            if source.assessment_scope == "not_applicable":
                assessment = validate_goal_assessment(
                    _local_not_applicable(source), source
                )
                generation_source: GenerationSource = "local_scope_rule"
            else:
                resolved_settings = settings or load_settings()
                if not resolved_settings.ai_api_key:
                    raise GoalAssessmentError(
                        "ai_not_configured", "Aucune clé API d'IA n'est configurée."
                    )
                provider_name = resolved_settings.ai_provider
                model_name = ai_provider.routed_openai_model(
                    resolved_settings, "balanced"
                )
                attempt_id = (
                    datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f")[:-3]
                    + f"_{uuid.uuid4().hex[:6]}"
                )
                attempt: dict[str, Any] = {
                    "schema_version": 1,
                    "attempt_id": attempt_id,
                    "captured_at": datetime.now()
                    .astimezone()
                    .isoformat(timespec="seconds"),
                    "category": "goal_assessment",
                    "provider": provider_name,
                    "model": model_name,
                    "prompt_version": PROMPT_VERSION,
                    "prompt_digest": prompt_digest,
                    "input_fingerprint": fingerprint,
                    "input_payload": source.model_dump(mode="json"),
                    "allowed_supporting_fact_paths": sorted(source.facts),
                    "status": "requested",
                    "candidate_before_validation": None,
                    "candidate_after_safe_canonicalization": None,
                    "cited_supporting_fact_paths": [],
                    "unknown_supporting_fact_paths": [],
                    "validation": None,
                }
                _write_ai_attempt(attempt_id, attempt)
                try:
                    candidate = ai_provider.structured_completion(
                        resolved_settings,
                        instructions=SYSTEM_PROMPT_V1,
                        payload=source.model_dump(mode="json"),
                        result_type=GoalAssessmentV1,
                        reasoning_effort="medium",
                        category="goal_assessment",
                        max_output_tokens=1_000,
                        quality="balanced",
                    )
                except GoalAssessmentError as exc:
                    attempt.update(
                        {
                            "status": "provider_error",
                            "validation": {
                                "accepted": False,
                                "code": exc.code,
                                "message": str(exc),
                            },
                        }
                    )
                    _write_ai_attempt(attempt_id, attempt)
                    raise
                except Exception as exc:
                    if _is_invalid_provider_response(exc):
                        provider_error = GoalAssessmentError(
                            "invalid_provider_response",
                            "La réponse structurée de l'IA est invalide.",
                        )
                    else:
                        provider_error = GoalAssessmentError(
                            _provider_failure_code(exc),
                            "Le fournisseur d'IA est indisponible.",
                        )
                    attempt.update(
                        {
                            "status": "provider_error",
                            "validation": {
                                "accepted": False,
                                "code": provider_error.code,
                                "message": str(provider_error),
                            },
                        }
                    )
                    _write_ai_attempt(attempt_id, attempt)
                    raise provider_error from exc

                canonical_candidate = _canonicalize_supporting_facts(
                    candidate, source.facts
                )
                cited_paths = [
                    item.path for item in canonical_candidate.supporting_facts
                ]
                attempt.update(
                    {
                        "status": "received",
                        "candidate_before_validation": candidate.model_dump(mode="json"),
                        "candidate_after_safe_canonicalization": (
                            canonical_candidate.model_dump(mode="json")
                        ),
                        "cited_supporting_fact_paths": cited_paths,
                        "unknown_supporting_fact_paths": sorted(
                            set(cited_paths) - set(source.facts)
                        ),
                    }
                )
                _write_ai_attempt(attempt_id, attempt)
                try:
                    assessment = validate_goal_assessment(candidate, source)
                except GoalAssessmentError as exc:
                    attempt.update(
                        {
                            "status": "rejected",
                            "validation": {
                                "accepted": False,
                                "code": exc.code,
                                "message": str(exc),
                            },
                        }
                    )
                    _write_ai_attempt(attempt_id, attempt)
                    raise
                attempt.update(
                    {
                        "status": "accepted",
                        "validation": {
                            "accepted": True,
                            "code": "accepted",
                            "message": "La réponse respecte le contrat GoalAssessment V1.",
                        },
                    }
                )
                _write_ai_attempt(attempt_id, attempt)
                generation_source = "ai"

            store = GoalAssessmentStoreV1(
                prompt_digest=prompt_digest,
                input_fingerprint=fingerprint,
                assessment=assessment,
                generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                generation_source=generation_source,
                provider=provider_name,
                model=model_name,
            )
            _atomic_write_store(store)
            response_bytes = len(
                json.dumps(
                    assessment.model_dump(mode="json"),
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
            return GoalAssessmentStateV1(
                status="available",
                input_fingerprint=fingerprint,
                assessment=assessment,
                cache_hit=False,
                generation_source=generation_source,
                provider=provider_name,
                model=model_name,
            )
