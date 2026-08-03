from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, Field

from .ai_analysis import MissingOpenAIKeyError, _sanitized_metrics, load_analysis
from .config import PLANS_DIR, PROFILE_FILE, Settings, ensure_directories
from .coach_chat import list_messages
from .storage import load_activities


class PlannedSession(BaseModel):
    date: str
    titre: str = Field(min_length=3, max_length=80)
    type: Literal[
        "endurance facile",
        "tempo",
        "intervalles",
        "sortie longue",
        "récupération",
    ]
    objectif: str = Field(min_length=10, max_length=250)
    raison: str = Field(min_length=10, max_length=300)
    echauffement: str = Field(min_length=3, max_length=200)
    bloc_principal: str = Field(min_length=5, max_length=400)
    retour_au_calme: str = Field(min_length=3, max_length=200)
    duree_estimee_minutes: int = Field(ge=15, le=150)
    distance_estimee_km: float = Field(ge=2, le=25)
    consigne_intensite: str = Field(min_length=5, max_length=250)
    echauffement_minutes: int = Field(ge=3, le=25)
    repetitions_bloc: int = Field(ge=1, le=12)
    effort_minutes: int = Field(ge=1, le=100)
    recuperation_minutes: int = Field(ge=0, le=15)
    retour_au_calme_minutes: int = Field(ge=3, le=25)
    echauffement_zone_fc: int = Field(default=1, ge=1, le=5)
    echauffement_zone_fc_fin: int | None = Field(default=None, ge=1, le=5)
    bloc_zone_fc: int = Field(default=2, ge=1, le=5)
    recuperation_zone_fc: int = Field(default=1, ge=1, le=5)
    retour_au_calme_zone_fc: int = Field(default=1, ge=1, le=5)


class WeeklyReview(BaseModel):
    adherence: Literal["3/3", "2/3", "1/3", "0/3", "non mesurable"]
    message_adherence: str = Field(min_length=15, max_length=400)
    bilan: str = Field(min_length=20, max_length=700)
    points_positifs: list[str] = Field(min_length=1, max_length=3)
    points_a_ameliorer: list[str] = Field(min_length=1, max_length=3)


class WeeklyCycle(BaseModel):
    revue: WeeklyReview
    adaptation: str = Field(min_length=20, max_length=500)
    seances_semaine_suivante: list[PlannedSession] = Field(
        min_length=3, max_length=3
    )
    avertissement_securite: str = Field(min_length=10, max_length=300)


WEEKLY_INSTRUCTIONS = """
Tu es un coach de course à pied prudent. Tu fais le bilan d'une semaine puis tu
prépares exactement trois entraînements pour la semaine suivante afin de faire
progresser un coureur amateur vers un semi-marathon.

Règles :
- Réponds en français et fonde chaque conclusion sur les données fournies.
- Si aucun plan n'existait pour la semaine évaluée, l'adhérence est « non
  mesurable » : ne reproche pas au coureur de ne pas avoir suivi un plan absent.
- Si un plan de trois séances existait et qu'une ou plusieurs manquent, indique
  clairement le nombre manquant, sans culpabiliser. N'augmente pas la charge de
  la semaine suivante pour « rattraper ».
- Une séance lente n'est pas un échec. Compare sa réalisation à son objectif,
  à sa distance, à son dénivelé et aux analyses disponibles.
- Les trois nouvelles séances doivent utiliser exactement les trois dates
  proposées, laisser de la récupération entre elles et avoir chacune un but
  mesurable et compréhensible.
- Les champs numériques décrivent la séance Garmin : échauffement, répétitions
  du bloc, minutes d'effort par répétition, récupération entre répétitions et
  retour au calme. Pour une endurance continue, utilise une seule répétition et
  zéro minute de récupération.
- Chaque phase doit avoir une cible cardiaque Garmin : échauffement progressif
  de la zone 1 vers la zone 2, endurance et sortie longue principalement en
  zone 2, récupération en zone 1, et travail soutenu au maximum en zone 4.
  La zone 5 ne doit pas être prescrite dans ce programme amateur.
- Décris précisément le déroulement de l'échauffement et du retour au calme.
- L'adaptation doit rester progressive. En cas de données insuffisantes ou de
  faible régularité, privilégie l'endurance et la continuité.
- N'invente aucune douleur, sensation ou contrainte. Ne pose aucun diagnostic.
- Utilise les retours déclarés dans le chat (effort perçu, fatigue, douleur et
  contraintes) pour adapter prudemment la charge. Une douleur déclarée impose
  de ne pas augmenter la charge et de conseiller une évaluation adaptée.
- Ne prescris jamais de courir malgré une douleur.
""".strip()


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def plan_path(week_start: date | str) -> Path:
    value = week_start.isoformat() if isinstance(week_start, date) else week_start
    return PLANS_DIR / f"{value}.json"


def load_plan(week_start: date | str) -> dict[str, Any] | None:
    target = plan_path(week_start)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def find_planned_session(activity: dict[str, Any]) -> dict[str, Any] | None:
    raw_date = str(activity.get("startTimeLocal") or "")[:10]
    if not raw_date:
        return None
    try:
        activity_date = date.fromisoformat(raw_date)
    except ValueError:
        return None

    # A moved session can live in a plan file named after another week.
    preferred = plan_path(monday_of(activity_date))
    targets = [preferred, *sorted(PLANS_DIR.glob("*.json"), reverse=True)]
    sessions: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for target in targets:
        if target in seen_paths or not target.is_file():
            continue
        seen_paths.add(target)
        try:
            plan = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        raw_sessions = plan.get("seances", []) if isinstance(plan, dict) else []
        if isinstance(raw_sessions, list):
            sessions.extend(
                session for session in raw_sessions if isinstance(session, dict)
            )

    # Garmin writes the originating workout ID into a completed guided activity.
    # It is stronger evidence than a date, especially after manual rescheduling.
    activity_workout_id = activity.get("workoutId")
    if activity_workout_id is not None:
        for session in sessions:
            garmin = session.get("garmin")
            planned_workout_id = (
                garmin.get("workout_id") if isinstance(garmin, dict) else None
            )
            if (
                planned_workout_id is not None
                and str(planned_workout_id) == str(activity_workout_id)
            ):
                return session

    # Date remains a fallback for manually recorded runs without a workout ID.
    return next(
        (session for session in sessions if session.get("date") == raw_date),
        None,
    )


def _activity_date(activity: dict[str, Any]) -> date | None:
    raw = str(activity.get("startTimeLocal") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _profile_context() -> dict[str, Any]:
    profile = {
        "objectif": "terminer un semi-marathon",
        "distance_km": 21.1,
        "frequence": "3 séances par semaine",
        "date_course": None,
    }
    try:
        payload = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return profile
    objective = payload.get("objective") if isinstance(payload, dict) else None
    if not isinstance(objective, dict):
        return profile
    title = str(objective.get("title") or "").strip()
    if title:
        profile["objectif"] = title
    distance = objective.get("distanceKm")
    if isinstance(distance, (int, float)):
        profile["distance_km"] = float(distance)
    frequency = objective.get("sessionsPerWeek")
    if isinstance(frequency, int):
        profile["frequence"] = f"{frequency} séances par semaine"
    race_date = str(objective.get("raceDate") or "").strip()
    if race_date:
        profile["date_course"] = race_date
    return profile


def completed_session_indexes(
    planned_sessions: list[dict[str, Any]], activities: list[dict[str, Any]]
) -> set[int]:
    completed: set[int] = set()
    for activity in activities:
        activity_date = str(activity.get("startTimeLocal") or "")[:10]
        activity_workout_id = activity.get("workoutId")
        for index, session in enumerate(planned_sessions):
            garmin = session.get("garmin")
            planned_workout_id = (
                garmin.get("workout_id") if isinstance(garmin, dict) else None
            )
            same_workout = (
                activity_workout_id is not None
                and planned_workout_id is not None
                and str(activity_workout_id) == str(planned_workout_id)
            )
            if same_workout or (
                activity_date and activity_date == str(session.get("date") or "")
            ):
                completed.add(index)
                break
    return completed


def _completed_session_count(
    planned_sessions: list[dict[str, Any]], activities: list[dict[str, Any]]
) -> int:
    return len(completed_session_indexes(planned_sessions, activities))


def session_statuses(
    planned_sessions: list[dict[str, Any]],
    activities: list[dict[str, Any]],
    today: date | None = None,
) -> list[str]:
    """Return the user-confirmed state of every planned session."""
    completed = completed_session_indexes(planned_sessions, activities)
    statuses: list[str] = []
    for index, session in enumerate(planned_sessions):
        if index in completed:
            statuses.append("terminee")
        elif session.get("statut") == "terminee":
            statuses.append("terminee")
        elif session.get("statut") == "skippee":
            statuses.append("skippee")
        else:
            statuses.append("a_venir")
    return statuses


def _context_for_week(week_start: date) -> dict[str, Any]:
    week_end = week_start + timedelta(days=6)
    next_start = week_start + timedelta(days=7)
    proposed_dates = [
        next_start + timedelta(days=1),
        next_start + timedelta(days=3),
        next_start + timedelta(days=6),
    ]
    all_activities = load_activities()
    week_activities = [
        activity
        for activity in all_activities
        if (activity_day := _activity_date(activity)) is not None
        and week_start <= activity_day <= week_end
    ]
    current_plan = load_plan(week_start)
    planned_sessions = current_plan.get("seances", []) if current_plan else []
    completed_count = session_statuses(planned_sessions, all_activities).count(
        "terminee"
    )
    adherence = f"{completed_count}/3" if len(planned_sessions) == 3 else "non mesurable"
    recent = [
        activity
        for activity in all_activities
        if (activity_day := _activity_date(activity)) is not None
        and week_start - timedelta(days=28) <= activity_day <= week_end
    ]
    conversation_feedback = [
        {"role": item.get("role"), "content": item.get("content")}
        for item in list_messages()[-20:]
        if item.get("role") in {"user", "assistant"}
    ]
    return {
        "profil": _profile_context(),
        "semaine_evaluee": {
            "debut": week_start.isoformat(),
            "fin": week_end.isoformat(),
            "plan": planned_sessions if current_plan else None,
            "adherence_calculee": adherence,
            "courses_realisees": [
                {
                    "mesures": _sanitized_metrics(activity),
                    "analyse_ia": load_analysis(str(activity.get("activityId", ""))),
                }
                for activity in week_activities
            ],
        },
        "historique_28_jours": [_sanitized_metrics(activity) for activity in recent],
        "retours_conversation": conversation_feedback,
        "semaine_suivante": {
            "debut": next_start.isoformat(),
            "dates_obligatoires": [item.isoformat() for item in proposed_dates],
        },
        "limites": [
            "Les jours préférés ne sont pas encore renseignés : mardi, jeudi et dimanche sont utilisés par défaut.",
            "Aucune information subjective sur la fatigue ou la douleur n'est disponible.",
        ],
    }


def generate_weekly_cycle(week_start: date, settings: Settings) -> WeeklyCycle:
    if not settings.openai_api_key:
        raise MissingOpenAIKeyError("OPENAI_API_KEY manque dans le fichier .env.")
    context = _context_for_week(week_start)
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.responses.parse(
        model=settings.openai_model,
        reasoning={"effort": "low"},
        instructions=WEEKLY_INSTRUCTIONS,
        input=json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        text_format=WeeklyCycle,
    )
    if response.output_parsed is None:
        raise RuntimeError("L'IA n'a pas produit de plan hebdomadaire structuré.")
    cycle = response.output_parsed
    adherence = context["semaine_evaluee"]["adherence_calculee"]
    cycle.revue.adherence = adherence
    if adherence == "non mesurable":
        prefix = "Aucun plan de trois séances n'existait : l'adhérence ne peut pas être mesurée. "
    else:
        completed = int(adherence[0])
        missing = 3 - completed
        prefix = (
            "Les trois séances planifiées ont été enregistrées. "
            if missing == 0
            else f"{missing} séance(s) planifiée(s) n'ont pas été enregistrée(s). "
        )
    cycle.revue.message_adherence = prefix + cycle.revue.message_adherence
    required_dates = context["semaine_suivante"]["dates_obligatoires"]
    for session, required_date in zip(cycle.seances_semaine_suivante, required_dates):
        session.date = required_date
    return cycle


def save_weekly_cycle(evaluated_week: date, cycle: WeeklyCycle) -> Path:
    ensure_directories()
    next_week = evaluated_week + timedelta(days=7)
    target = plan_path(next_week)
    payload = {
        "semaine": next_week.isoformat(),
        "genere_le": datetime.now().isoformat(timespec="seconds"),
        "semaine_evaluee": evaluated_week.isoformat(),
        "revue_precedente": cycle.revue.model_dump(mode="json"),
        "adaptation": cycle.adaptation,
        "seances": [
            session.model_dump(mode="json")
            for session in cycle.seances_semaine_suivante
        ],
        "avertissement_securite": cycle.avertissement_securite,
    }
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


def review_and_plan(evaluated_week: date, settings: Settings) -> Path:
    cycle = generate_weekly_cycle(evaluated_week, settings)
    return save_weekly_cycle(evaluated_week, cycle)


def maybe_review_and_plan(settings: Settings, today: date | None = None) -> Path | None:
    """Create the next plan once every session is completed or explicitly skipped."""
    if not settings.openai_api_key:
        return None
    current_week = monday_of(today or date.today())
    activities = load_activities()
    for evaluated_week in (current_week, current_week - timedelta(days=7)):
        current_plan = load_plan(evaluated_week)
        if not current_plan:
            continue
        sessions = current_plan.get("seances", [])
        if not isinstance(sessions, list) or len(sessions) != 3:
            continue
        statuses = session_statuses(sessions, activities, today or date.today())
        if any(status == "a_venir" for status in statuses):
            continue
        next_plan = plan_path(evaluated_week + timedelta(days=7))
        if next_plan.is_file():
            return None
        return review_and_plan(evaluated_week, settings)
    return None
