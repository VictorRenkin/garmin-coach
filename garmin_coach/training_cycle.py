from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .ai_provider import structured_completion
from .ai_analysis import MissingOpenAIKeyError, _sanitized_metrics, load_analysis
from .config import PLANS_DIR, PROFILE_FILE, Settings, ensure_directories
from .coach_chat import list_messages
from .local_activity_history import load_recent_local_activities
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
    adherence: str = Field(pattern=r"^(?:[0-7]/[1-7]|non mesurable)$")
    message_adherence: str = Field(min_length=15, max_length=400)
    bilan: str = Field(min_length=20, max_length=700)
    points_positifs: list[str] = Field(min_length=1, max_length=3)
    points_a_ameliorer: list[str] = Field(min_length=1, max_length=3)


class WeeklyCycle(BaseModel):
    revue: WeeklyReview
    adaptation: str = Field(min_length=20, max_length=500)
    seances_semaine_suivante: list[PlannedSession] = Field(
        min_length=1, max_length=7
    )
    avertissement_securite: str = Field(min_length=10, max_length=300)


class InitialPlan(BaseModel):
    synthese_historique: str = Field(min_length=20, max_length=600)
    logique_existante: str = Field(min_length=10, max_length=500)
    adaptation_objectif: str = Field(min_length=20, max_length=600)
    seances: list[PlannedSession] = Field(min_length=1, max_length=7)
    avertissement_securite: str = Field(min_length=10, max_length=300)


INITIAL_PLAN_INSTRUCTIONS = """
Tu crées le tout premier plan de course Motionest après l'onboarding d'un sportif.
Respecte exactement le nombre de séances et les dates obligatoires fournis.
Analyse l'historique et les entraînements Garmin existants avant de proposer la
progression. S'il existe une logique cohérente, conserve ses principes utiles ;
sinon repars prudemment de l'historique réel. Chaque séance doit respecter le
schéma Garmin, rester progressive et être justifiée par l'objectif confirmé.
Tiens compte des jours disponibles, des contraintes, des préférences et des
autres sports réellement observés. Une séance de natation ou de vélo peut
contribuer à la charge et à la récupération, mais ne remplace pas automatiquement
une séance de course. N'invente aucune douleur ou disponibilité. Si une douleur
actuelle est déclarée, n'augmente pas la charge ni l'intensité et conseille une
évaluation adaptée. Ne prescris jamais de courir malgré une douleur, ni de zone 5.
Ta sortie peut uniquement décrire l'objectif et les séances du schéma demandé ;
elle ne modifie jamais la configuration ni l'infrastructure de l'application.
""".strip()


WEEKLY_INSTRUCTIONS = """
Tu es un coach de course à pied prudent. Tu fais le bilan d'une semaine puis tu
prépares un plan strictement adapté à l'objectif et à la fréquence confirmés.

Règles :
- Réponds en français et fonde chaque conclusion sur les données fournies.
- Si aucun plan n'existait pour la semaine évaluée, l'adhérence est « non
  mesurable » : ne reproche pas au coureur de ne pas avoir suivi un plan absent.
- Si un plan existait et qu'une ou plusieurs séances manquent, indique
  clairement le nombre manquant, sans culpabiliser. N'augmente pas la charge de
  la semaine suivante pour « rattraper ».
- Une séance lente n'est pas un échec. Compare sa réalisation à son objectif,
  à sa distance, à son dénivelé et aux analyses disponibles.
- Les nouvelles séances doivent respecter exactement le nombre et les dates
  proposées, laisser de la récupération entre elles et avoir chacune un but
  mesurable et compréhensible.
- Respecte les jours disponibles et les contraintes déclarées dans le profil.
  Tiens compte de la natation, du vélo, du renforcement et des autres sports
  observés dans la charge globale sans les convertir arbitrairement en course.
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
- Ne propose aucune modification de la configuration ou de l'infrastructure :
  seules les séances structurées de ce plan peuvent varier.
""".strip()


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _session_offsets(count: int, available_days: list[str] | None = None) -> list[int]:
    """Choose plan days from the athlete's declared availability when possible."""
    day_numbers = {
        "mon": 0,
        "tue": 1,
        "wed": 2,
        "thu": 3,
        "fri": 4,
        "sat": 5,
        "sun": 6,
    }
    preferred = sorted(
        {
            day_numbers[day]
            for day in (available_days or [])
            if day in day_numbers
        }
    )
    if len(preferred) >= count:
        if count == 1:
            return [preferred[len(preferred) // 2]]
        selected = {
            preferred[round(index * (len(preferred) - 1) / (count - 1))]
            for index in range(count)
        }
        if len(selected) == count:
            return sorted(selected)

    offsets = sorted(
        {round(index * 6 / max(1, count - 1)) for index in range(count)}
    )
    while len(offsets) < count:
        offsets.append(next(day for day in range(7) if day not in offsets))
        offsets.sort()
    return offsets


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
        "objectif": "à définir avec le coureur",
        "distance_km": None,
        "frequence": None,
        "date_course": None,
        "jours_disponibles": [],
        "contraintes": "",
        "douleur_ou_blessure": "",
        "preferences": "",
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
    profile["jours_disponibles"] = objective.get("availableDays") or []
    profile["contraintes"] = str(objective.get("constraints") or "").strip()
    profile["douleur_ou_blessure"] = str(objective.get("injuryPain") or "").strip()
    profile["preferences"] = str(objective.get("preferences") or "").strip()
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
    all_activities = load_activities()
    week_activities = [
        activity
        for activity in all_activities
        if (activity_day := _activity_date(activity)) is not None
        and week_start <= activity_day <= week_end
    ]
    current_plan = load_plan(week_start)
    planned_sessions = current_plan.get("seances", []) if current_plan else []
    profile = _profile_context()
    frequency = profile.get("frequence")
    try:
        next_count = int(str(frequency).split()[0])
    except (TypeError, ValueError, IndexError):
        next_count = len(planned_sessions) if planned_sessions else 0
    if not 1 <= next_count <= 7:
        raise ValueError("La fréquence d'entraînement doit être définie entre 1 et 7.")
    offsets = _session_offsets(next_count, profile.get("jours_disponibles"))
    proposed_dates = [next_start + timedelta(days=day) for day in offsets]
    completed_count = session_statuses(planned_sessions, all_activities).count(
        "terminee"
    )
    adherence = (
        f"{completed_count}/{len(planned_sessions)}"
        if planned_sessions
        else "non mesurable"
    )
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
        "profil": profile,
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
            "nombre_exact_seances": next_count,
            "dates_obligatoires": [item.isoformat() for item in proposed_dates],
        },
        "limites": [
            "Les jours préférés ne sont pas encore renseignés : les séances sont réparties régulièrement.",
            "Aucune information subjective sur la fatigue ou la douleur n'est disponible.",
        ],
    }


def generate_weekly_cycle(week_start: date, settings: Settings) -> WeeklyCycle:
    if not settings.ai_api_key:
        raise MissingOpenAIKeyError("Aucune clé API d'IA n'est configurée.")
    context = _context_for_week(week_start)
    cycle = structured_completion(
        settings,
        instructions=WEEKLY_INSTRUCTIONS,
        payload=context,
        result_type=WeeklyCycle,
        reasoning_effort="low",
        category="plan_hebdomadaire",
        max_output_tokens=2600,
        quality="balanced",
    )
    adherence = context["semaine_evaluee"]["adherence_calculee"]
    cycle.revue.adherence = adherence
    if adherence == "non mesurable":
        prefix = "Aucun plan n'existait : l'adhérence ne peut pas être mesurée. "
    else:
        completed, total = (int(value) for value in adherence.split("/", 1))
        missing = total - completed
        prefix = (
            f"Les {total} séance(s) planifiée(s) ont été enregistrée(s). "
            if missing == 0
            else f"{missing} séance(s) planifiée(s) n'ont pas été enregistrée(s). "
        )
    cycle.revue.message_adherence = prefix + cycle.revue.message_adherence
    required_dates = context["semaine_suivante"]["dates_obligatoires"]
    if len(cycle.seances_semaine_suivante) != len(required_dates):
        raise RuntimeError("L'IA n'a pas respecté la fréquence confirmée.")
    for session, required_date in zip(cycle.seances_semaine_suivante, required_dates):
        session.date = required_date
    return cycle


def generate_initial_plan(settings: Settings, onboarding: dict[str, Any]) -> Path:
    """Crée un premier plan confirmé, sans supposer une semaine précédente."""
    if not settings.ai_api_key:
        raise MissingOpenAIKeyError("Aucune clé API d'IA n'est configurée.")
    try:
        stored = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("L'objectif doit être enregistré avant de créer le plan.") from exc
    objective = stored.get("objective") if isinstance(stored, dict) else None
    if not isinstance(objective, dict):
        raise ValueError("L'objectif doit être enregistré avant de créer le plan.")
    count = objective.get("sessionsPerWeek")
    if not isinstance(count, int) or not 1 <= count <= 7:
        raise ValueError("Le nombre de séances doit être compris entre 1 et 7.")

    week_start = monday_of(date.today()) + timedelta(days=7)
    offsets = _session_offsets(count, objective.get("availableDays"))
    required_dates = [(week_start + timedelta(days=day)).isoformat() for day in offsets]
    # Temporary bounded context: the 20 newest in-scope activities across every
    # sport. Metadata comes from the local multisport import; detail files stay
    # unopened here because the current AI contract only needs summary metrics.
    activities = load_recent_local_activities(limit=20)
    payload = {
        "objectif_confirme": objective,
        "inventaire_garmin": onboarding.get("garmin_summary"),
        "historique_recent": [_sanitized_metrics(item) for item in activities],
        "retours_conversation": [
            {"role": item.get("role"), "content": item.get("content")}
            for item in list_messages()[-30:]
            if item.get("role") in {"user", "assistant"}
        ],
        "nombre_exact_seances": count,
        "dates_obligatoires": required_dates,
    }
    plan = structured_completion(
        settings,
        instructions=INITIAL_PLAN_INSTRUCTIONS,
        payload=payload,
        result_type=InitialPlan,
        reasoning_effort="low",
        category="plan_initial",
        max_output_tokens=2200,
        quality="balanced",
    )
    if len(plan.seances) != count:
        raise RuntimeError("L'IA n'a pas respecté le nombre de séances confirmé.")
    for session, required_date in zip(plan.seances, required_dates):
        session.date = required_date
    ensure_directories()
    target = plan_path(week_start)
    target.write_text(
        json.dumps(
            {
                "semaine": week_start.isoformat(),
                "genere_le": datetime.now().isoformat(timespec="seconds"),
                "onboarding": True,
                "adaptation": plan.adaptation_objectif,
                "synthese_historique": plan.synthese_historique,
                "logique_existante": plan.logique_existante,
                "seances": [item.model_dump(mode="json") for item in plan.seances],
                "avertissement_securite": plan.avertissement_securite,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


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
    if not settings.ai_api_key:
        return None
    current_week = monday_of(today or date.today())
    activities = load_activities()
    for evaluated_week in (current_week, current_week - timedelta(days=7)):
        current_plan = load_plan(evaluated_week)
        if not current_plan:
            continue
        sessions = current_plan.get("seances", [])
        if not isinstance(sessions, list) or not sessions:
            continue
        statuses = session_statuses(sessions, activities, today or date.today())
        if any(status == "a_venir" for status in statuses):
            continue
        next_plan = plan_path(evaluated_week + timedelta(days=7))
        if next_plan.is_file():
            return None
        return review_and_plan(evaluated_week, settings)
    return None
