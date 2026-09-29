"""Détermination centralisée de l'étape du parcours utilisateur Motionest."""
from __future__ import annotations

from typing import Literal

from .config import user_context
from .garmin import stored_tokens_available
from .onboarding import load_onboarding
from .user_accounts import list_user_records


UserJourneyStage = Literal[
    "verify_email",
    "onboarding",
    "connect_garmin",
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
        if load_onboarding().get("status") != "complete":
            return "onboarding"
        if not stored_tokens_available():
            return "connect_garmin"
    return "dashboard"
