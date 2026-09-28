from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from .config import ONBOARDING_FILE, ensure_directories
from .summary import is_running


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


def inspect_garmin_account(client: Any, *, force: bool = False) -> dict[str, Any]:
    """Inventorie une fois l'historique et les séances sans stocker d'identifiants."""
    existing = load_onboarding()
    if not force and existing.get("status") in {
        "needs_profile",
        "ready",
        "waiting_existing_plan",
        "plan_proposed",
        "complete",
    }:
        return existing

    activities = client.get_activities(0, 100)
    valid_activities = [item for item in activities if isinstance(item, dict)]
    runs = [item for item in valid_activities if is_running(item)]
    try:
        workouts = client.get_workouts(0, 100)
    except Exception:
        workouts = []
    running_workouts = [
        item
        for item in workouts
        if isinstance(item, dict)
        and str((item.get("sportType") or {}).get("sportTypeKey") or item.get("sportTypeKey") or "").lower()
        in {"running", "run"}
    ]
    distances = [value for item in runs if (value := _distance_km(item)) is not None]
    names = [
        str(item.get("workoutName") or item.get("name") or "").strip()
        for item in running_workouts
    ]
    type_counts = _training_type_counts(running_workouts + runs)
    logic = [label for label, _ in type_counts.most_common()]
    sports = Counter(_sport(item) for item in valid_activities)
    dated = sorted(value for item in valid_activities if (value := _activity_date(item)))
    recent_cutoff = dated[-1] - timedelta(days=27) if dated else None
    recent = [value for value in dated if recent_cutoff and value >= recent_cutoff]
    active_weeks = len({value.isocalendar()[:2] for value in recent})
    weekly_frequency = round(len(recent) / 4, 1) if recent else None
    regular = bool(len(recent) >= 4 and active_weeks >= 3)
    summary = {
        "activites_trouvees": len(valid_activities),
        "courses_trouvees": len(runs),
        "seances_course_garmin_trouvees": len(running_workouts),
        "distance_moyenne_km": round(sum(distances) / len(distances), 1) if distances else None,
        "distance_maximale_km": max(distances, default=None),
        "types_detectes": logic,
        "types_entrainement_detectes": dict(type_counts.most_common()),
        "logique_detectee": bool(len(logic) >= 2 or len(running_workouts) >= 3),
        "sports_detectes": dict(sports.most_common()),
        "frequence_hebdomadaire_recente": weekly_frequency,
        "semaines_actives_sur_4": active_weeks,
        "regularite_detectee": regular,
        "noms_seances_exemples": names[:8],
    }
    payload = {
        "status": "needs_profile",
        "inspected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "garmin_summary": summary,
        "required_answers": [
            "objectif et distance visée",
            "date éventuelle de la course",
            "nombre de séances possibles par semaine",
            "jours ou contraintes de disponibilité",
            "niveau de fatigue, douleurs et préférences",
        ],
        "publication_allowed": False,
    }
    ensure_directories()
    ONBOARDING_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


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
        "ce que tes données ne peuvent pas me dire : tes disponibilités et tes éventuelles contraintes."
    )


def mark_profile_ready() -> dict[str, Any]:
    state = load_onboarding()
    state["status"] = "ready"
    state["publication_allowed"] = False
    ensure_directories()
    ONBOARDING_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return state


def mark_complete() -> dict[str, Any]:
    state = load_onboarding()
    state["status"] = "complete"
    state["publication_allowed"] = True
    ensure_directories()
    ONBOARDING_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return state


def mark_existing_plan_continuation() -> dict[str, Any]:
    state = load_onboarding()
    state["status"] = "waiting_existing_plan"
    state["takeover_after_existing_plan"] = True
    state["publication_allowed"] = False
    ensure_directories()
    ONBOARDING_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return state


def mark_plan_proposed(path: str) -> dict[str, Any]:
    state = load_onboarding()
    state["status"] = "plan_proposed"
    state["proposed_plan"] = path
    state["publication_allowed"] = False
    ensure_directories()
    ONBOARDING_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return state


def mark_plan_rejected(feedback: str = "") -> dict[str, Any]:
    state = load_onboarding()
    state["status"] = "ready"
    state["publication_allowed"] = False
    state["last_plan_feedback"] = feedback.strip()[:1000]
    ensure_directories()
    ONBOARDING_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return state
