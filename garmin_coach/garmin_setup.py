"""Orchestration asynchrone du premier import Garmin d'un utilisateur."""
from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

from .config import current_user_id, load_settings, user_context
from .garmin import build_worker_client_factory, connect
from .history_pagination import paginate_activity_history
from .history_import import load_history_import
from .multisport_details import (
    GlobalRequestController,
    configured_detail_queue_size,
    configured_detail_workers,
    import_multisport_details,
)
from .onboarding import (
    inspect_garmin_account,
    load_onboarding,
    update_garmin_setup,
)
from .runtime_context import build_runtime_motionest_context


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


def _stream_paginated_details(
    user_id: str, client: Any, client_factory: Callable[[], Any]
) -> dict[str, Any]:
    """Apply backpressure while metadata pagination feeds detail workers."""
    workers = configured_detail_workers()
    total_capacity = configured_detail_queue_size(workers)
    detail_capacity = max(workers, total_capacity - 1)
    controller = GlobalRequestController(workers)
    pending: queue.Queue[object] = queue.Queue(maxsize=1)
    ready = threading.Event()
    producer_done = threading.Event()
    producer_errors: list[BaseException] = []

    def publish(activity: dict[str, Any]) -> None:
        ready.set()
        while not controller.auth_stopped:
            try:
                pending.put(activity, timeout=0.05)
                return
            except queue.Full:
                continue

    def produce() -> None:
        try:
            with user_context(user_id):
                paginate_activity_history(client, on_activity_persisted=publish)
        except BaseException as exc:
            producer_errors.append(exc)
        finally:
            ready.set()
            producer_done.set()

    producer = threading.Thread(
        target=produce,
        daemon=True,
        name=f"garmin-pagination-{user_id}",
    )
    producer.start()
    ready.wait()

    def activities():
        while True:
            try:
                yield pending.get(timeout=0.05)
            except queue.Empty:
                if producer_done.is_set():
                    if producer_errors:
                        raise producer_errors[0]
                    return

    try:
        return import_multisport_details(
            client_factory=client_factory,
            worker_count=workers,
            queue_size=detail_capacity,
            controller=controller,
            activity_source=activities(),
            allow_incomplete_pagination=True,
        )
    finally:
        producer.join()


def run_initial_setup(user_id: str) -> None:
    """Importe et analyse localement; aucune IA ni génération de plan."""
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
                # Real Garmin clients expose garth. The fallback keeps small
                # injected test doubles compatible without weakening the
                # production rule of one isolated HTTP session per worker.
                worker_factory = (
                    build_worker_client_factory(client)
                    if hasattr(client, "garth")
                    else None
                )
                if worker_factory is None:
                    paginate_activity_history(client)
                    update_garmin_setup(
                        step,
                        message="Importation des données sportives détaillées…",
                    )
                    import_multisport_details(client)
                else:
                    update_garmin_setup(
                        step,
                        message="Importation des données sportives détaillées…",
                    )
                    if load_history_import(detect_stale=False).get(
                        "pagination_complete"
                    ):
                        import_multisport_details(client_factory=worker_factory)
                    else:
                        _stream_paginated_details(user_id, client, worker_factory)

                step = "analyzing"
                update_garmin_setup(step, message="Analyse de ton historique…")
                inspect_garmin_account(
                    client, force=True, preserve_status=True
                )
                build_runtime_motionest_context(user_id)

                if initial_status in {
                    "waiting_existing_plan",
                    "plan_proposed",
                    "complete",
                }:
                    update_garmin_setup(
                        "complete", message="Garmin est reconnecté et à jour."
                    )
                    return

                update_garmin_setup(
                    "analysis_ready",
                    message="Ton historique est prêt à être analysé par Motionest.",
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
