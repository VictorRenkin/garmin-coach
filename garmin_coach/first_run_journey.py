"""Server-authoritative orchestration for Motionest's first usable context."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from .config import user_context
from .garmin import stored_tokens_available
from .goal import goal_display_summary
from .goal_assessment import GoalAssessmentError, get_goal_assessment_state
from .history_import import load_history_import
from .onboarding import (
    LEGACY_COMPLETE_STATUSES,
    load_onboarding,
    record_journey_confirmation,
)
from .planning_decision import (
    PlanningDecisionError,
    get_planning_decision_state,
    get_planning_recommendation_state,
)
from .plan_acceptance import get_plan_acceptance_state
from .plan_proposal import PlanProposalError, get_plan_proposal_state
from .runtime_context import (
    RuntimeMotionestContextV1,
    build_runtime_motionest_context,
    load_motionest_inputs_v1,
)


FirstRunStage = Literal[
    "initial_intent",
    "connect_garmin",
    "importing",
    "import_failed",
    "review_garmin",
    "complete_declarations",
    "review_understanding",
    "goal_assessment",
    "planning_recommendation",
    "frequency_unavailable",
    "confirm_frequency",
    "ready_for_plan",
    "review_plan",
    "dashboard",
]


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _observations(runtime: RuntimeMotionestContextV1) -> dict[str, Any]:
    context = runtime.context
    athlete = context.athlete_state
    history = context.training_history_summary
    confidence = context.data_confidence
    return {
        "activity_count": history.activity_count,
        "sports_observed": athlete.sports_observed,
        "last_activity_date": athlete.last_activity_date,
        "observed_sessions_per_week_last_28_days": (
            athlete.observed_sessions_per_week_last_28_days
        ),
        "running_sessions_last_28_days": athlete.running_sessions_last_28_days,
        "running_distance_last_28_days_km": (
            athlete.running_distance_last_28_days_km
        ),
        "history_status": history.status,
        "history_scope": history.scope,
        "covered_days": history.covered_days,
        "data_confidence": {
            "training_history": confidence.training_history,
            "sport_classification": confidence.sport_classification,
            "performance_evidence": confidence.performance_evidence,
        },
        "missing_data": confidence.missing_data_codes,
        "limitations": [
            issue.code
            for issue in context.integration.limitations
            if issue.source == "athlete_state"
            or issue.source.startswith("data_confidence.")
        ],
        "provenance": "measured_and_deterministic",
    }


def _declared(runtime: RuntimeMotionestContextV1) -> dict[str, Any]:
    context = runtime.context
    goal = context.goal
    display = goal_display_summary(goal) if goal is not None else None
    return {
        "goal": (
            {
                **goal.model_dump(mode="json"),
                "display": display.model_dump(mode="json") if display else None,
                "provenance": "declared",
            }
            if goal is not None
            else None
        ),
        "availability": (
            {
                **context.availability.model_dump(mode="json"),
                "provenance": "declared",
            }
            if context.availability is not None
            else None
        ),
        "subjective_context": {
            **context.subjective_context.model_dump(mode="json"),
            "provenance": "declared",
        },
    }


def _safe_assessment_state(
    user_id: str, runtime: RuntimeMotionestContextV1
) -> dict[str, Any]:
    try:
        return get_goal_assessment_state(
            user_id, runtime=runtime
        ).model_dump(mode="json")
    except GoalAssessmentError as exc:
        return {"status": "missing", "assessment": None, "diagnostics": [exc.code]}


def _safe_recommendation_state(
    user_id: str, runtime: RuntimeMotionestContextV1
) -> dict[str, Any]:
    try:
        return get_planning_recommendation_state(
            user_id, runtime=runtime
        ).model_dump(mode="json")
    except PlanningDecisionError as exc:
        return {
            "status": "missing",
            "recommendation": None,
            "diagnostics": [exc.code],
        }


def _safe_decision_state(
    user_id: str, runtime: RuntimeMotionestContextV1
) -> dict[str, Any]:
    try:
        return get_planning_decision_state(
            user_id, runtime=runtime
        ).model_dump(mode="json")
    except PlanningDecisionError as exc:
        return {
            "status": "missing",
            "decision": None,
            "plan_constraints": None,
            "diagnostics": [exc.code],
        }


def _safe_plan_state(user_id: str) -> dict[str, Any]:
    try:
        return get_plan_proposal_state(user_id).model_dump(mode="json")
    except PlanProposalError as exc:
        return {"status": "missing", "proposal": None, "diagnostics": [exc.code]}


def _safe_plan_acceptance_state(user_id: str) -> dict[str, Any]:
    try:
        return get_plan_acceptance_state(user_id).model_dump(mode="json")
    except PlanProposalError as exc:
        return {"status": "missing", "decision": None, "diagnostics": [exc.code]}


def build_first_run_journey(user_id: str) -> dict[str, Any]:
    """Derive one resumable first-run stage without network or AI calls."""
    with user_context(user_id):
        onboarding = load_onboarding()
        if str(onboarding.get("status") or "") in LEGACY_COMPLETE_STATUSES:
            return {
                "schema_version": 1,
                "stage": "dashboard",
                "legacy_continuation": True,
                "actions": [],
            }

        inputs = load_motionest_inputs_v1(user_id)
        if inputs.goal is None:
            return {
                "schema_version": 1,
                "stage": "initial_intent",
                "legacy_continuation": False,
                "actions": ["save_goal"],
            }
        if not stored_tokens_available():
            return {
                "schema_version": 1,
                "stage": "connect_garmin",
                "legacy_continuation": False,
                "actions": ["connect_garmin"],
            }

        setup = onboarding.get("garmin_setup")
        setup_status = str(setup.get("status") or "idle") if isinstance(setup, dict) else "idle"
        history_import = load_history_import()
        if setup_status == "error" or history_import.get("status") in {
            "interrupted",
            "error",
        }:
            return {
                "schema_version": 1,
                "stage": "import_failed",
                "legacy_continuation": False,
                "import": {
                    "status": history_import.get("status"),
                    "retryable": bool(history_import.get("retryable")),
                    "setup_status": setup_status,
                },
                "actions": ["retry_import"],
            }
        if setup_status != "analysis_ready":
            return {
                "schema_version": 1,
                "stage": "importing",
                "legacy_continuation": False,
                "import": {
                    "status": history_import.get("status"),
                    "setup_status": setup_status,
                },
                "actions": [],
            }

        runtime = build_runtime_motionest_context(user_id)
        observations = _observations(runtime)
        observations_fingerprint = _digest(observations)
        confirmations = onboarding.get("journey_confirmations")
        confirmations = confirmations if isinstance(confirmations, dict) else {}
        declared = _declared(runtime)
        base = {
            "schema_version": 1,
            "legacy_continuation": False,
            "import": {
                "status": history_import.get("status"),
                "setup_status": setup_status,
            },
            "observations": observations,
            "declarations": declared,
            "observations_fingerprint": observations_fingerprint,
        }
        if confirmations.get("observations_fingerprint") != observations_fingerprint:
            return {**base, "stage": "review_garmin", "actions": ["confirm_observations"]}

        subjective = runtime.context.subjective_context
        if (
            runtime.context.availability is None
            or subjective.pain_or_injury.status == "unknown"
        ):
            return {
                **base,
                "stage": "complete_declarations",
                "actions": ["save_availability", "save_subjective_context"],
            }

        understanding_fingerprint = _digest(
            {"observations": observations_fingerprint, "declarations": declared}
        )
        base["understanding_fingerprint"] = understanding_fingerprint
        if confirmations.get("understanding_fingerprint") != understanding_fingerprint:
            return {
                **base,
                "stage": "review_understanding",
                "actions": ["confirm_understanding"],
            }

        assessment = _safe_assessment_state(user_id, runtime)
        base["goal_assessment"] = assessment
        if assessment.get("status") != "available":
            return {
                **base,
                "stage": "goal_assessment",
                "actions": ["generate_goal_assessment"],
            }

        recommendation = _safe_recommendation_state(user_id, runtime)
        base["planning_recommendation"] = recommendation
        if recommendation.get("status") != "available":
            return {
                **base,
                "stage": "planning_recommendation",
                "actions": ["generate_planning_recommendation"],
            }

        recommendation_value = recommendation.get("recommendation")
        recommended_sessions = (
            recommendation_value.get("recommended_sessions_per_week")
            if isinstance(recommendation_value, dict)
            else None
        )
        if (
            not isinstance(recommendation_value, dict)
            or recommendation_value.get("status") != "recommended"
            or isinstance(recommended_sessions, bool)
            or not isinstance(recommended_sessions, int)
        ):
            return {
                **base,
                "stage": "frequency_unavailable",
                "actions": ["edit_declarations"],
            }

        decision = _safe_decision_state(user_id, runtime)
        base["planning_decision"] = decision
        if decision.get("status") != "available":
            return {
                **base,
                "stage": "confirm_frequency",
                "actions": ["confirm_frequency"],
            }
        proposal = _safe_plan_state(user_id)
        base["plan_proposal"] = proposal
        if proposal.get("status") != "available":
            return {
                **base,
                "stage": "ready_for_plan",
                "actions": ["generate_plan_proposal"],
            }
        acceptance = _safe_plan_acceptance_state(user_id)
        base["plan_acceptance"] = acceptance
        if acceptance.get("status") in {"accepted", "partially_accepted"}:
            return {**base, "stage": "dashboard", "actions": []}
        return {
            **base,
            "stage": "review_plan",
            "actions": ["accept_plan", "request_plan_revision"],
        }


def confirm_first_run_step(user_id: str, step: str) -> dict[str, Any]:
    """Confirm only the currently expected factual review step."""
    current = build_first_run_journey(user_id)
    expected = {
        "observations": ("review_garmin", "observations_fingerprint"),
        "understanding": ("review_understanding", "understanding_fingerprint"),
    }
    if step not in expected:
        raise ValueError("Étape de confirmation inconnue.")
    required_stage, fingerprint_field = expected[step]
    if current.get("stage") != required_stage:
        raise ValueError("Cette étape n'est pas prête à être confirmée.")
    fingerprint = str(current.get(fingerprint_field) or "")
    if not fingerprint:
        raise ValueError("Empreinte de confirmation indisponible.")
    with user_context(user_id):
        record_journey_confirmation(fingerprint_field, fingerprint)
    return build_first_run_journey(user_id)
