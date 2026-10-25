"""Canonical PlanGenerationInput V2, PlanProposal V2 and local validation.

The provider proposes a plan.  This module is the local authority that checks
the proposal and persists it.  It has no Garmin dependency and never publishes
or accepts a plan.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .session_limits import MAX_SESSION_MINUTES, MAX_SESSION_DISTANCE_KM, MAX_BLOCK_REPETITIONS, MAX_EFFORT_MINUTES
from .session_blocks import SessionBlock, validate_blocks_duration
from . import ai_provider
from .athlete_memory import decision_memory_context
from .config import (
    PLAN_PROPOSAL_FILE,
    Settings,
    ensure_directories,
    load_settings,
    user_context,
)
from .goal import GoalIntent, RaceGoal
from .goal_assessment import GoalAssessmentStateV1, get_goal_assessment_state
from .planning_decision import (
    PlanConstraintsV1,
    PlanningDecisionStateV1,
    PlanningRecommendationStateV1,
    get_planning_decision_state,
    get_planning_recommendation_state,
)
from .runtime_context import RuntimeMotionestContextV1, build_runtime_motionest_context
from .subjective_context import UnrecordedActivity


SCHEMA_VERSION = 2
PROMPT_VERSION = 1
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WRITE_LOCK = threading.RLock()
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_SECRET = re.compile(r"(?i)\b(api[_ -]?key|token|cookie|password|mot de passe)\b")

SYSTEM_PROMPT_V2 = """Tu proposes un plan sportif d'une semaine uniquement à partir de l'entrée structurée fournie.
Le nombre de séances doit être exactement plan_constraints.confirmed_sessions_per_week.
Utilise uniquement allowed_dates. Une séance par date par défaut. Une double journée
est possible uniquement sur plan_constraints.double_session_days explicitement déclarés,
avec repos minimal et plafond quotidien déclarés ; jamais pour beginner/comeback,
une douleur affectant l'entraînement ou une récupération insuffisamment documentée.
Dans un plan avec double journée, chaque séance a un session_id distinct et stable,
et starts_at au format ISO avec offset. Ordonne les séances chronologiquement.
Respecte le repos minimal entre la fin d'une séance et le début de la suivante,
y compris le jour suivant. Pas de séance traversant minuit, ni de date dans blocked_dates.
Le plafond reste sept séances par semaine et deux par date : ce sont des capacités,
pas une recommandation d'augmenter la fréquence. Respecte les contraintes déclarées.
N'invente aucune donnée personnelle, zone cardiaque, blessure, récupération ou performance.
Si les zones cardiaques ne sont pas fiables, exprime l'intensité qualitativement (aisance respiratoire, effort perçu).
La distance est optionnelle. N'ajoute pas de pseudo-précision si les faits sont limités.
Chaque séance doit avoir une structure concrète et interprétable pour une future conversion, sans publication Garmin.
Pour plusieurs blocs distincts, utilise structure.blocks dans leur ordre d'exécution ;
repetitions, effort_minutes et recovery_minutes de structure doivent alors être null.
Chaque bloc précise effort_minutes, repetitions, recovery_minutes et intensity_instruction.
La récupération est exécutée après chaque effort, y compris le dernier. Les fractions
0.5, 0.75 et 1.5 minute correspondent exactement à 30, 45 et 90 secondes.
La durée totale est échauffement + somme des répétitions × (effort + récupération)
+ retour au calme ; duration_minutes en est l'arrondi à la minute. Le cumul des
répétitions de tous les blocs ne dépasse pas 100, la séance ne dépasse pas 480 minutes.
N'invente aucune zone FC ; sans cible explicitement fondée, les consignes restent qualitatives.
Les blocs imbriqués, cibles en distance ou allure numérique ne sont pas représentables :
ne les remplace pas silencieusement par un bloc homogène ou une autre cible.
Une gêne légère est un contexte d'adaptation, pas une interdiction automatique. Une contrainte medical_no_running interdit la course.
Le résumé, la stratégie, les objectifs et les explications sont destinés à l'utilisateur : n'y affiche jamais
un path, un code de limitation ou du jargon interne. Décris les périodes concrètement, par exemple
« aucune course enregistrée ces 4 dernières semaines » plutôt que « activité récente insuffisante ».
Retourne uniquement le contrat PlanProposalV2 demandé."""


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PlanPeriodV2(StrictContract):
    start: date
    end: date

    @model_validator(mode="after")
    def validate_exact_week(self) -> "PlanPeriodV2":
        if self.end != self.start + timedelta(days=6):
            raise ValueError(
                "PlanPeriodV2 doit couvrir exactement sept jours calendaires inclusifs."
            )
        return self


class GoalAssessmentForPlanV2(StrictContract):
    assessment: str
    confidence: Literal["low", "medium", "high"]
    summary: str
    uncertainties: list[str]
    caveats: list[str]


class CanonicalGoalForPlanV2(StrictContract):
    intent: GoalIntent
    race: RaceGoal | None


class DeclaredPlanContextV2(StrictContract):
    category: Literal["constraint", "current_signal", "unrecorded_activity", "preference"]
    key: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=300)
    scope: Literal["durable", "current", "event"]


class PlanGenerationInputV2(StrictContract):
    schema_version: Literal[2] = SCHEMA_VERSION
    prompt_version: Literal[1] = PROMPT_VERSION
    as_of: date
    period: PlanPeriodV2
    goal: CanonicalGoalForPlanV2
    goal_assessment: GoalAssessmentForPlanV2 | None
    plan_constraints: PlanConstraintsV1
    allowed_dates: list[date] = Field(min_length=1, max_length=7)
    training_facts: dict[str, Any]
    confidence: dict[
        str, Literal["low", "medium", "high", "none", "not_applicable"]
    ]
    limitations: list[str]
    unrecorded_activities: list[UnrecordedActivity]
    active_declared_context: list[DeclaredPlanContextV2]
    important_preferences: list[str]
    blocked_dates: list[date] = Field(default_factory=list, max_length=7)


def _normalized(value: str, maximum: int) -> str:
    clean = " ".join(value.strip().split())
    if not clean or clean != value or len(clean) > maximum:
        raise ValueError("Texte de plan invalide ou non normalisé.")
    return clean


class PlanSessionStructureV2(StrictContract):
    warmup_minutes: int | None = Field(default=None, ge=0, le=120)
    main_set: str = Field(min_length=1, max_length=600)
    repetitions: int | None = Field(default=None, ge=1, le=MAX_BLOCK_REPETITIONS)
    effort_minutes: float | None = Field(default=None, gt=0, le=MAX_EFFORT_MINUTES)
    recovery_minutes: float | None = Field(default=None, ge=0, le=120)
    cooldown_minutes: int | None = Field(default=None, ge=0, le=120)
    blocks: list[SessionBlock] | None = Field(default=None, min_length=1, max_length=MAX_BLOCK_REPETITIONS)

    @model_validator(mode="after")
    def exclusive_structure(self) -> "PlanSessionStructureV2":
        if self.blocks is not None and any(value is not None for value in (self.repetitions, self.effort_minutes, self.recovery_minutes)):
            raise ValueError("Les blocs distincts et les champs du bloc homogène sont exclusifs.")
        return self

    @field_validator("main_set")
    @classmethod
    def validate_main_set(cls, value: str) -> str:
        return _normalized(value, 600)


class PlanSessionV2(StrictContract):
    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    starts_at: datetime | None = None
    date: date
    sport: Literal["running", "cycling", "swimming", "strength", "cross_training", "other"]
    session_type: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    objective: str = Field(min_length=1, max_length=240)
    duration_minutes: int = Field(gt=0, le=MAX_SESSION_MINUTES)
    distance_km: float | None = Field(default=None, gt=0, le=MAX_SESSION_DISTANCE_KM)
    intensity_instruction: str = Field(min_length=1, max_length=400)
    structure: PlanSessionStructureV2
    explanation: str = Field(min_length=1, max_length=500)
    limitations: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_complex_duration(self) -> "PlanSessionV2":
        if self.starts_at is not None:
            if self.session_id is None or self.starts_at.utcoffset() is None:
                raise ValueError("Un horaire exige une identité de séance et un offset explicites.")
            if self.starts_at.date() != self.date:
                raise ValueError("La date locale de starts_at doit être celle de la séance.")
        if self.structure.blocks is not None:
            if self.sport != "running":
                raise ValueError("Les blocs structurés distincts sont actuellement réservés à la course.")
            validate_blocks_duration(self.structure.blocks, self.structure.warmup_minutes or 0,
                                     self.structure.cooldown_minutes or 0, self.duration_minutes)
        return self

    @field_validator("objective", "intensity_instruction", "explanation")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _normalized(value, 500)


class PlanProposalV2(StrictContract):
    schema_version: Literal[2] = SCHEMA_VERSION
    prompt_version: Literal[1] = PROMPT_VERSION
    period: PlanPeriodV2
    summary: str = Field(min_length=1, max_length=500)
    strategy: str = Field(min_length=1, max_length=1_000)
    sessions: list[PlanSessionV2] = Field(min_length=1, max_length=7)
    limitations: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_identities(self) -> "PlanProposalV2":
        identities = [session.session_id for session in self.sessions if session.session_id is not None]
        if identities and len(identities) != len(self.sessions):
            raise ValueError("Une proposition identifiée exige une identité pour chaque séance.")
        if len(identities) != len(set(identities)):
            raise ValueError("Les identifiants de séance doivent être distincts.")
        return self

    @field_validator("summary", "strategy")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _normalized(value, 1_000)


class PlanValidationIssue(StrictContract):
    code: str
    path: str
    message: str


class PlanValidationResult(StrictContract):
    valid: bool
    errors: list[PlanValidationIssue]


PlanProposalStatus = Literal["missing", "available", "invalid", "stale", "generation_failed"]


class PlanProposalStoreV2(StrictContract):
    schema_version: Literal[2] = SCHEMA_VERSION
    prompt_version: Literal[1] = PROMPT_VERSION
    prompt_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["available", "invalid", "generation_failed"]
    proposal: PlanProposalV2 | None
    validation_errors: list[PlanValidationIssue]
    diagnostics: list[str]
    generated_at: str
    provider: str | None
    model: str | None


class PlanProposalStateV2(StrictContract):
    status: PlanProposalStatus
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposal: PlanProposalV2 | None = None
    cache_hit: bool = False
    validation_errors: list[PlanValidationIssue] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None


class PlanProposalError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _prompt_digest() -> str:
    return hashlib.sha256(SYSTEM_PROMPT_V2.encode("utf-8")).hexdigest()


def _safe_text(value: object) -> str | None:
    clean = " ".join(str(value or "").strip().split())
    if not clean or _EMAIL.search(clean) or _SECRET.search(clean):
        return None
    return clean[:300]


def _next_monday(reference: date) -> date:
    return reference + timedelta(days=(7 - reference.weekday()) % 7 or 7)


def local_calendar_blocked_dates(start: date, end: date) -> list[date]:
    """Reuse local metadata and existing legacy plan files; expose dates only."""
    from .config import PLANS_DIR
    from .local_activity_history import iter_local_activity_history
    days: set[date] = set()
    for item in iter_local_activity_history(start_date=start, end_date=end):
        try:
            day = date.fromisoformat(str(item.get("startTimeLocal") or "")[:10])
        except ValueError:
            continue
        days.add(day)
    for target in PLANS_DIR.glob("*.json"):
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            sessions = payload.get("seances", [])
            if not isinstance(sessions, list):
                raise ValueError("Invalid legacy calendar")
            for item in sessions:
                if not isinstance(item, dict) or item.get("statut") == "skippee":
                    continue
                day = date.fromisoformat(str(item.get("date") or ""))
                if start <= day <= end:
                    days.add(day)
        except (OSError, UnicodeDecodeError, ValueError, AttributeError) as exc:
            raise PlanProposalError("calendar_unavailable", "Un plan local ne permet pas de vérifier le calendrier.") from exc
    return sorted(days)


def _compact_training_facts(runtime: RuntimeMotionestContextV1) -> dict[str, Any]:
    athlete = runtime.context.athlete_state
    pattern = runtime.training_pattern
    return {
        "athlete_state_status": athlete.status,
        "sports_observed": athlete.sports_observed,
        "observed_sessions_per_week_last_28_days": athlete.observed_sessions_per_week_last_28_days,
        "running_sessions_last_28_days": athlete.running_sessions_last_28_days,
        "running_distance_last_28_days_km": athlete.running_distance_last_28_days_km,
        "recent_changes": athlete.recent_change_directions,
        "pattern_coverage": pattern.pattern_coverage.model_dump(mode="json"),
        "structured_intervals": pattern.session_patterns.structured_intervals.model_dump(mode="json"),
        "relative_long_run": pattern.session_patterns.relative_long_run.model_dump(mode="json"),
        "race_evidence": pattern.session_patterns.race.model_dump(mode="json"),
        "pain_affecting_training": runtime.context.integration.safety_context.pain_or_injury_affecting_training,
    }


def _assessment_for_plan(state: GoalAssessmentStateV1 | None) -> GoalAssessmentForPlanV2 | None:
    if state is None or state.assessment is None:
        return None
    value = state.assessment
    return GoalAssessmentForPlanV2(
        assessment=value.assessment,
        confidence=value.confidence,
        summary=value.summary,
        uncertainties=value.uncertainties,
        caveats=value.caveats,
    )


def build_plan_generation_input_v2(
    user_id: str,
    runtime: RuntimeMotionestContextV1,
    *,
    recommendation_state: PlanningRecommendationStateV1 | None = None,
    decision_state: PlanningDecisionStateV1 | None = None,
    goal_assessment_state: GoalAssessmentStateV1 | None = None,
    period_start: date | None = None,
) -> PlanGenerationInputV2:
    goal = runtime.context.goal
    if goal is None:
        raise PlanProposalError("goal_unavailable", "Goal V1 valide requis.")
    if runtime.context.integration.blocking_issues:
        raise PlanProposalError("context_unavailable", "Le contexte Motionest contient un blocage technique.")
    assessment_state = goal_assessment_state
    if goal.intent == "race":
        assessment_state = assessment_state or get_goal_assessment_state(user_id, runtime=runtime)
        if assessment_state.status != "available" or assessment_state.assessment is None:
            raise PlanProposalError("goal_assessment_unavailable", "GoalAssessment actuel requis pour une course.")
    recommendation = recommendation_state or get_planning_recommendation_state(
        user_id, runtime=runtime, goal_assessment_state=assessment_state
    )
    if recommendation.status != "available" or recommendation.recommendation is None:
        raise PlanProposalError("recommendation_unavailable", "PlanningRecommendation disponible requise.")
    decision = decision_state or get_planning_decision_state(user_id, runtime=runtime)
    if decision.status != "available" or decision.decision is None or decision.plan_constraints is None:
        raise PlanProposalError("planning_decision_unavailable", "PlanningDecision valide requis.")
    if decision.decision.recommendation_fingerprint != recommendation.input_fingerprint:
        raise PlanProposalError("planning_decision_stale", "PlanningDecision ne correspond plus à la recommandation actuelle.")
    constraints = decision.plan_constraints
    start = period_start or _next_monday(date.fromisoformat(runtime.reference_date))
    period = PlanPeriodV2(start=start, end=start + timedelta(days=6))
    allowed = [start + timedelta(days=i) for i in range(7) if DAY_NAMES[(start + timedelta(days=i)).weekday()] in constraints.available_days]
    if len(allowed) + len(constraints.double_session_days) < constraints.confirmed_sessions_per_week:
        raise PlanProposalError("plan_constraints_invalid", "La fréquence confirmée dépasse les dates disponibles.")
    memory: list[DeclaredPlanContextV2] = []
    for item in decision_memory_context("plan_generation"):
        text = _safe_text(item.get("text"))
        key = _safe_text(item.get("key"))
        if text and key:
            memory.append(DeclaredPlanContextV2(category=item["category"], key=key[:100], text=text, scope=item["scope"]))
    subjective = runtime.context.subjective_context
    preferences = [safe for value in subjective.important_preferences if (safe := _safe_text(value))]
    confidence = runtime.context.data_confidence
    blocked = local_calendar_blocked_dates(start, period.end)
    return PlanGenerationInputV2(
        as_of=date.fromisoformat(runtime.reference_date),
        period=period,
        goal=CanonicalGoalForPlanV2(intent=goal.intent, race=goal.race),
        goal_assessment=_assessment_for_plan(assessment_state),
        plan_constraints=constraints,
        allowed_dates=allowed,
        training_facts=_compact_training_facts(runtime),
        confidence={
            "training_history": confidence.training_history,
            "current_state": confidence.current_state,
            "heart_rate_zones": confidence.heart_rate_zones,
            "training_load": confidence.training_load,
            "recovery": confidence.recovery,
        },
        limitations=sorted({*(item.code for item in runtime.context.integration.limitations), *runtime.training_pattern.limitations}),
        unrecorded_activities=subjective.unrecorded_activities,
        active_declared_context=memory,
        important_preferences=preferences,
        blocked_dates=blocked,
    )


def plan_generation_fingerprint(source: PlanGenerationInputV2) -> str:
    return _digest({"schema_version": SCHEMA_VERSION, "prompt_version": PROMPT_VERSION, "prompt_digest": _prompt_digest(), "input": source.model_dump(mode="json")})


def proposal_fingerprint(proposal: PlanProposalV2) -> str:
    """Identify the exact canonical proposal content, independently of its input."""
    payload = proposal.model_dump(mode="json")
    for session in payload["sessions"]:
        for field in ("session_id", "starts_at"):
            if session.get(field) is None:
                session.pop(field, None)
    return _digest(
        {
            "schema_version": proposal.schema_version,
            "prompt_version": proposal.prompt_version,
            "proposal": payload,
        }
    )


def session_fingerprint(session: PlanSessionV2) -> str:
    return _digest(session.model_dump(mode="json"))


def session_execution_seconds(session: PlanSessionV2) -> float:
    structure = session.structure
    if structure.blocks is not None:
        exact = validate_blocks_duration(structure.blocks, structure.warmup_minutes or 0,
                                         structure.cooldown_minutes or 0, session.duration_minutes)
    elif structure.effort_minutes is not None:
        exact = ((structure.warmup_minutes or 0) + (structure.cooldown_minutes or 0)
                 + (structure.repetitions or 1) * (structure.effort_minutes + (structure.recovery_minutes or 0))) * 60
    else:
        exact = session.duration_minutes * 60
    return max(exact, session.duration_minutes * 60)


def validate_plan_proposal(proposal: PlanProposalV2, source: PlanGenerationInputV2) -> PlanValidationResult:
    errors: list[PlanValidationIssue] = []
    def add(code: str, path: str, message: str) -> None:
        errors.append(PlanValidationIssue(code=code, path=path, message=message))
    if proposal.period != source.period:
        add("period_mismatch", "period", "La période proposée diffère de la période demandée.")
    expected = source.plan_constraints.confirmed_sessions_per_week
    if len(proposal.sessions) != expected:
        add("session_count_mismatch", "sessions", f"Le plan doit contenir exactement {expected} séances.")
    dates = [session.date for session in proposal.sessions]
    doubles = {day for day in dates if dates.count(day) > 1}
    constraints = source.plan_constraints
    for day in doubles:
        sessions = [session for session in proposal.sessions if session.date == day]
        if DAY_NAMES[day.weekday()] not in constraints.double_session_days:
            add("duplicate_session_date", "sessions.date", "Cette double journée n'est pas autorisée explicitement.")
        if len(sessions) > 2:
            add("daily_session_limit", "sessions.date", "Au plus deux séances par date autorisée.")
        if source.goal.intent in {"beginner", "comeback"}:
            add("double_day_inappropriate", "sessions", "Une double journée n'est pas proposée pour un débutant ou une reprise.")
        if source.training_facts.get("pain_affecting_training") is not False or source.confidence.get("recovery") not in {"medium", "high"}:
            add("double_day_safety_uncertain", "sessions", "Le contexte de douleur ou de récupération ne permet pas cette double journée.")
        if constraints.minimum_recovery_hours is None or constraints.max_daily_duration_minutes is None:
            add("double_day_constraints_missing", "sessions", "Précise le repos minimal et le volume quotidien maximal.")
        elif sum(session_execution_seconds(session) for session in sessions) > constraints.max_daily_duration_minutes * 60:
            add("daily_duration_limit", "sessions", "Le volume quotidien dépasse la limite déclarée.")
    if doubles:
        if any(session.session_id is None or session.starts_at is None for session in proposal.sessions):
            add("session_schedule_missing", "sessions", "Chaque séance du plan doit avoir une identité et un horaire avec offset.")
        else:
            chronological = sorted(proposal.sessions, key=lambda session: session.starts_at.astimezone(timezone.utc))
            if chronological != proposal.sessions:
                add("session_order_invalid", "sessions", "Les séances doivent suivre leur ordre chronologique réel.")
            for index, session in enumerate(chronological):
                end = session.starts_at + timedelta(seconds=session_execution_seconds(session))
                if end.date() != session.date:
                    add("session_crosses_midnight", "sessions", "La séance ne doit pas déborder sur une autre date.")
                if index:
                    previous = chronological[index - 1]
                    previous_end = previous.starts_at.astimezone(timezone.utc) + timedelta(seconds=session_execution_seconds(previous))
                    rest = (session.starts_at.astimezone(timezone.utc) - previous_end).total_seconds()
                    if constraints.minimum_recovery_hours is None or rest < constraints.minimum_recovery_hours * 3600:
                        add("insufficient_recovery", "sessions", "Les horaires ne respectent pas le repos minimal déclaré.")
    allowed = set(source.allowed_dates)
    for index, session in enumerate(proposal.sessions):
        if session.date not in allowed:
            add("unavailable_day", f"sessions.{index}.date", "La séance est placée sur un jour indisponible.")
        if session.date in source.blocked_dates:
            add("existing_calendar_conflict", f"sessions.{index}.date", "Une activité existante occupe déjà cette date.")
    hard_keys = {item.key for item in source.active_declared_context if item.category == "constraint"}
    if "medical_no_training" in hard_keys and proposal.sessions:
        add("medical_no_training", "sessions", "Une contrainte active interdit actuellement toute séance.")
    if "medical_no_running" in hard_keys:
        for index, session in enumerate(proposal.sessions):
            if session.sport == "running":
                add("medical_no_running", f"sessions.{index}.sport", "Une contrainte active interdit la course.")
    return PlanValidationResult(valid=not errors, errors=errors)


def _read_store() -> tuple[PlanProposalStoreV2 | None, list[str]]:
    target = Path(os.fspath(PLAN_PROPOSAL_FILE))
    if not target.is_file():
        return None, []
    try:
        return PlanProposalStoreV2.model_validate_json(target.read_text(encoding="utf-8")), []
    except (OSError, UnicodeDecodeError, ValueError, ValidationError):
        return None, ["plan_proposal_store_invalid"]


def _write_store(store: PlanProposalStoreV2) -> None:
    ensure_directories()
    target = Path(os.fspath(PLAN_PROPOSAL_FILE))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(store.model_dump_json(indent=2), encoding="utf-8")
    PlanProposalStoreV2.model_validate_json(temporary.read_text(encoding="utf-8"))
    os.replace(temporary, target)


def _state(source: PlanGenerationInputV2) -> PlanProposalStateV2:
    fingerprint = plan_generation_fingerprint(source)
    store, diagnostics = _read_store()
    if store is None:
        return PlanProposalStateV2(status="missing", input_fingerprint=fingerprint, diagnostics=diagnostics)
    if store.input_fingerprint != fingerprint or store.prompt_digest != _prompt_digest():
        return PlanProposalStateV2(status="stale", input_fingerprint=fingerprint, diagnostics=["plan_generation_input_changed"])
    return PlanProposalStateV2(status=store.status, input_fingerprint=fingerprint, proposal=store.proposal if store.status == "available" else None, validation_errors=store.validation_errors, diagnostics=store.diagnostics, provider=store.provider, model=store.model)


def get_plan_proposal_state(
    user_id: str, *, runtime: RuntimeMotionestContextV1 | None = None,
    recommendation_state: PlanningRecommendationStateV1 | None = None,
    decision_state: PlanningDecisionStateV1 | None = None,
    goal_assessment_state: GoalAssessmentStateV1 | None = None,
) -> PlanProposalStateV2:
    """Read local state only. This function never calls an AI provider."""
    with user_context(user_id):
        factual = runtime or build_runtime_motionest_context(user_id)
        source = build_plan_generation_input_v2(user_id, factual, recommendation_state=recommendation_state, decision_state=decision_state, goal_assessment_state=goal_assessment_state)
        return _state(source)


def generate_plan_proposal(
    user_id: str, *, runtime: RuntimeMotionestContextV1 | None = None,
    recommendation_state: PlanningRecommendationStateV1 | None = None,
    decision_state: PlanningDecisionStateV1 | None = None,
    goal_assessment_state: GoalAssessmentStateV1 | None = None,
    settings: Settings | None = None,
) -> PlanProposalStateV2:
    with user_context(user_id):
        factual = runtime or build_runtime_motionest_context(user_id)
        source = build_plan_generation_input_v2(user_id, factual, recommendation_state=recommendation_state, decision_state=decision_state, goal_assessment_state=goal_assessment_state)
        fingerprint = plan_generation_fingerprint(source)
        with _WRITE_LOCK:
            current = _state(source)
            if current.status == "available":
                return current.model_copy(update={"cache_hit": True})
            resolved = settings or load_settings()
            if not resolved.ai_api_key:
                raise PlanProposalError("ai_not_configured", "Aucune clé API d'IA n'est configurée.")
            provider_model = ai_provider.routed_openai_model(resolved, "deep")
            try:
                proposal = ai_provider.structured_completion(
                    resolved,
                    instructions=SYSTEM_PROMPT_V2,
                    payload=source.model_dump(mode="json"),
                    result_type=PlanProposalV2,
                    reasoning_effort="low",
                    category="plan_proposal_v2",
                    max_output_tokens=3_000,
                    quality="deep",
                    timeout_seconds=90,
                    max_retries=0,
                )
                validation = validate_plan_proposal(proposal, source)
                status: Literal["available", "invalid", "generation_failed"] = "available" if validation.valid else "invalid"
                store = PlanProposalStoreV2(
                    prompt_digest=_prompt_digest(), input_fingerprint=fingerprint,
                    status=status, proposal=proposal if validation.valid else None,
                    validation_errors=validation.errors, diagnostics=[] if validation.valid else ["local_validation_failed"],
                    generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                    provider=resolved.ai_provider, model=provider_model,
                )
            except (ValidationError, ValueError, RuntimeError, TimeoutError) as error:
                store = PlanProposalStoreV2(
                    prompt_digest=_prompt_digest(), input_fingerprint=fingerprint,
                    status="generation_failed", proposal=None, validation_errors=[],
                    diagnostics=["provider_response_invalid" if isinstance(error, (ValidationError, ValueError)) else "provider_unavailable"],
                    generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                    provider=resolved.ai_provider, model=provider_model,
                )
            _write_store(store)
            return _state(source)


def preview_plan_session_revision(user_id: str, *, session_id: str, expected_proposal_fingerprint: str,
                        patch: dict[str, Any], source: PlanGenerationInputV2 | None = None) -> PlanProposalStateV2:
    """Validate one complete candidate without writing proposal or decisions."""
    with user_context(user_id), _WRITE_LOCK:
        source = source or build_plan_generation_input_v2(user_id, build_runtime_motionest_context(user_id))
        current = _state(source)
        if current.status != "available" or current.proposal is None:
            raise PlanProposalError("proposal_unavailable", "Aucune proposition actuelle à modifier.")
        if proposal_fingerprint(current.proposal) != expected_proposal_fingerprint:
            raise PlanProposalError("proposal_fingerprint_mismatch", "La proposition affichée a changé.")
        indexes = [index for index, item in enumerate(current.proposal.sessions) if item.session_id == session_id]
        if len(indexes) != 1:
            raise PlanProposalError("session_not_found", "Séance identifiée introuvable.")
        if not isinstance(patch, dict) or not patch or set(patch) - (set(PlanSessionV2.model_fields) - {"session_id"}):
            raise PlanProposalError("session_patch_invalid", "Le patch contient un champ non modifiable.")
        payload = current.proposal.model_dump(mode="json")
        index = indexes[0]
        payload["sessions"][index].update(patch)
        candidate = PlanProposalV2.model_validate(payload)
        validation = validate_plan_proposal(candidate, source)
        if not validation.valid:
            raise PlanProposalError("session_patch_invalid", validation.errors[0].message)
        return current.model_copy(update={"proposal": candidate})


def revise_plan_session(user_id: str, *, session_id: str, expected_proposal_fingerprint: str,
                        patch: dict[str, Any], source: PlanGenerationInputV2 | None = None) -> PlanProposalStateV2:
    """Revise exactly one session; serialize with delivery before the plan lock."""
    # Deferred import keeps the canonical contracts independent at import time.
    from .garmin_delivery import _WRITE_LOCK as delivery_lock
    with user_context(user_id), delivery_lock, _WRITE_LOCK:
        source = source or build_plan_generation_input_v2(user_id, build_runtime_motionest_context(user_id))
        candidate = preview_plan_session_revision(user_id, session_id=session_id,
            expected_proposal_fingerprint=expected_proposal_fingerprint, patch=patch, source=source)
        store, diagnostics = _read_store()
        if store is None or diagnostics:
            raise PlanProposalError("proposal_unavailable", "La proposition ne peut pas être sauvegardée.")
        _write_store(store.model_copy(update={"proposal": candidate.proposal}))
        return _state(source)
