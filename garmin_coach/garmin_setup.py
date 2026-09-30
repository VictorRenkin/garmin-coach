"""Orchestration asynchrone du premier import Garmin d'un utilisateur."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from .config import current_user_id, load_settings, user_context
from .garmin import connect
from .history_pagination import paginate_activity_history
from .multisport_details import import_multisport_details
from .onboarding import (
    inspect_garmin_account,
    load_onboarding,
    mark_plan_proposed,
    update_garmin_setup,
)
from .training_cycle import generate_initial_plan


LOGGER = logging.getLogger(__name__)
_LOCKS_GUARD = threading.Lock()
_USER_LOCKS: dict[str, threading.Lock] = {}
_RUNNING_USERS: set[str] = set()


def _user_lock(user_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _USER_LOCKS.setdefault(user_id, threading.Lock())


def setup_status() -> dict[str, Any]:
    """Retourne un état stable même avant le démarrage du setup."""
    setup = load_onboarding().get("garmin_setup")
    if isinstance(setup, dict):
        return setup
    return {
        "status": "idle",
        "message": "Garmin n'est pas encore connecté.",
        "error": "",
        "failed_step": "",
        "retryable": False,
    }


def run_initial_setup(user_id: str) -> None:
    """Importe, analyse et prépare un brouillon sans jamais le publier."""
    lock = _user_lock(user_id)
    if not lock.acquire(blocking=False):
        return
    try:
        with user_context(user_id):
            settings = load_settings()
            initial_status = str(load_onboarding().get("status") or "")
            step = "importing"
            try:
                update_garmin_setup(step, message="Importation de tes activités…")
                client = connect(settings)
                paginate_activity_history(client)
                update_garmin_setup(
                    step, message="Importation des données sportives détaillées…"
                )
                import_multisport_details(client)

                step = "analyzing"
                update_garmin_setup(step, message="Analyse de ton historique…")
                onboarding = inspect_garmin_account(
                    client, force=True, preserve_status=True
                )

                if initial_status in {
                    "waiting_existing_plan",
                    "plan_proposed",
                    "complete",
                }:
                    update_garmin_setup(
                        "complete", message="Garmin est reconnecté et à jour."
                    )
                    return

                step = "preparing_plan"
                update_garmin_setup(step, message="Préparation de ton plan…")
                target = generate_initial_plan(settings, onboarding)
                mark_plan_proposed(str(target))
                update_garmin_setup(
                    "plan_proposed", message="Ton plan est prêt à être vérifié."
                )
            except Exception as exc:
                LOGGER.exception("Échec du setup Garmin initial pour %s", user_id)
                update_garmin_setup(
                    "error",
                    message="Le setup Garmin a été interrompu.",
                    error=type(exc).__name__,
                    failed_step=step,
                    retryable=True,
                )
    finally:
        lock.release()


def start_initial_setup(
    user_id: str | None = None,
    *,
    thread_factory: Callable[..., threading.Thread] = threading.Thread,
) -> bool:
    """Lance le pipeline en arrière-plan; retourne False s'il tourne déjà."""
    resolved_user_id = user_id or current_user_id()
    if not resolved_user_id:
        raise ValueError("Utilisateur Motionest authentifié requis.")
    lock = _user_lock(resolved_user_id)
    with _LOCKS_GUARD:
        if resolved_user_id in _RUNNING_USERS or lock.locked():
            return False
        _RUNNING_USERS.add(resolved_user_id)

    def run_and_release() -> None:
        try:
            run_initial_setup(resolved_user_id)
        finally:
            with _LOCKS_GUARD:
                _RUNNING_USERS.discard(resolved_user_id)

    worker = thread_factory(
        target=run_and_release,
        daemon=True,
        name=f"garmin-setup-{resolved_user_id}",
    )
    try:
        worker.start()
    except Exception:
        with _LOCKS_GUARD:
            _RUNNING_USERS.discard(resolved_user_id)
        raise
    return True
