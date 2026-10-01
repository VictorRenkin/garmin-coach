from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from .config import ONBOARDING_FILE, ensure_directories
from .local_activity_history import iter_local_activity_history
from .summary import is_running


ONBOARDING_VERSION = 2
INPUT_STEPS = ("goal", "availability", "subjective_context")
LEGACY_COMPLETE_STATUSES = {"waiting_existing_plan", "plan_proposed", "complete"}
_WRITE_LOCK = threading.RLock()


def _write_onboarding(state: dict[str, Any]) -> dict[str, Any]:
    """Validate the serialization then atomically replace onboarding progress."""
    ensure_directories()
    temporary = ONBOARDING_FILE.with_name(
        f".{ONBOARDING_FILE.name}.{uuid.uuid4().hex}.tmp"
    )
    encoded = json.dumps(state, ensure_ascii=False, indent=2)
    with _WRITE_LOCK:
        try:
            temporary.write_text(encoded, encoding="utf-8")
            decoded = json.loads(temporary.read_text(encoding="utf-8"))
            if not isinstance(decoded, dict):
                raise ValueError("État onboarding invalide.")
            os.replace(temporary, ONBOARDING_FILE)
        finally:
            temporary.unlink(missing_ok=True)
    return state


def onboarding_v2_state(*, inputs_source: str = "legacy_profile", inputs: Any = None) -> dict[str, Any]:
    """Return or initialize explicit V2 progress without treating fallbacks as answers."""
    with _WRITE_LOCK:
        state = load_onboarding()
        if str(state.get("status") or "") in LEGACY_COMPLETE_STATUSES:
            return state
        progress = state.get("input_progress")
        if state.get("onboarding_version") == ONBOARDING_VERSION and isinstance(progress, dict):
            completed = {step: progress.get(step) is True for step in INPUT_STEPS}
        else:
            # Existing explicit V1 declarations are authoritative. A legacy profile
            # is considered complete only when the legacy onboarding had reached
            # ready; incomplete legacy data merely prefills the V2 form.
            explicit = inputs_source == "v1"
            legacy_ready = str(state.get("status") or "") == "ready"
            pain = getattr(getattr(inputs, "subjective_context", None), "pain_or_injury", None)
            completed = {
                "goal": bool((explicit or legacy_ready) and getattr(inputs, "goal", None)),
                "availability": bool((explicit or legacy_ready) and getattr(inputs, "availability", None)),
                "subjective_context": bool(
                    (explicit or legacy_ready)
                    and pain is not None
                    and getattr(pain, "status", "unknown") != "unknown"
                ),
            }
        ready = all(completed.values())
        current = next((step for step in INPUT_STEPS if not completed[step]), "complete")
        updated = {
            **state,
            "onboarding_version": ONBOARDING_VERSION,
            "status": "ready" if ready else "collecting_inputs",
            "input_progress": {**completed, "current_step": current},
            "publication_allowed": False,
        }
        if updated != state:
            _write_onboarding(updated)
        return updated


def complete_onboarding_v2_step(step: str) -> dict[str, Any]:
    """Mark one already-persisted declaration as explicitly confirmed."""
    if step not in INPUT_STEPS:
        raise ValueError("Étape onboarding V2 invalide.")
    with _WRITE_LOCK:
        state = load_onboarding()
        if str(state.get("status") or "") in LEGACY_COMPLETE_STATUSES:
            return state
        progress = state.get("input_progress")
        completed = {
            item: isinstance(progress, dict) and progress.get(item) is True
            for item in INPUT_STEPS
        }
        completed[step] = True
        ready = all(completed.values())
        current = next((item for item in INPUT_STEPS if not completed[item]), "complete")
        return _write_onboarding(
            {
                **state,
                "onboarding_version": ONBOARDING_VERSION,
                "status": "ready" if ready else "collecting_inputs",
                "input_progress": {**completed, "current_step": current},
                "publication_allowed": False,
            }
        )


def load_onboarding() -> dict[str, Any]:
    if not ONBOARDING_FILE.is_file():
        return {"status": "not_started"}
    try:
        payload = json.loads(ONBOARDING_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "not_started"}
    if not isinstance(payload, dict):
        return {"status": "not_started"}
    summary = payload.get("garmin_summary")
    if isinstance(summary, dict):
        sports = dict(summary.get("sports_detectes") or {})
        legacy_other = int(sports.pop("other", 0) or 0) + int(
            sports.pop("autre", 0) or 0
        )
        if legacy_other:
            sports["autres activités"] = (
                int(sports.get("autres activités", 0) or 0) + legacy_other
            )
        summary["sports_detectes"] = sports
        counts = dict(summary.get("types_entrainement_detectes") or {})
        if not counts:
            names = summary.get("noms_seances_exemples") or []
            counts = dict(
                _training_type_counts(
                    [{"workoutName": str(name)} for name in names]
                ).most_common()
            )
        if counts:
            summary["types_entrainement_detectes"] = counts
            summary["types_detectes"] = list(counts)
    return payload


def _distance_km(activity: dict[str, Any]) -> float | None:
    value = activity.get("distance")
    return round(float(value) / 1000, 1) if isinstance(value, (int, float)) else None


def _sport(activity: dict[str, Any]) -> str:
    kind = activity.get("activityType")
    key = kind.get("typeKey") if isinstance(kind, dict) else kind
    raw = " ".join(
        str(item or "")
        for item in (
            key,
            activity.get("activityTypeDTO"),
            activity.get("activityName"),
            activity.get("name"),
        )
    ).lower()
    value = "".join(
        character
        for character in unicodedata.normalize("NFKD", raw)
        if not unicodedata.combining(character)
    )
    if "run" in value or "course" in value:
        return "course"
    if "swim" in value or "natation" in value:
        return "natation"
    if "cycl" in value or "bike" in value or "vélo" in value:
        return "vélo"
    if "walk" in value or "marche" in value or "hike" in value:
        return "marche"
    if any(
        word in value
        for word in ("strength", "musculation", "renforcement", "cardio", "hiit")
    ):
        return "renforcement"
    if any(word in value for word in ("yoga", "pilates", "mobility", "mobilite")):
        return "mobilité"
    if any(word in value for word in ("row", "rameur", "rowing")):
        return "rameur"
    if any(word in value for word in ("ski", "snowboard")):
        return "sport d'hiver"
    return "autres activités"


def _activity_date(activity: dict[str, Any]) -> datetime | None:
    raw = activity.get("startTimeLocal") or activity.get("startTimeGMT")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _training_types(items: list[dict[str, Any]]) -> list[str]:
    keywords: Counter[str] = Counter()
    variants = {
        "endurance": ("easy", "endurance", "footing", "base", "aerobic"),
        "intervalles": ("interval", "fraction", "hiit", "repeat", "répét"),
        "tempo": ("tempo", "threshold", "seuil"),
        "sortie longue": ("long", "longue"),
        "récupération": ("recovery", "récup", "recup"),
    }
    for item in items:
        name = str(
            item.get("workoutName") or item.get("activityName") or item.get("name") or ""
        ).lower()
        for label, words in variants.items():
            if any(word in name for word in words):
                keywords[label] += 1
    return [label for label, _ in keywords.most_common()]


def _training_type_counts(items: list[dict[str, Any]]) -> Counter[str]:
    """Reconnaît aussi les abréviations courantes des séances Garmin."""
    counts: Counter[str] = Counter()
    variants = (
        ("intervalles", r"\b(?:interval|fraction|hiit|repeat|repet|int)\b|\b\d+\s*x\s*\d+"),
        ("tempo", r"\b(?:tempo|threshold|seuil)\b"),
        ("récupération", r"\b(?:recovery|recuperation|recup|rec)\b"),
        ("sortie longue", r"\b(?:sortie\s+longue|long\s+run|longue|sl)\b"),
        ("endurance", r"\b(?:easy|endurance|footing|base|aerobic|ef)\b"),
    )
    for item in items:
        raw_name = str(
            item.get("workoutName")
            or item.get("activityName")
            or item.get("name")
            or ""
        ).lower()
        name = "".join(
            character
            for character in unicodedata.normalize("NFKD", raw_name)
            if not unicodedata.combining(character)
        )
        for label, pattern in variants:
            if re.search(pattern, name):
                counts[label] += 1
                break
    return counts


def inspect_garmin_account(
    client: Any, *, force: bool = False, preserve_status: bool = False
) -> dict[str, Any]:
    """Inventorie l'historique local sans nouvel appel Garmin."""
    existing = load_onboarding()
    if not force and existing.get("status") in {
        "needs_profile",
        "ready",
        "waiting_existing_plan",
        "plan_proposed",
        "complete",
    }:
        return existing

    del client  # Kept in the public signature for setup-call compatibility.
    activities = list(iter_local_activity_history())
    legacy_classification = existing.get("onboarding_version") != ONBOARDING_VERSION
    type_counts: Counter[str] = Counter()
    activity_count = 0
    run_count = 0
    distance_count = 0
    distance_total = 0.0
    distance_maximum: float | None = None
    sports: Counter[str] = Counter()
    latest_date: datetime | None = None
    for item in activities:
        activity_count += 1
        sports[_sport(item)] += 1
        activity_day = _activity_date(item)
        if activity_day is not None and (
            latest_date is None or activity_day > latest_date
        ):
            latest_date = activity_day
        if not is_running(item):
            continue
        run_count += 1
        if legacy_classification:
            type_counts.update(_training_type_counts([item]))
        distance = _distance_km(item)
        if distance is not None:
            distance_count += 1
            distance_total += distance
            distance_maximum = (
                distance
                if distance_maximum is None
                else max(distance_maximum, distance)
            )
    logic = [label for label, _ in type_counts.most_common()]
    recent_cutoff = latest_date - timedelta(days=27) if latest_date else None
    recent_count = 0
    recent_weeks: set[tuple[int, int]] = set()
    if recent_cutoff:
        for item in activities:
            activity_day = _activity_date(item)
            if activity_day is not None and activity_day >= recent_cutoff:
                recent_count += 1
                iso = activity_day.isocalendar()
                recent_weeks.add((iso.year, iso.week))
    active_weeks = len(recent_weeks)
    weekly_frequency = round(recent_count / 4, 1) if recent_count else None
    regular = bool(recent_count >= 4 and active_weeks >= 3)
    summary = {
        "activites_trouvees": activity_count,
        "courses_trouvees": run_count,
        "seances_course_garmin_trouvees": 0,
        "distance_moyenne_km": round(distance_total / distance_count, 1)
        if distance_count
        else None,
        "distance_maximale_km": distance_maximum,
        "types_detectes": logic,
        "types_entrainement_detectes": dict(type_counts.most_common()),
        "logique_detectee": bool(len(logic) >= 2),
        "sports_detectes": dict(sports.most_common()),
        "frequence_hebdomadaire_recente": weekly_frequency,
        "semaines_actives_sur_4": active_weeks,
        "regularite_detectee": regular,
    }
    preserved_status = str(existing.get("status") or "")
    status = (
        preserved_status
        if preserve_status
        and preserved_status
        in {"ready", "waiting_existing_plan", "plan_proposed", "complete"}
        else "needs_profile"
    )
    payload = {
        **existing,
        "status": status,
        "inspected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "garmin_summary": summary,
        "required_answers": [
            "objectif et distance visée",
            "date éventuelle de la course",
            "nombre de séances possibles par semaine",
            "jours de disponibilité",
            "niveau de fatigue, douleurs et préférences",
        ],
        "publication_allowed": False,
    }
    return _write_onboarding(payload)


def update_garmin_setup(
    status: str,
    *,
    message: str = "",
    error: str = "",
    failed_step: str = "",
    retryable: bool = False,
) -> dict[str, Any]:
    """Persiste l'avancement du setup Garmin dans l'espace de l'utilisateur."""
    state = load_onboarding()
    state["garmin_setup"] = {
        "status": status,
        "message": message,
        "error": error,
        "failed_step": failed_step,
        "retryable": retryable,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return _write_onboarding(state)


def onboarding_prompt(state: dict[str, Any]) -> str:
    summary = state.get("garmin_summary") or {}
    runs = int(summary.get("courses_trouvees") or 0)
    workouts = int(summary.get("seances_course_garmin_trouvees") or 0)
    types = ", ".join(summary.get("types_detectes") or [])
    sports = summary.get("sports_detectes") or {}
    other_sports = [f"{count} séance(s) de {sport}" for sport, count in sports.items() if sport != "course" and count]
    observation = f"J’ai parcouru tes derniers mois d’activité et trouvé {runs} course(s)"
    if other_sports:
        observation += f", ainsi que {', '.join(other_sports)}"
    observation += "."
    if summary.get("regularite_detectee"):
        frequency = summary.get("frequence_hebdomadaire_recente")
        observation += f" Tu t’entraînes régulièrement, autour de {frequency:g} fois par semaine récemment."
    if types:
        observation += f" Je reconnais dans tes séances du travail de {types}."
    elif workouts:
        observation += " Tes entraînements semblent déjà structurés, même si je manque encore d’indices pour préciser leur logique."
    else:
        observation += " Je ne vois pas encore de programme structuré dans Garmin."
    return (
        observation
        + " Quel objectif aimerais-tu maintenant préparer ? Ensuite, je te demanderai seulement "
        "ce que tes données ne peuvent pas me dire : tes disponibilités, douleurs et préférences."
    )


def mark_profile_ready() -> dict[str, Any]:
    state = load_onboarding()
    state["status"] = "ready"
    state["publication_allowed"] = False
    return _write_onboarding(state)


def mark_complete() -> dict[str, Any]:
    state = load_onboarding()
    state["status"] = "complete"
    state["publication_allowed"] = True
    return _write_onboarding(state)


def mark_existing_plan_continuation() -> dict[str, Any]:
    state = load_onboarding()
    state["status"] = "waiting_existing_plan"
    state["takeover_after_existing_plan"] = True
    state["publication_allowed"] = False
    return _write_onboarding(state)


def mark_plan_proposed(path: str) -> dict[str, Any]:
    state = load_onboarding()
    state["status"] = "plan_proposed"
    state["proposed_plan"] = path
    state["publication_allowed"] = False
    return _write_onboarding(state)


def mark_plan_rejected(feedback: str = "") -> dict[str, Any]:
    state = load_onboarding()
    state["status"] = "ready"
    state["publication_allowed"] = False
    state["last_plan_feedback"] = feedback.strip()[:1000]
    setup = state.get("garmin_setup")
    if isinstance(setup, dict):
        setup["status"] = "plan_rejected"
        setup["message"] = "Le plan doit être ajusté avant une nouvelle proposition."
        setup["retryable"] = False
    return _write_onboarding(state)
