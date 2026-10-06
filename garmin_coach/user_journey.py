"""Détermination centralisée de l'étape du parcours utilisateur Motionest."""
from __future__ import annotations

from typing import Literal

from .config import user_context
from .garmin import stored_tokens_available
from .first_run_journey import build_first_run_journey
from .onboarding import load_onboarding
from .user_accounts import list_user_records


UserJourneyStage = Literal[
    "verify_email",
    "onboarding",
    "connect_garmin",
    "importing",
    "import_failed",
    "review_garmin",
    "complete_declarations",
    "review_understanding",
    "goal_assessment",
    "planning_recommendation",
    "confirm_frequency",
    "ready_for_plan",
    "dashboard",
]


def determine_user_stage(user_id: str) -> UserJourneyStage:
    """Retourne l'étape actuelle d'un utilisateur Motionest authentifié.

    Les données absentes ou illisibles ramènent toujours vers l'étape sûre la
    plus ancienne. Le statut récapitulatif ``onboarding_complete`` du compte
    n'est volontairement pas utilisé : le fichier d'onboarding propre à
    l'utilisateur est la source de vérité.
    """
    account = next(
        (
            record
            for record in list_user_records()
            if str(record.get("id") or "") == user_id
        ),
        None,
    )
    if account is None or account.get("email_verified") is not True:
        return "verify_email"

    with user_context(user_id):
        onboarding = load_onboarding()
        status = str(onboarding.get("status") or "not_started")
        if status not in {
            "ready",
            "ready_for_garmin",
            "waiting_existing_plan",
            "plan_proposed",
            "complete",
        }:
            return "onboarding"
        if not stored_tokens_available():
            return "connect_garmin"
        setup = onboarding.get("garmin_setup")
        setup_status = str(setup.get("status") or "") if isinstance(setup, dict) else ""
        if status in {"waiting_existing_plan", "plan_proposed", "complete"}:
            return "dashboard"
        if onboarding.get("onboarding_version") != 2:
            return (
                "dashboard"
                if setup_status in {"analysis_ready", "plan_rejected"}
                else "connect_garmin"
            )
        if setup_status == "plan_rejected":
            return "dashboard"
        journey_stage = build_first_run_journey(user_id).get("stage")
        if journey_stage == "initial_intent":
            return "onboarding"
        if journey_stage in UserJourneyStage.__args__:
            return journey_stage  # type: ignore[return-value]
        return "onboarding"
