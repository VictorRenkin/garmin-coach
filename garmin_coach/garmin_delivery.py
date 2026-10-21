"""Explicit delivery of an accepted PlanProposal V2 session to Garmin.

Creating a workout, scheduling it in the calendar and syncing it to a watch are
separate facts.  This module persists each confirmed fact and never claims a
watch sync, which Garmin Connect does not expose reliably through this client.
``updated_at`` tracks changes to persisted delivery facts, not unchanged checks.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from garminconnect.workout import (
    RunningWorkout,
    WorkoutSegment,
    create_cooldown_step,
    create_interval_step,
    create_recovery_step,
    create_repeat_group,
    create_warmup_step,
)

from .config import GARMIN_DELIVERY_FILE, ensure_directories, user_context
from .garmin_workouts import RUNNING_SPORT, _calendar_entries
from .session_blocks import validate_blocks_duration
from .structured_workouts import build_block_steps
from .plan_acceptance import get_plan_acceptance_state
from .plan_proposal import (
    PlanProposalStateV2,
    PlanSessionV2,
    get_plan_proposal_state,
    proposal_fingerprint,
    session_fingerprint,
    local_calendar_blocked_dates,
)


SCHEMA_VERSION = 1
_WRITE_LOCK = threading.RLock()


class GarminDeliveryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_store() -> dict[str, Any]:
    target = Path(os.fspath(GARMIN_DELIVERY_FILE))
    if not target.is_file():
        return {"schema_version": SCHEMA_VERSION, "deliveries": {}}
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GarminDeliveryError(
            "delivery_store_invalid", "Le suivi des envois Garmin est illisible."
        ) from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != SCHEMA_VERSION
        or not isinstance(value.get("deliveries"), dict)
    ):
        raise GarminDeliveryError(
            "delivery_store_invalid", "Le suivi des envois Garmin est invalide."
        )
    return value


def _write_store(store: dict[str, Any]) -> None:
    ensure_directories()
    target = Path(os.fspath(GARMIN_DELIVERY_FILE))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        json.loads(temporary.read_text(encoding="utf-8"))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _accepted_proposal(user_id: str) -> tuple[PlanProposalStateV2, str]:
    proposal_state = get_plan_proposal_state(user_id)
    if proposal_state.status != "available" or proposal_state.proposal is None:
        raise GarminDeliveryError(
            "proposal_unavailable", "Aucune semaine actuelle n'est disponible."
        )
    acceptance = get_plan_acceptance_state(user_id, proposal_state=proposal_state)
    if not any(session.session_id for session in proposal_state.proposal.sessions) and acceptance.status != "accepted":
        raise GarminDeliveryError(
            "proposal_not_accepted", "Valide d'abord cette semaine dans Motionest."
        )
    return proposal_state, proposal_fingerprint(proposal_state.proposal)


def _delivery_key(fingerprint: str, session_index: int) -> str:
    return f"{fingerprint}:{session_index}"


def _session_delivery_key(fingerprint: str, session_index: int, session: PlanSessionV2) -> str:
    if session.session_id is not None:
        return f"session:{session.session_id}:{session_fingerprint(session)}"
    return _delivery_key(fingerprint, session_index)


def _checked_calendar_entries(client: Any, target_date: date) -> list[dict[str, Any]]:
    getter = getattr(client, "get_scheduled_workouts", None)
    try:
        payload = getter(target_date.year, target_date.month) if callable(getter) else client.connectapi(
            f"/calendar-service/year/{target_date.year}/month/{target_date.month - 1}")
        if not isinstance(payload, (dict, list)) or (isinstance(payload, dict) and not any(isinstance(payload.get(key), list) for key in ("calendarItems", "items", "calendarItemsList"))):
            raise ValueError("Unrecognized calendar response")
        entries = payload if isinstance(payload, list) else next(payload[key] for key in ("calendarItems", "items", "calendarItemsList") if isinstance(payload.get(key), list))
        for item in entries:
            if not isinstance(item, dict) or item.get("itemType") not in {"workout", "activity"}:
                raise ValueError("Unrecognized calendar entry")
            date.fromisoformat(_item_date(item) or "")
    except Exception as exc:
        raise GarminDeliveryError("calendar_unavailable", "Le calendrier n'est pas vérifiable ; aucun envoi n'est effectué.") from exc
    return entries


def _check_calendar_collisions(client: Any, target_date: date, session: PlanSessionV2,
                               proposal_state: PlanProposalStateV2, store: dict[str, Any], user_id: str, fingerprint: str) -> None:
    entries = _checked_calendar_entries(client, target_date)
    allowed: set[int] = set()
    assert proposal_state.proposal is not None
    for index, candidate in enumerate(proposal_state.proposal.sessions):
        record = store["deliveries"].get(_session_delivery_key(fingerprint, index, candidate)) or {}
        if record.get("scheduled_date") == target_date.isoformat() and record.get("workout_id"):
            if candidate.session_id is None or get_plan_acceptance_state(user_id, proposal_state=proposal_state, session_id=candidate.session_id).status == "accepted":
                allowed.add(int(record["workout_id"]))
    for item in entries:
        day = _item_date(item)
        if day is None:
            raise GarminDeliveryError("calendar_unavailable", "Un événement sans date empêche de vérifier les collisions.")
        if day != target_date.isoformat():
            continue
        try:
            owned = int(item.get("workoutId") or 0) in allowed
        except (ValueError, TypeError):
            owned = False
        if not owned:
            raise GarminDeliveryError("calendar_collision", "Une activité ou programmation existante occupe déjà cette date.")
    try:
        local_conflict = target_date in local_calendar_blocked_dates(target_date, target_date)
    except Exception as exc:
        raise GarminDeliveryError("calendar_unavailable", "Le calendrier local n'est pas vérifiable.") from exc
    if local_conflict:
        raise GarminDeliveryError("calendar_collision", "Une activité locale existe déjà à cette date.")


def _workout_name(session: PlanSessionV2) -> str:
    return f"MOT {session.date.strftime('%d%m')} RUN"[:15]


def build_plan_session_workout(session: PlanSessionV2) -> RunningWorkout:
    """Convert one factual running session without inventing targets."""
    if session.sport != "running":
        raise GarminDeliveryError(
            "sport_not_supported",
            "L'export Garmin structuré est actuellement disponible pour la course.",
        )
    structure = session.structure
    warmup_seconds = int((structure.warmup_minutes or 0) * 60)
    cooldown_seconds = int((structure.cooldown_minutes or 0) * 60)
    if structure.blocks is not None:
        # Revalidate at the conversion boundary as well (model_copy is not validating).
        session = PlanSessionV2.model_validate(session.model_dump())
        structure = session.structure
        warmup_seconds = int((structure.warmup_minutes or 0) * 60)
        cooldown_seconds = int((structure.cooldown_minutes or 0) * 60)
        structured_seconds = validate_blocks_duration(structure.blocks, structure.warmup_minutes or 0,
                                                       structure.cooldown_minutes or 0, session.duration_minutes)
        steps: list[Any] = []
        if warmup_seconds:
            steps.append(create_warmup_step(warmup_seconds, step_order=1))
        steps.extend(build_block_steps(structure.blocks, len(steps) + 1))
        if cooldown_seconds:
            steps.append(create_cooldown_step(cooldown_seconds, step_order=len(steps) + 1))
        return RunningWorkout(workoutName=_workout_name(session), estimatedDurationInSecs=structured_seconds,
                              workoutSegments=[WorkoutSegment(segmentOrder=1, sportType=RUNNING_SPORT, workoutSteps=steps)])
    repetitions = structure.repetitions or 1
    recovery_seconds = int((structure.recovery_minutes or 0) * 60)
    if structure.effort_minutes is not None:
        effort_seconds = int(round(structure.effort_minutes * 60))
    else:
        available = session.duration_minutes * 60 - warmup_seconds - cooldown_seconds
        effort_seconds = available // repetitions - recovery_seconds
    if effort_seconds <= 0:
        raise GarminDeliveryError(
            "session_structure_invalid", "La durée du bloc principal est invalide."
        )
    structured_seconds = (
        warmup_seconds
        + repetitions * (effort_seconds + recovery_seconds)
        + cooldown_seconds
    )
    if abs(structured_seconds - session.duration_minutes * 60) > 60:
        raise GarminDeliveryError(
            "session_duration_mismatch",
            "La durée annoncée ne correspond pas aux blocs de la séance.",
        )

    steps: list[Any] = []
    order = 1
    if warmup_seconds:
        steps.append(create_warmup_step(warmup_seconds, step_order=order))
        order += 1
    if repetitions > 1:
        repeated: list[Any] = [create_interval_step(effort_seconds, step_order=1)]
        if recovery_seconds:
            repeated.append(create_recovery_step(recovery_seconds, step_order=2))
        steps.append(create_repeat_group(repetitions, repeated, step_order=order))
    else:
        steps.append(create_interval_step(effort_seconds, step_order=order))
        if recovery_seconds:
            order += 1
            steps.append(create_recovery_step(recovery_seconds, step_order=order))
    order += 1
    if cooldown_seconds:
        steps.append(create_cooldown_step(cooldown_seconds, step_order=order))
    return RunningWorkout(
        workoutName=_workout_name(session),
        estimatedDurationInSecs=structured_seconds,
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1, sportType=RUNNING_SPORT, workoutSteps=steps
            )
        ],
    )


def _item_date(item: dict[str, Any]) -> str | None:
    for key in ("date", "calendarDate", "startDate", "startDateLocal"):
        value = item.get(key)
        if value:
            return str(value)[:10]
    return None


def _calendar_event(
    client: Any, workout_id: int, scheduled_date: date
) -> dict[str, Any] | None:
    for item in _calendar_entries(client, scheduled_date.year, scheduled_date.month):
        try:
            same_workout = int(item.get("workoutId", 0)) == int(workout_id)
        except (TypeError, ValueError):
            same_workout = False
        item_date = _item_date(item)
        if same_workout and (item_date is None or item_date == scheduled_date.isoformat()):
            return item
    return None


def _public_record(
    session: PlanSessionV2, session_index: int, record: dict[str, Any] | None
) -> dict[str, Any]:
    value = record or {}
    workout_id = value.get("workout_id")
    calendar_event_id = value.get("calendar_event_id")
    calendar_status = value.get("calendar_status") or "not_scheduled"
    uncertain = bool(value.get("upload_requested_at") and not workout_id)
    return {
        "session_index": session_index,
        "session_date": session.date.isoformat(),
        "scheduled_date": value.get("scheduled_date") or session.date.isoformat(),
        "sport": session.sport,
        "supported": session.sport == "running",
        "motionest_status": "saved",
        "garmin_connect_status": "created" if workout_id else "unknown" if uncertain else "not_created",
        "upload_status": "confirmed" if workout_id else "uncertain" if uncertain else "not_requested",
        "garmin_workout_id": workout_id,
        "calendar_status": calendar_status,
        "garmin_calendar_event_id": calendar_event_id,
        "watch_sync_status": "unverified",
        "structured_blocks_exported": bool(workout_id),
        "instructions_exported": False,
        "last_error": "Création Garmin incertaine ; aucun nouvel envoi avant vérification." if uncertain else value.get("last_error"),
        "updated_at": value.get("updated_at"),
        "session_id": session.session_id,
        "starts_at": session.starts_at.isoformat() if session.starts_at else None,
    }


def _uncertain_upload(store: dict[str, Any], session: PlanSessionV2, key: str) -> dict[str, Any] | None:
    if (store["deliveries"].get(key) or {}).get("workout_id"):
        return None  # A confirmed current ID never requires a new upload.
    for record in store["deliveries"].values():
        if not isinstance(record, dict) or not record.get("upload_requested_at") or record.get("workout_id"):
            continue
        # Legacy records have no stable identity: no new creation can safely be
        # attributed to a different session. Never guess from a name or a date.
        if session.session_id is None or record.get("session_id") is None or record.get("session_id") == session.session_id:
            return record
    return None


def assert_upload_retry_safe(user_id: str, *, session_index: int, expected_session_id: str | None = None) -> None:
    """Local preflight usable before constructing a connected Garmin client.

    publish_accepted_session repeats the check under the same process lock.
    This does not replace its consent, identity, date, sport or collision guards.
    """
    with user_context(user_id), _WRITE_LOCK:
        state, fingerprint = _accepted_proposal(user_id)
        assert state.proposal is not None
        if not 0 <= session_index < len(state.proposal.sessions):
            raise GarminDeliveryError("session_not_found", "Séance introuvable.")
        session = state.proposal.sessions[session_index]
        if expected_session_id is not None and session.session_id != expected_session_id:
            raise GarminDeliveryError("session_identity_conflict", "L'identité de la séance affichée a changé.")
        if _uncertain_upload(_read_store(), session, _session_delivery_key(fingerprint, session_index, session)):
            raise GarminDeliveryError("upload_uncertain", "Création Garmin incertaine ; aucun nouvel envoi avant vérification.")


def get_garmin_delivery_state(user_id: str) -> dict[str, Any]:
    """Read local facts only. Dashboard refreshes never call Garmin."""
    with user_context(user_id):
        proposal_state, fingerprint = _accepted_proposal(user_id)
        assert proposal_state.proposal is not None
        store = _read_store()
        return {
            "status": "available",
            "proposal_fingerprint": fingerprint,
            "sessions": [
                _public_record(
                    session,
                    index,
                    _uncertain_upload(store, session, _session_delivery_key(fingerprint, index, session)) or store["deliveries"].get(_session_delivery_key(fingerprint, index, session)),
                )
                for index, session in enumerate(proposal_state.proposal.sessions)
            ],
        }


def publish_accepted_session(
    user_id: str,
    client: Any,
    *,
    session_index: int,
    scheduled_date: date | None = None,
    expected_session_id: str | None = None,
) -> dict[str, Any]:
    """Create, schedule and verify one accepted session without duplicate retries."""
    with user_context(user_id), _WRITE_LOCK:
        proposal_state, fingerprint = _accepted_proposal(user_id)
        assert proposal_state.proposal is not None
        sessions = proposal_state.proposal.sessions
        if session_index < 0 or session_index >= len(sessions):
            raise GarminDeliveryError("session_not_found", "Séance introuvable.")
        session = sessions[session_index]
        if expected_session_id is not None and session.session_id != expected_session_id:
            raise GarminDeliveryError("session_identity_conflict", "L'identité de la séance affichée a changé.")
        if session.session_id is not None:
            decision = get_plan_acceptance_state(user_id, proposal_state=proposal_state, session_id=session.session_id)
            if decision.status != "accepted":
                raise GarminDeliveryError("session_not_accepted", "Valide explicitement cette séance avant son envoi.")
        target_date = scheduled_date or session.date
        if target_date < date.today():
            raise GarminDeliveryError(
                "scheduled_date_in_past", "La séance ne peut pas être programmée dans le passé."
            )
        if session.session_id is not None and target_date != session.date:
            raise GarminDeliveryError("scheduled_date_conflict", "Révise et accepte la séance avant de changer sa date.")
        if session.sport != "running":
            raise GarminDeliveryError(
                "sport_not_supported",
                "L'export Garmin structuré est actuellement disponible pour la course.",
            )
        store = _read_store()
        key = _session_delivery_key(fingerprint, session_index, session)
        if _uncertain_upload(store, session, key):
            raise GarminDeliveryError("upload_uncertain", "Création Garmin incertaine ; aucun nouvel envoi avant vérification.")
        previous = store["deliveries"].get(key) or {}
        # Confirmation of an already requested, unchanged delivery has no remote
        # effect. A revised sibling may leave an old workout on the same date;
        # that must still block new scheduling, not this exact confirmation.
        if (previous.get("workout_id") and previous.get("schedule_requested_at")
                and previous.get("scheduled_date") == target_date.isoformat()):
            for event in _checked_calendar_entries(client, target_date):
                if (event.get("itemType") != "workout"
                        or str(event.get("workoutId")) != str(previous["workout_id"])
                        or _item_date(event) != target_date.isoformat()):
                    continue
                confirmed = {"calendar_status": "scheduled", "calendar_event_id": event.get("id"), "last_error": None}
                if any(previous.get(field) != value for field, value in confirmed.items()):
                    previous.update(confirmed)
                    previous["updated_at"] = _now()
                    store["deliveries"][key] = previous
                    _write_store(store)
                return _public_record(session, session_index, previous)
        _check_calendar_collisions(client, target_date, session, proposal_state, store, user_id, fingerprint)
        record = store["deliveries"].get(key) or {
            "proposal_fingerprint": fingerprint,
            "session_index": session_index,
            "session_date": session.date.isoformat(),
            "scheduled_date": target_date.isoformat(),
            "calendar_status": "not_scheduled",
            "session_id": session.session_id,
            "session_fingerprint": session_fingerprint(session) if session.session_id else None,
        }
        if record.get("scheduled_date") != target_date.isoformat() and record.get(
            "workout_id"
        ):
            raise GarminDeliveryError(
                "scheduled_date_conflict",
                "Cette séance Garmin existe déjà pour une autre date.",
            )

        workout_id = record.get("workout_id")
        if workout_id:
            event = _calendar_event(client, int(workout_id), target_date)
            if event is not None:
                confirmed = {
                    "calendar_status": "scheduled",
                    "calendar_event_id": event.get("id"),
                    "last_error": None,
                }
                if any(record.get(field) != value for field, value in confirmed.items()):
                    record.update(confirmed)
                    record["updated_at"] = _now()
                    store["deliveries"][key] = record
                    _write_store(store)
                return _public_record(session, session_index, record)
            if record.get("schedule_requested_at"):
                if record.get("calendar_status") != "verification_pending":
                    record.update(
                        {
                            "calendar_status": "verification_pending",
                            "updated_at": _now(),
                        }
                    )
                    store["deliveries"][key] = record
                    _write_store(store)
                return _public_record(session, session_index, record)
        else:
            workout = build_plan_session_workout(session)
            record.update({"upload_requested_at": _now(), "updated_at": _now()})
            store["deliveries"][key] = record
            _write_store(store)  # If this fails, no upload is attempted.
            try:
                uploaded = client.upload_running_workout(workout)
            except Exception as exc:
                raise GarminDeliveryError("upload_uncertain", "Création Garmin incertaine ; aucun nouvel envoi avant vérification.") from exc
            candidate = uploaded.get("workoutId") if isinstance(uploaded, dict) else None
            if (isinstance(candidate, bool) or not isinstance(candidate, (int, str))
                    or not str(candidate).isascii() or not str(candidate).isdigit() or int(candidate) <= 0):
                raise GarminDeliveryError(
                    "workout_id_missing",
                    "Garmin n'a pas confirmé d'identifiant valide ; création incertaine, aucun nouvel envoi avant vérification.",
                )
            workout_id = int(candidate)
            record.update(
                {
                    "workout_id": int(workout_id),
                    "created_at": _now(),
                    "updated_at": _now(),
                    "last_error": None,
                }
            )
            store["deliveries"][key] = record
            _write_store(store)

        record.update(
            {
                "schedule_requested_at": _now(),
                "calendar_status": "verification_pending",
                "updated_at": _now(),
            }
        )
        store["deliveries"][key] = record
        _write_store(store)
        try:
            client.schedule_workout(int(workout_id), target_date.isoformat())
            event = _calendar_event(client, int(workout_id), target_date)
        except Exception as exc:
            record.update(
                {
                    "last_error": f"{type(exc).__name__}: vérification Garmin requise",
                    "updated_at": _now(),
                }
            )
            store["deliveries"][key] = record
            _write_store(store)
            raise GarminDeliveryError(
                "calendar_schedule_uncertain",
                "Garmin n'a pas confirmé la programmation. Vérifie le calendrier avant de réessayer.",
            ) from exc
        if event is not None:
            record.update(
                {
                    "calendar_status": "scheduled",
                    "calendar_event_id": event.get("id"),
                    "last_error": None,
                    "updated_at": _now(),
                }
            )
        else:
            record.update(
                {
                    "calendar_status": "verification_pending",
                    "last_error": "Programmation demandée ; confirmation calendrier en attente.",
                    "updated_at": _now(),
                }
            )
        store["deliveries"][key] = record
        _write_store(store)
        return _public_record(session, session_index, record)
