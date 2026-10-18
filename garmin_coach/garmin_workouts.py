from __future__ import annotations

import json
from datetime import date
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

from .training_cycle import PlannedSession
from .session_blocks import validate_blocks_duration
from .structured_workouts import build_block_steps


RUNNING_SPORT = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
TYPE_CODES = {
    "endurance facile": "EF",
    "tempo": "TP",
    "intervalles": "INT",
    "sortie longue": "SL",
    "récupération": "REC",
}


def _garmin_name(session: PlannedSession) -> str:
    day = date.fromisoformat(session.date).strftime("%d%m")
    return f"GC {day} {TYPE_CODES[session.type]}"[:15]


def build_running_workout(session: PlannedSession) -> RunningWorkout:
    steps: list[Any] = [
        create_warmup_step(session.echauffement_minutes * 60, step_order=1)
    ]
    if session.repetitions_bloc > 1:
        repeated: list[Any] = [
            create_interval_step(session.effort_minutes * 60, step_order=1)
        ]
        if session.recuperation_minutes > 0:
            repeated.append(
                create_recovery_step(
                    session.recuperation_minutes * 60, step_order=2
                )
            )
        steps.append(
            create_repeat_group(
                session.repetitions_bloc, repeated, step_order=2
            )
        )
    else:
        steps.append(
            create_interval_step(session.effort_minutes * 60, step_order=2)
        )
    steps.append(
        create_cooldown_step(session.retour_au_calme_minutes * 60, step_order=3)
    )
    estimated_seconds = (
        session.echauffement_minutes
        + session.repetitions_bloc
        * (session.effort_minutes + session.recuperation_minutes)
        + session.retour_au_calme_minutes
    ) * 60
    return RunningWorkout(
        workoutName=_garmin_name(session),
        estimatedDurationInSecs=round(estimated_seconds),
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1, sportType=RUNNING_SPORT, workoutSteps=steps
            )
        ],
    )


def _save_payload(target: Path, payload: dict[str, Any]) -> None:
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def publish_plan(client: Any, target: Path) -> int:
    """Publie et planifie les séances non encore envoyées, sans doublons."""
    payload = json.loads(target.read_text(encoding="utf-8"))
    sessions = payload.get("seances", [])
    if not isinstance(sessions, list):
        raise ValueError("Le plan ne contient aucune liste de séances.")
    scheduled_count = 0
    for raw_session in sessions:
        if not isinstance(raw_session, dict):
            continue
        garmin_state = raw_session.setdefault("garmin", {})
        if garmin_state.get("scheduled"):
            continue
        session = PlannedSession.model_validate(raw_session)
        workout_id = garmin_state.get("workout_id")
        if not workout_id:
            uploaded = client.upload_running_workout(build_running_workout(session))
            workout_id = uploaded.get("workoutId")
            if not workout_id:
                raise RuntimeError("Garmin n'a pas renvoyé l'identifiant de la séance.")
            garmin_state["workout_id"] = workout_id
            _save_payload(target, payload)
        client.schedule_workout(workout_id, session.date)
        garmin_state["scheduled"] = True
        _save_payload(target, payload)
        scheduled_count += 1
    return scheduled_count


HR_ZONE_TARGET = {
    "workoutTargetTypeId": 4,
    "workoutTargetTypeKey": "heart.rate.zone",
    "displayOrder": 4,
}


def _with_hr_zone(step: Any, zone: int) -> Any:
    step.targetType = HR_ZONE_TARGET
    step.zoneNumber = zone
    return step


def build_running_workout(session: PlannedSession) -> RunningWorkout:
    """Construit toutes les phases avec les zones cardiaques Garmin."""
    if session.blocks is not None:
        session = PlannedSession.model_validate(session.model_dump())
        structured_seconds = validate_blocks_duration(session.blocks, session.echauffement_minutes,
                                                       session.retour_au_calme_minutes, session.duree_estimee_minutes)
        # Keep the established progressive warmup and its explicitly supplied zones.
        steps: list[Any] = []
        end_zone = session.echauffement_zone_fc_fin
        if end_zone and end_zone != session.echauffement_zone_fc:
            first = session.echauffement_minutes // 2
            steps.append(_with_hr_zone(create_warmup_step(first * 60, step_order=1), session.echauffement_zone_fc))
            steps.append(_with_hr_zone(create_warmup_step((session.echauffement_minutes - first) * 60, step_order=2), end_zone))
        else:
            steps.append(_with_hr_zone(create_warmup_step(session.echauffement_minutes * 60, step_order=1), session.echauffement_zone_fc))
        steps.extend(build_block_steps(session.blocks, len(steps) + 1))
        steps.append(_with_hr_zone(create_cooldown_step(session.retour_au_calme_minutes * 60, step_order=len(steps) + 1), session.retour_au_calme_zone_fc))
        return RunningWorkout(workoutName=_garmin_name(session), estimatedDurationInSecs=structured_seconds,
                              workoutSegments=[WorkoutSegment(segmentOrder=1, sportType=RUNNING_SPORT, workoutSteps=steps)])
    warmup_end_zone = session.echauffement_zone_fc_fin
    if warmup_end_zone and warmup_end_zone != session.echauffement_zone_fc:
        first_minutes = session.echauffement_minutes // 2
        second_minutes = session.echauffement_minutes - first_minutes
        steps: list[Any] = [
            _with_hr_zone(
                create_warmup_step(first_minutes * 60, step_order=1),
                session.echauffement_zone_fc,
            ),
            _with_hr_zone(
                create_warmup_step(second_minutes * 60, step_order=2),
                warmup_end_zone,
            ),
        ]
    else:
        steps = [
            _with_hr_zone(
                create_warmup_step(session.echauffement_minutes * 60, step_order=1),
                session.echauffement_zone_fc,
            )
        ]
    main_order = len(steps) + 1
    if session.repetitions_bloc > 1:
        repeated: list[Any] = [
            _with_hr_zone(
                create_interval_step(session.effort_minutes * 60, step_order=1),
                session.bloc_zone_fc,
            )
        ]
        if session.recuperation_minutes > 0:
            repeated.append(
                _with_hr_zone(
                    create_recovery_step(
                        session.recuperation_minutes * 60, step_order=2
                    ),
                    session.recuperation_zone_fc,
                )
            )
        steps.append(
            create_repeat_group(
                session.repetitions_bloc, repeated, step_order=main_order
            )
        )
    else:
        steps.append(
            _with_hr_zone(
                create_interval_step(
                    session.effort_minutes * 60, step_order=main_order
                ),
                session.bloc_zone_fc,
            )
        )
        if session.recuperation_minutes > 0:
            main_order += 1
            steps.append(_with_hr_zone(create_recovery_step(session.recuperation_minutes * 60, step_order=main_order),
                                       session.recuperation_zone_fc))
    steps.append(
        _with_hr_zone(
            create_cooldown_step(
                session.retour_au_calme_minutes * 60, step_order=main_order + 1
            ),
            session.retour_au_calme_zone_fc,
        )
    )
    estimated_seconds = (
        session.echauffement_minutes
        + session.repetitions_bloc
        * (session.effort_minutes + session.recuperation_minutes)
        + session.retour_au_calme_minutes
    ) * 60
    return RunningWorkout(
        workoutName=_garmin_name(session),
        estimatedDurationInSecs=estimated_seconds,
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1, sportType=RUNNING_SPORT, workoutSteps=steps
            )
        ],
    )


def _walk_objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        nested = [value]
        for item in value.values():
            nested.extend(_walk_objects(item))
        return nested
    if isinstance(value, list):
        nested = []
        for item in value:
            nested.extend(_walk_objects(item))
        return nested
    return []


def _calendar_entries(client: Any, year: int, month: int) -> list[dict[str, Any]]:
    getter = getattr(client, "get_scheduled_workouts", None)
    if callable(getter):
        payload = getter(year, month)
    else:
        payload = client.connectapi(
            f"/calendar-service/year/{year}/month/{month - 1}"
        )
    return [
        item
        for item in _walk_objects(payload)
        if item.get("itemType") == "workout" and item.get("workoutId")
    ]


def remove_future_sessions(
    client: Any,
    target: Path,
    today: date | None = None,
    session_indexes: set[int] | None = None,
) -> int:
    """Retire du calendrier et de la bibliothèque les séances encore futures."""
    payload = json.loads(target.read_text(encoding="utf-8"))
    current_day = today or date.today()
    sessions = [item for item in payload.get("seances", []) if isinstance(item, dict)]
    future = []
    for index, session in enumerate(sessions):
        if session_indexes is not None and index not in session_indexes:
            continue
        try:
            session_day = date.fromisoformat(str(session.get("date")))
        except ValueError:
            continue
        workout_id = (session.get("garmin") or {}).get("workout_id")
        if session_day >= current_day and workout_id:
            future.append((session, int(workout_id), session_day))

    calendar_items: list[dict[str, Any]] = []
    for year, month in {(item[2].year, item[2].month) for item in future}:
        calendar_items.extend(_calendar_entries(client, year, month))

    removed = 0
    for session, workout_id, _ in future:
        event_ids = {
            int(item["id"])
            for item in calendar_items
            if int(item.get("workoutId", 0)) == workout_id and item.get("id")
        }
        for event_id in event_ids:
            unschedule = getattr(client, "unschedule_workout", None)
            if callable(unschedule):
                unschedule(event_id)
            else:
                client.garth.delete(
                    "connectapi",
                    f"/workout-service/schedule/{event_id}",
                    api=True,
                )
        delete = getattr(client, "delete_workout", None)
        if callable(delete):
            delete(workout_id)
        else:
            client.garth.delete(
                "connectapi", f"/workout-service/workout/{workout_id}", api=True
            )
        session.pop("garmin", None)
        _save_payload(target, payload)
        removed += 1
    return removed


def replace_future_plan(client: Any, target: Path) -> tuple[int, int]:
    removed = remove_future_sessions(client, target)
    published = publish_plan(client, target)
    return removed, published


def apply_plan_revision(
    client: Any, current: Path, replacement: Path, today: date | None = None
) -> tuple[int, int]:
    """Valide une révision, retire les séances futures, puis publie sans doublon."""
    try:
        revised = json.loads(replacement.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Le plan de remplacement n'est pas un JSON valide.") from exc
    sessions = revised.get("seances") if isinstance(revised, dict) else None
    if not isinstance(sessions, list) or not 1 <= len(sessions) <= 7:
        raise ValueError("Le plan de remplacement doit contenir entre 1 et 7 séances.")
    current_day = today or date.today()
    dates: set[str] = set()
    for raw_session in sessions:
        session = PlannedSession.model_validate(raw_session)
        if session.date in dates:
            raise ValueError("Deux séances ne peuvent pas avoir la même date.")
        dates.add(session.date)
        if date.fromisoformat(session.date) >= current_day:
            raw_session.pop("garmin", None)

    removed = remove_future_sessions(client, current, today=current_day)
    _save_payload(current, revised)
    try:
        published = publish_plan(client, current)
    except Exception:
        # Le plan validé reste visible localement et pourra être republié sans doublon.
        raise
    return removed, published
