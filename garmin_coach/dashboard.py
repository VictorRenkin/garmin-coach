from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import logging
import os
import re
import threading
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import set_key
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
    GarthException,
)

from .account_security import (
    production_mode,
    public_auth_config,
    send_account_email,
    verify_human,
)
from .ai_analysis import analyze_and_save, load_analysis
from .activity_data import latest_hr_zone_boundaries, public_detail
from .athlete_memory import activity_trace, select_relevant_memory
from .conversation_sport_context import build_conversation_sport_context
from .coach_calendar_context import build_coach_calendar_context
from .coach_session_actions import SessionActionError, confirm_session_action, cancel_session_action
from .coach_chat import (
    add_assistant_prompt,
    claim_action,
    generate_response,
    get_message,
    list_messages,
    mark_action_applied,
    mark_action_rejected,
    mark_request_failed,
    release_action_claim,
    retry_request,
    store_response,
    submit_message,
)
from .config import ACTIVITIES_DIR, PLANS_DIR, PROFILE_FILE, REPORTS_DIR, ROOT_DIR, current_user_id, ensure_directories, load_settings, user_context
from .garmin import (
    GarminMFARequiredError,
    authenticate_with_credentials,
    connect,
    persist_client_tokens,
    stored_tokens_available,
)
from .garmin_setup import setup_status, start_initial_setup
from .garmin_delivery import (
    GarminDeliveryError,
    assert_upload_retry_safe,
    get_garmin_delivery_state,
    publish_accepted_session,
)
from .first_run_journey import build_first_run_journey, confirm_first_run_step
from .goal import goal_context_for_legacy_confidence
from .availability import build_availability_v1
from .goal import build_goal_v1
from .goal_assessment import (
    GoalAssessmentError,
    generate_goal_assessment,
    get_goal_assessment_state,
)
from .history_import import (
    initialize_history_import,
    load_history_import,
    validate_scope,
)
from .garmin_workouts import publish_plan, remove_future_sessions, replace_future_plan
from .manager import MANAGER_EMAIL, manager_authorized, manager_cookie, manager_login, manager_stats
from .user_accounts import (
    authenticate_account,
    confirm_email,
    create_session,
    ensure_user_record,
    issue_email_verification,
    issue_password_reset,
    register_account,
    record_login,
    reset_password,
    session_cookie,
    user_from_cookie,
    user_id_for_email,
    user_login_lock,
)
from .user_journey import determine_user_stage
from .onboarding import (
    complete_onboarding_v2_step,
    enable_discovery_without_garmin,
    inspect_garmin_account,
    load_onboarding,
    mark_complete,
    mark_existing_plan_continuation,
    mark_plan_proposed,
    mark_plan_rejected,
    mark_profile_ready,
    onboarding_v2_state,
    update_garmin_setup,
)
from .planning_decision import (
    PlanningDecisionError,
    confirm_planning_decision,
    generate_planning_recommendation,
    get_planning_decision_state,
    get_planning_recommendation_state,
)
from .plan_proposal import (
    PlanProposalError,
    generate_plan_proposal,
    get_plan_proposal_state,
    proposal_fingerprint,
    revise_plan_session,
    local_calendar_blocked_dates,
)
from .plan_acceptance import (
    PlanAcceptanceError,
    get_plan_acceptance_state,
    record_plan_acceptance_decision,
)
from .runtime_context import (
    build_runtime_motionest_context,
    load_motionest_inputs_v1,
    update_motionest_inputs_v1,
)
from .subjective_context import build_subjective_context_v1
from .summary import is_running
from .storage import load_activities
from .sync import sync_once
from .training_cycle import (
    PlannedSession,
    completed_session_indexes,
    load_plan,
    maybe_review_and_plan,
    monday_of,
    session_statuses,
    generate_initial_plan,
    find_planned_session,
)


TEMPLATE_FILE = Path(__file__).with_name("dashboard_v2.html")
LANDING_FILE = Path(__file__).with_name("landing.html")
TERMS_FILE = Path(__file__).with_name("terms.html")
PRIVACY_FILE = Path(__file__).with_name("privacy.html")
RESET_PASSWORD_FILE = Path(__file__).with_name("reset_password.html")
CONNECT_GARMIN_FILE = Path(__file__).with_name("connect_garmin.html")
ASSETS_DIR = Path(__file__).with_name("assets")
MANAGER_FILE = Path(__file__).with_name("manager.html")
LOGGER = logging.getLogger(__name__)
_INITIAL_PLAN_LOCK = threading.Lock()
DEFAULT_OBJECTIVE = {
    "goalType": "other",
    "title": "Objectif à définir",
    "distanceKm": None,
    "sessionsPerWeek": None,
    "raceDate": None,
    "goal_target_time": None,
    "availableDays": [],
    "constraints": "",
    "injuryPain": "",
    "preferences": "",
}


def _user_stage_redirect(stage: str) -> str:
    if stage == "verify_email":
        return "/"
    if stage in {"connect_garmin", "importing", "import_failed"}:
        return "/connect-garmin"
    return "/dashboard"


def coach_ai_status() -> dict[str, Any]:
    settings = load_settings()
    configured = bool(settings.ai_api_key)
    return {
        "date_actuelle": date.today().isoformat(),
        "configured": configured,
        "provider": settings.ai_provider if configured else None,
        "model": settings.ai_model if configured else None,
        "baseUrl": settings.ai_base_url if configured else None,
        "codexAvailable": True,
    }


def save_coach_ai_config(
    api_key: str,
    provider: str = "openai",
    model: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    key = api_key.strip()
    provider = provider.strip().lower() or "custom"
    providers = {
        "openai": ("gpt-5.6-sol", None),
        "nvidia": ("deepseek-ai/deepseek-v4-flash", "https://integrate.api.nvidia.com/v1"),
        "groq": ("openai/gpt-oss-120b", "https://api.groq.com/openai/v1"),
        "openrouter": ("openrouter/free", "https://openrouter.ai/api/v1"),
        "github": ("openai/gpt-4.1", "https://models.github.ai/inference"),
        "gemini": ("gemini-3.5-flash", "https://generativelanguage.googleapis.com/v1beta/openai/"),
        "custom": ("", None),
    }
    if provider not in providers:
        raise ValueError("Fournisseur d'IA inconnu.")
    if len(key) < 12:
        raise ValueError("La clé API semble trop courte.")
    default_model, default_url = providers[provider]
    selected_model = (model or default_model).strip()
    selected_url = (base_url or default_url or "").strip() or None
    if not selected_model:
        raise ValueError("Indique le nom du modèle à utiliser.")
    if provider == "custom" and not selected_url:
        raise ValueError("Indique l'URL compatible OpenAI du fournisseur.")
    if selected_url and not selected_url.startswith("https://"):
        raise ValueError("L'URL de l'API doit commencer par https://.")
    env_file = ROOT_DIR / ".env"
    values = {
        "AI_PROVIDER": provider,
        "AI_API_KEY": key,
        "AI_MODEL": selected_model,
        "AI_BASE_URL": selected_url or "",
    }
    for name, value in values.items():
        set_key(str(env_file), name, value, quote_mode="always")
        os.environ[name] = value
    return {
        "configured": True,
        "provider": provider,
        "model": selected_model,
        "baseUrl": selected_url,
        "codexAvailable": True,
    }


def load_objective() -> dict[str, Any]:
    user_id = current_user_id()
    if user_id:
        try:
            inputs = load_motionest_inputs_v1(user_id)
            if inputs.source == "v1" and inputs.goal is not None:
                objective = goal_context_for_legacy_confidence(inputs.goal)
                availability = inputs.availability
                return {
                    **DEFAULT_OBJECTIVE,
                    **objective,
                    "goalType": inputs.goal.intent,
                    "sessionsPerWeek": (
                        availability.preferred_sessions_per_week
                        if availability is not None
                        else None
                    ),
                    "availableDays": (
                        availability.available_days if availability is not None else []
                    ),
                    "source": "motionest_inputs_v1",
                }
        except Exception:
            LOGGER.warning(
                "Canonical objective unavailable for %s", user_id
            )
    if not PROFILE_FILE.is_file():
        return dict(DEFAULT_OBJECTIVE)
    try:
        payload = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_OBJECTIVE)
    objective = payload.get("objective") if isinstance(payload, dict) else None
    return objective if isinstance(objective, dict) else dict(DEFAULT_OBJECTIVE)


def compose_goal_target_time(
    hours: object = None, minutes: object = None, seconds: object = None
) -> str | None:
    """Build the canonical target time from user-facing numeric components."""
    values: list[int] = []
    for value, label in (
        (hours, "heures"),
        (minutes, "minutes"),
        (seconds, "secondes"),
    ):
        raw = str(value or "").strip()
        if raw and not raw.isdigit():
            raise ValueError(f"Le nombre de {label} doit être un entier positif.")
        values.append(int(raw or 0))
    parsed_hours, parsed_minutes, parsed_seconds = values
    if parsed_hours > 99:
        raise ValueError("Le nombre d’heures doit être compris entre 0 et 99.")
    if parsed_minutes > 59:
        raise ValueError("Le nombre de minutes doit être compris entre 0 et 59.")
    if parsed_seconds > 59:
        raise ValueError("Le nombre de secondes doit être compris entre 0 et 59.")
    if not any(values):
        return None
    return f"{parsed_hours:02d}:{parsed_minutes:02d}:{parsed_seconds:02d}"


def decompose_goal_target_time(value: object) -> dict[str, int]:
    """Return editable components for an existing canonical target time."""
    raw = str(value or "").strip()
    if not raw:
        return {"hours": 0, "minutes": 0, "seconds": 0}
    match = re.fullmatch(r"(\d{2}):([0-5]\d):([0-5]\d)", raw)
    if not match:
        raise ValueError("Le temps objectif enregistré est invalide.")
    return {
        "hours": int(match.group(1)),
        "minutes": int(match.group(2)),
        "seconds": int(match.group(3)),
    }


def save_objective(payload: dict[str, Any]) -> dict[str, Any]:
    title = str(payload.get("title") or "").strip()
    if not 3 <= len(title) <= 80:
        raise ValueError("Le titre de l'objectif doit contenir entre 3 et 80 caractères.")

    raw_distance = payload.get("distanceKm")
    distance = None if raw_distance in (None, "") else float(raw_distance)
    frequency = int(payload.get("sessionsPerWeek"))
    if distance is not None and not 1 <= distance <= 200:
        raise ValueError("La distance doit être comprise entre 1 et 200 km.")
    if not 1 <= frequency <= 7:
        raise ValueError("Le nombre de séances doit être compris entre 1 et 7.")

    race_date = str(payload.get("raceDate") or "").strip() or None
    if race_date:
        date.fromisoformat(race_date)

    goal_target_time = str(payload.get("goal_target_time") or "").strip() or None
    if goal_target_time:
        match = re.fullmatch(r"(\d{2}):([0-5]\d):([0-5]\d)", goal_target_time)
        if not match:
            raise ValueError("Le temps objectif doit utiliser le format HH:MM:SS.")

    goal_type = str(payload.get("goalType") or "other").strip().lower()
    if not 2 <= len(goal_type) <= 32 or not all(
        character.isalnum() or character in "-_" for character in goal_type
    ):
        raise ValueError("Le type d'objectif est invalide.")

    raw_days = payload.get("availableDays") or []
    if not isinstance(raw_days, list):
        raise ValueError("Les jours disponibles doivent être une liste.")
    allowed_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    available_days = list(dict.fromkeys(str(day).lower() for day in raw_days))
    if any(day not in allowed_days for day in available_days):
        raise ValueError("Un jour disponible est invalide.")
    if available_days and len(available_days) < frequency:
        raise ValueError(
            "Choisis au moins autant de jours disponibles que de séances par semaine."
        )

    def optional_note(name: str, label: str) -> str:
        value = str(payload.get(name) or "").strip()
        if len(value) > 600:
            raise ValueError(f"Le champ {label} ne peut pas dépasser 600 caractères.")
        return value

    current = load_objective()
    objective = {
        "goalType": goal_type,
        "title": title,
        "distanceKm": round(distance, 2) if distance is not None else None,
        "sessionsPerWeek": frequency,
        "raceDate": race_date,
        "goal_target_time": goal_target_time,
        "availableDays": available_days,
        "constraints": (
            optional_note("constraints", "contraintes")
            if "constraints" in payload
            else str(current.get("constraints") or "")
        ),
        "injuryPain": optional_note("injuryPain", "douleurs ou blessures"),
        "preferences": optional_note("preferences", "préférences"),
    }
    ensure_directories()
    PROFILE_FILE.write_text(
        json.dumps({"objective": objective}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return objective


def apply_objective_update(update: dict[str, Any]) -> dict[str, Any]:
    """Fusionne une proposition du coach avec l'objectif actuel puis la valide."""
    current = load_objective()
    return save_objective(
        {
            "goalType": update.get("goal_type", current.get("goalType")),
            "title": update.get("title", current.get("title")),
            "distanceKm": update.get("distance_km", current.get("distanceKm")),
            "sessionsPerWeek": update.get(
                "sessions_per_week", current.get("sessionsPerWeek")
            ),
            "raceDate": update.get("race_date", current.get("raceDate")),
            "goal_target_time": update.get(
                "goal_target_time", current.get("goal_target_time")
            ),
            "availableDays": current.get("availableDays", []),
            "constraints": current.get("constraints", ""),
            "injuryPain": current.get("injuryPain", ""),
            "preferences": current.get("preferences", ""),
        }
    )


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _public_activity(activity: dict[str, Any]) -> dict[str, Any]:
    distance_m = _number(activity.get("distance"))
    duration_s = _number(
        activity.get("movingDuration")
        or activity.get("duration")
        or activity.get("elapsedDuration")
    )
    pace_seconds = None
    if distance_m and duration_s and distance_m > 0:
        pace_seconds = duration_s / (distance_m / 1000)

    activity_id = str(activity.get("activityId", ""))
    source = str(activity.get("source") or "garmin")
    source_id = str(activity.get("sourceActivityId") or activity_id)
    return {
        "id": activity_id,
        "source": source,
        "garminActivityUrl": (
            f"https://www.strava.com/activities/{source_id}"
            if source.startswith("strava")
            else f"https://connect.garmin.com/modern/activity/{activity_id}"
        ),
        "name": str(activity.get("activityName") or "Course à pied"),
        "date": str(activity.get("startTimeLocal") or ""),
        "distanceKm": round(distance_m / 1000, 2) if distance_m is not None else None,
        "durationSeconds": round(duration_s) if duration_s is not None else None,
        "paceSeconds": round(pace_seconds) if pace_seconds is not None else None,
        "averageHr": _number(activity.get("averageHR")),
        "maxHr": _number(activity.get("maxHR")),
        "elevation": _number(activity.get("elevationGain")),
        "calories": _number(activity.get("calories")),
        "cadence": _number(activity.get("averageRunningCadenceInStepsPerMinute")),
        "vo2Max": _number(activity.get("vO2MaxValue")),
        "analysis": load_analysis(activity_id),
    }


def load_public_activities() -> list[dict[str, Any]]:
    activities: list[dict[str, Any]] = []
    if not ACTIVITIES_DIR.exists():
        return activities
    for path in ACTIVITIES_DIR.glob("*.json"):
        try:
            activity = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(activity, dict) and is_running(activity):
            activities.append(_public_activity(activity))
    return sorted(activities, key=lambda item: item["date"])


def queue_post_sync_coaching(
    user_id: str, activity_ids: list[str], *, first_connection: bool = False
) -> None:
    """Analyse les nouvelles séances puis prépare une adaptation sans la publier."""
    selected_ids = activity_ids[-1:] if first_connection else activity_ids

    def runner() -> None:
        with user_context(user_id):
            settings = load_settings()
            history = load_activities()
            if settings.ai_api_key:
                by_id = {
                    str(item.get("activityId") or ""): item
                    for item in history
                    if isinstance(item, dict)
                }
                for activity_id in selected_ids:
                    activity = by_id.get(activity_id)
                    if activity is None or load_analysis(activity_id) is not None:
                        continue
                    try:
                        analyze_and_save(
                            activity,
                            history,
                            settings,
                            find_planned_session(activity),
                        )
                    except Exception:
                        LOGGER.exception(
                            "Analyse post-synchronisation impossible pour l'activité %s",
                            activity_id,
                        )
            try:
                next_plan = maybe_review_and_plan(settings)
                if next_plan:
                    mark_plan_proposed(str(next_plan))
            except Exception:
                LOGGER.exception("Adaptation post-synchronisation impossible")

    threading.Thread(
        target=runner,
        daemon=True,
        name=f"motionest-post-sync-{user_id[:12]}",
    ).start()


def queue_coach_response(user_id: str, request_id: str) -> None:
    """RÃ©pond en arriÃ¨re-plan pour afficher immÃ©diatement l'indicateur de saisie."""
    def runner() -> None:
        with user_context(user_id):
            request = get_message(request_id)
            if not request or request.get("status") != "pending":
                return
            try:
                response = generate_response(request, load_settings(), coach_context())
                store_response(request_id, response)
                return
            except Exception:
                LOGGER.exception("Réponse du coach impossible")
            mark_request_failed(request_id)

    threading.Thread(
        target=runner,
        daemon=True,
        name=f"motionest-coach-{user_id[:12]}-{request_id[:8]}",
    ).start()


def coach_context() -> dict[str, Any]:
    """Contexte sportif utile, sans identité ni coordonnées GPS."""
    all_messages = list_messages()
    conversation = [
        {
            "role": item.get("role"),
            "content": item.get("content"),
            "action": item.get("action"),
            "action_status": item.get("action_status"),
            "session_action_preview": (
                {key: item["session_action_preview"].get(key) for key in ("draft_id", "status")}
                if isinstance(item.get("session_action_preview"), dict) else None
            ),
        }
        for item in all_messages[-8:]
        if item.get("role") in {"user", "assistant"}
    ]
    latest_user_text = next(
        (
            str(item.get("content") or "") for item in reversed(all_messages)
            if item.get("role") == "user"
        ),
        "",
    )
    all_activities = load_public_activities()
    activities = all_activities[-5:]
    raw_plan = load_relevant_plan()
    plan = None
    if isinstance(raw_plan, dict):
        plan = {
            key: raw_plan.get(key)
            for key in (
                "semaine",
                "adaptation",
                "seances_terminees",
                "seances_skippees",
                "seances_restantes",
            )
        }
        sessions = raw_plan.get("seances", [])
        plan["seances"] = [
            {
                key: session.get(key)
                for key in (
                    "date",
                    "titre",
                    "type",
                    "objectif",
                    "raison",
                    "duree_estimee_minutes",
                    "distance_estimee_km",
                    "consigne_intensite",
                    "statut",
                    "terminee",
                    "confirmation_requise",
                )
            }
            for session in sessions
            if isinstance(session, dict)
        ]
    user_id = current_user_id()
    multisport_context: dict[str, Any] = {
        "status": "unavailable", "reason": "no_active_user"
    }
    if user_id:
        try:
            multisport_context = build_conversation_sport_context(user_id)
        except Exception as exc:
            LOGGER.warning("Multisport coach context unavailable (%s)", type(exc).__name__)
            multisport_context = {
                "status": "unavailable", "reason": "canonical_context_unavailable"
            }
    canonical_inputs: dict[str, Any] | None = None
    journey: dict[str, Any] | None = None
    planning_decision: dict[str, Any] | None = None
    calendar_context: dict[str, Any] = {"status": "unavailable"}
    if user_id:
        # Read the same canonical proposal as the dashboard, independently of
        # onboarding stage and historical-plan availability.
        calendar_context = build_coach_calendar_context(user_id)
        try:
            inputs = load_motionest_inputs_v1(user_id)
            canonical_inputs = inputs.model_dump(mode="json")
            journey = build_first_run_journey(user_id)
            planning_decision = journey.get("planning_decision")
        except Exception as exc:
            LOGGER.warning(
                "Canonical coach context unavailable for %s (%s)",
                user_id,
                type(exc).__name__,
            )
    return {
        "objectif": load_objective(),
        "connexion_sportive": {
            "garmin_connected": bool(user_id and stored_tokens_available()),
            "discovery_without_garmin": bool(user_id and not stored_tokens_available() and load_onboarding().get("discovery_without_garmin") is True),
        },
        "declarations_v1": canonical_inputs,
        "contexte_multisport": multisport_context,
        "etape_parcours": journey.get("stage") if journey else None,
        "decision_frequence": planning_decision,
        "plan_actif": plan,
        "calendrier_plan_v2": calendar_context,
        "courses_recentes": [
            {key: value for key, value in activity.items() if key != "analysis"}
            for activity in activities
        ],
        "trace_sportive": activity_trace(all_activities),
        "memoire_pertinente": select_relevant_memory(latest_user_text),
        "conversation_recente": conversation,
        "onboarding": load_onboarding(),
    }


def coach_acceptance_followup(confirmation: str) -> dict[str, Any] | None:
    """Informe réellement l'IA d'une acceptation et conserve sa réponse."""
    request = submit_message(
        f"J'accepte cette proposition. Résultat appliqué : {confirmation}"
    )
    try:
        return store_response(
            request["id"],
            generate_response(request, load_settings(), coach_context()),
        )
    except Exception:
        mark_request_failed(str(request["id"]))
        return None


def queue_coach_acceptance_followup(confirmation: str) -> None:
    """Ne bloque jamais l'application pendant la réaction conversationnelle."""
    user_id = current_user_id()
    def runner() -> None:
        if user_id:
            with user_context(user_id):
                coach_acceptance_followup(confirmation)
        else:
            coach_acceptance_followup(confirmation)
    threading.Thread(
        target=runner,
        daemon=True,
        name="coach-acceptance-followup",
    ).start()


def load_relevant_plan() -> dict[str, Any] | None:
    onboarding = load_onboarding()
    proposed = Path(str(onboarding.get("proposed_plan") or ""))
    if (
        onboarding.get("status") == "plan_proposed"
        and proposed.is_file()
        and proposed.parent.resolve() == PLANS_DIR.resolve()
    ):
        plan = load_plan(proposed.stem)
    else:
        plan = None
    current_week = monday_of(date.today())
    plan = plan or load_plan(current_week) or load_plan(current_week + timedelta(days=7))
    if not plan:
        return None
    sessions = plan.get("seances")
    if not isinstance(sessions, list):
        return plan
    statuses = session_statuses(sessions, load_activities())
    public_plan = dict(plan)
    today = date.today().isoformat()
    public_plan["seances"] = [
        {
            **session,
            "statut": statuses[index],
            "terminee": statuses[index] == "terminee",
            "confirmation_requise": (
                statuses[index] == "a_venir"
                and str(session.get("date") or "") < today
            ),
        }
        if isinstance(session, dict)
        else session
        for index, session in enumerate(sessions)
    ]
    public_plan["seances_terminees"] = statuses.count("terminee")
    public_plan["seances_skippees"] = statuses.count("skippee")
    public_plan["seances_restantes"] = statuses.count("a_venir")
    return public_plan



def relevant_plan_path() -> Path | None:
    current_week = monday_of(date.today())
    for week_start in (current_week, current_week + timedelta(days=7)):
        target = PLANS_DIR / f"{week_start.isoformat()}.json"
        if target.is_file():
            return target
    return None


def adapt_plan(target: Path, adjustment: str) -> dict[str, Any]:
    factors = {"lighter": 0.8, "progressive": 1.1, "recovery": 0.7}
    if adjustment not in factors:
        raise ValueError("Adaptation du plan invalide.")
    payload = json.loads(target.read_text(encoding="utf-8"))
    sessions = payload.get("seances")
    if not isinstance(sessions, list):
        raise ValueError("Aucune séance adaptable dans le plan.")
    factor = factors[adjustment]
    changed = 0
    today = date.today().isoformat()
    if any(isinstance(session, dict) and session.get("blocks") is not None
           and str(session.get("date") or "") >= today for session in sessions):
        raise ValueError("L'adaptation globale ne représente pas les blocs distincts. Précise une modification de séance bloc par bloc.")
    for session in sessions:
        if not isinstance(session, dict) or str(session.get("date") or "") < today:
            continue
        for key in ("duree_estimee_minutes", "effort_minutes"):
            if isinstance(session.get(key), (int, float)):
                session[key] = max(1, round(session[key] * factor))
        if isinstance(session.get("distance_estimee_km"), (int, float)):
            session["distance_estimee_km"] = round(session["distance_estimee_km"] * factor, 1)
        if adjustment == "recovery":
            session.update(
                {
                    "type": "récupération",
                    "titre": "Récupération facile",
                    "bloc_zone_fc": 2,
                    "repetitions_bloc": 1,
                    "objectif": "Récupérer avec une course facile, sans rechercher la vitesse ni augmenter la charge.",
                    "consigne_intensite": "Rester en aisance respiratoire et arrêter en cas de douleur inhabituelle.",
                }
            )
        changed += 1
    if not changed:
        raise ValueError("Aucune séance future à adapter.")
    labels = {"lighter": "allégée de 20 %", "progressive": "augmentée prudemment de 10 %", "recovery": "convertie en récupération"}
    payload["adaptation"] = f"Semaine {labels[adjustment]} après confirmation dans le chat."
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"plan": payload, "changed_sessions": changed}


def modify_planned_session(
    target: Path, session_index: int, patch: dict[str, Any]
) -> dict[str, Any]:
    """Applique uniquement les champs sportifs validés d'une séance."""
    payload = json.loads(target.read_text(encoding="utf-8"))
    sessions = payload.get("seances", [])
    if not isinstance(sessions, list) or not 0 <= session_index < len(sessions):
        raise ValueError("Séance introuvable.")
    current = sessions[session_index]
    if not isinstance(current, dict):
        raise ValueError("Séance invalide.")
    forbidden = {"date", "garmin", "statut", "terminee", "confirmation_requise"}
    if forbidden.intersection(patch):
        raise ValueError("La proposition contient un champ non modifiable.")
    allowed = set(PlannedSession.model_fields) - {"date"}
    unknown = set(patch) - allowed
    if unknown:
        raise ValueError(f"Champ de séance inconnu : {sorted(unknown)[0]}")
    candidate = {**current, **patch, "date": current.get("date")}
    homogeneous = {"repetitions_bloc", "effort_minutes", "recuperation_minutes"}
    supplied = {key for key in homogeneous if patch.get(key) is not None}
    if patch.get("blocks") is not None:
        if supplied:
            raise ValueError("Les blocs distincts et les champs du bloc homogène sont exclusifs.")
        candidate.update({key: None for key in homogeneous})
    elif current.get("blocks") is not None and supplied:
        if supplied != homogeneous:
            raise ValueError("Le remplacement des blocs nécessite les trois champs du bloc homogène.")
        candidate["blocks"] = None
    validated = PlannedSession.model_validate(candidate).model_dump(mode="json")
    if isinstance(current.get("garmin"), dict):
        validated["garmin"] = current["garmin"]
    sessions[session_index] = validated
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return validated


def _calendar_items(client: Any, target_date: str) -> list[dict[str, Any]]:
    day = date.fromisoformat(target_date)
    calendar = client.connectapi(
        f"/calendar-service/year/{day.year}/month/{day.month - 1}"
    )
    items = calendar.get("calendarItems", []) if isinstance(calendar, dict) else []
    return [item for item in items if isinstance(item, dict)]


def reschedule_session(
    client: Any, target: Path, session_index: int, new_date: str
) -> dict[str, Any]:
    requested_date = date.fromisoformat(new_date).isoformat()
    payload = json.loads(target.read_text(encoding="utf-8"))
    sessions = payload.get("seances", [])
    if not isinstance(sessions, list) or not 0 <= session_index < len(sessions):
        raise ValueError("Séance introuvable.")
    session = sessions[session_index]
    if not isinstance(session, dict):
        raise ValueError("Séance invalide.")
    old_date = str(session.get("date") or "")
    if old_date == requested_date:
        return session
    garmin = session.get("garmin")
    workout_id = garmin.get("workout_id") if isinstance(garmin, dict) else None
    if not workout_id:
        raise ValueError("Cette séance n'est pas encore publiée dans Garmin.")

    old_items = [
        item
        for item in _calendar_items(client, old_date)
        if item.get("itemType") == "workout"
        and item.get("workoutId") == workout_id
        and item.get("date") == old_date
    ]
    client.schedule_workout(workout_id, requested_date)
    try:
        for item in old_items:
            client.garth.delete(
                "connectapi", f"/workout-service/schedule/{item['id']}", api=True
            )
    except Exception:
        for item in _calendar_items(client, requested_date):
            if item.get("workoutId") == workout_id and item.get("date") == requested_date:
                client.garth.delete(
                    "connectapi", f"/workout-service/schedule/{item['id']}", api=True
                )
        raise

    session["date"] = requested_date
    session.pop("statut", None)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return session


def mark_session_skipped(target: Path, session_index: int) -> dict[str, Any]:
    payload = json.loads(target.read_text(encoding="utf-8"))
    sessions = payload.get("seances", [])
    if not isinstance(sessions, list) or not 0 <= session_index < len(sessions):
        raise ValueError("Séance introuvable.")
    session = sessions[session_index]
    if not isinstance(session, dict):
        raise ValueError("Séance invalide.")
    session["statut"] = "skippee"
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return session


def _json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(body)


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _mobile_authorized(self) -> bool:
        """Protège l'API quand elle est exposée au réseau local."""
        expected = os.getenv("GARMIN_COACH_MOBILE_TOKEN", "").strip()
        if not expected:
            return True
        supplied = self.headers.get("X-Garmin-Coach-Token", "").strip()
        return hmac.compare_digest(supplied, expected)

    def _loopback_request(self) -> bool:
        """Return whether both the client and requested host are local."""
        try:
            client_is_local = ipaddress.ip_address(self.client_address[0]).is_loopback
            host = urlparse(f"//{self.headers.get('Host', '')}").hostname
            host_is_local = bool(
                host
                and (
                    host.lower() == "localhost"
                    or ipaddress.ip_address(host).is_loopback
                )
            )
        except ValueError:
            return False
        return client_is_local and host_is_local

    def _local_account_bypass(self) -> bool:
        """Enable the explicit no-email local account flow on loopback only."""
        enabled = os.getenv("MOTIONEST_LOCAL_AUTH_BYPASS", "").strip().lower()
        return enabled in {"1", "true", "yes", "on"} and self._loopback_request()

    def _local_auth_bypass(self) -> bool:
        """Allow Turnstile-free loopback testing without weakening public access."""
        return self._loopback_request() and (
            production_mode() or self._local_account_bypass()
        )

    def _authorize_api(self, path: str) -> bool:
        if not path.startswith("/api/") or self._mobile_authorized():
            return True
        _json_response(self, {"ok": False, "message": "Jeton mobile invalide."}, 401)
        return False

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.end_headers()

    def _require_user(self, path: str) -> str | None:
        user_id = user_from_cookie(self.headers.get("Cookie", ""))
        if user_id:
            return user_id
        if path == "/dashboard":
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
        else:
            _json_response(self, {"ok": False, "message": "Connexion utilisateur requise."}, 401)
        return None

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        public = path in {
            "/",
            "/terms",
            "/privacy",
            "/favicon.ico",
            "/verify-email",
            "/reset-password",
            "/api/public/auth-config",
            "/api/mobile/health",
        } or path.startswith("/assets/")
        manager = path == "/manager" or path.startswith("/api/manager/")
        if public or manager:
            self._do_GET()
            return
        user_id = self._require_user(path)
        if not user_id:
            return
        with user_context(user_id):
            if path in {"/dashboard", "/connect-garmin"}:
                stage = determine_user_stage(user_id)
                if stage == "verify_email":
                    self.send_response(302)
                    self.send_header("Location", "/")
                    self.end_headers()
                    return
                if path == "/dashboard" and stage in {
                    "connect_garmin",
                    "importing",
                    "import_failed",
                }:
                    self.send_response(302)
                    self.send_header("Location", "/connect-garmin")
                    self.end_headers()
                    return
                if path == "/connect-garmin" and stage in {
                    "onboarding",
                    "review_garmin",
                    "complete_declarations",
                    "review_understanding",
                    "goal_assessment",
                    "planning_recommendation",
                    "confirm_frequency",
                    "ready_for_plan",
                }:
                    self.send_response(302)
                    self.send_header("Location", "/dashboard")
                    self.end_headers()
                    return
                if path == "/connect-garmin" and stage == "dashboard" and stored_tokens_available():
                    self.send_response(302)
                    self.send_header("Location", "/dashboard")
                    self.end_headers()
                    return
            self._do_GET()

    def _do_GET(self) -> None:
        path = urlparse(self.path).path
        if not self._authorize_api(path):
            return
        if path == "/favicon.ico":
            body = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#07110f"/><path d="M6 23V9h4l6 8 6-8h4v14h-4v-8l-6 8-6-8v8z" fill="#c9f457"/></svg>'
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/":
            body = LANDING_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path in {"/terms", "/privacy", "/reset-password"}:
            source = {
                "/terms": TERMS_FILE,
                "/privacy": PRIVACY_FILE,
                "/reset-password": RESET_PASSWORD_FILE,
            }[path]
            body = source.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/verify-email":
            token = parse_qs(urlparse(self.path).query).get("token", [""])[0]
            self.send_response(302)
            self.send_header(
                "Location", "/?verified=1" if confirm_email(token) else "/?verification=invalid"
            )
            self.end_headers()
            return
        if path == "/api/public/auth-config":
            _json_response(
                self, public_auth_config(local_bypass=self._local_auth_bypass())
            )
            return
        if path == "/dashboard":
            body = TEMPLATE_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/connect-garmin":
            body = CONNECT_GARMIN_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/manager":
            if not manager_authorized(self.headers.get("Cookie", "")):
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            body = MANAGER_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/assets/"):
            filename = Path(unquote(path.removeprefix("/assets/"))).name
            asset = ASSETS_DIR / filename
            if not asset.is_file() or asset.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                self.send_error(404)
                return
            body = asset.read_bytes()
            content_type = "image/webp" if asset.suffix.lower() == ".webp" else "image/png" if asset.suffix.lower() == ".png" else "image/jpeg"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/activities":
            _json_response(self, {"activities": load_public_activities()})
            return
        if path == "/api/plan":
            _json_response(self, {"plan": load_relevant_plan()})
            return
        if path.startswith("/api/activity/") and path.endswith("/details"):
            parts = path.split("/")
            activity_id = parts[3] if len(parts) == 5 else ""
            if not activity_id.isdigit():
                _json_response(self, {"ok": False, "message": "Course invalide."}, 400)
                return
            detail = public_detail(activity_id)
            if detail is None:
                _json_response(
                    self,
                    {"ok": False, "message": "Détails sportifs indisponibles."},
                    404,
                )
                return
            _json_response(self, {"ok": True, "detail": detail})
            return
        if path == "/api/profile":
            _json_response(
                self,
                {
                    "objective": load_objective(),

                    "heartRateZones": latest_hr_zone_boundaries(),
                },
            )
            return
        if path == "/api/coach/messages":
            _json_response(self, {"messages": list_messages()})
            return
        if path == "/api/mobile/health":
            _json_response(self, {"ok": True, "service": "Motionest", "version": 1})
            return
        if path == "/api/coach/status":
            _json_response(self, coach_ai_status())
            return
        if path == "/api/onboarding":
            user_id = current_user_id()
            inputs = None
            onboarding = load_onboarding()
            try:
                inputs = load_motionest_inputs_v1(user_id) if user_id else None
                onboarding = onboarding_v2_state(
                    inputs_source=inputs.source if inputs is not None else "legacy_profile",
                    inputs=inputs,
                )
                runtime = (
                    build_runtime_motionest_context(user_id)
                    if user_id
                    else None
                )
                runtime_payload: dict[str, Any] = (
                    runtime.model_dump(mode="json")
                    if runtime is not None
                    else {
                        "schema_version": 1,
                        "status": "unavailable",
                        "diagnostics": [{"code": "authenticated_user_missing"}],
                    }
                )
            except Exception as exc:
                # Keep the existing onboarding readable if local runtime data is
                # damaged. Log only an opaque user id and exception type.
                LOGGER.warning(
                    "Motionest runtime context unavailable for %s (%s)",
                    user_id,
                    type(exc).__name__,
                )
                runtime_payload = {
                    "schema_version": 1,
                    "status": "unavailable",
                    "diagnostics": [{"code": "runtime_context_unavailable"}],
                }
            _json_response(
                self,
                {
                    "onboarding": onboarding,
                    "motionestInputs": (
                        inputs.model_dump(mode="json") if inputs is not None else None
                    ),
                    "motionestRuntime": runtime_payload,
                },
            )
            return
        if path == "/api/first-run-journey":
            user_id = current_user_id()
            if not user_id:
                _json_response(
                    self,
                    {"ok": False, "message": "Utilisateur Motionest indisponible."},
                    401,
                )
                return
            try:
                journey = build_first_run_journey(user_id)
            except Exception as exc:
                LOGGER.warning(
                    "First-run journey unavailable for %s (%s)",
                    user_id,
                    type(exc).__name__,
                )
                _json_response(
                    self,
                    {
                        "ok": False,
                        "stage": "import_failed",
                        "message": "Le parcours Motionest est temporairement indisponible.",
                    },
                    500,
                )
                return
            _json_response(self, {"ok": True, "journey": journey})
            return
        if path == "/api/goal-assessment":
            user_id = current_user_id()
            if not user_id:
                _json_response(
                    self,
                    {"ok": False, "message": "Utilisateur Motionest indisponible."},
                    401,
                )
                return
            try:
                state = get_goal_assessment_state(user_id)
            except GoalAssessmentError as exc:
                _json_response(
                    self,
                    {
                        "ok": True,
                        "status": "missing",
                        "assessment": None,
                        "diagnostics": [exc.code],
                    },
                )
                return
            except Exception:
                LOGGER.exception("Lecture GoalAssessment indisponible")
                _json_response(
                    self,
                    {"ok": False, "message": "GoalAssessment est indisponible."},
                    500,
                )
                return
            _json_response(self, {"ok": True, **state.model_dump(mode="json")})
            return
        if path == "/api/planning-recommendation":
            user_id = current_user_id()
            if not user_id:
                _json_response(
                    self,
                    {"ok": False, "message": "Utilisateur Motionest indisponible."},
                    401,
                )
                return
            try:
                state = get_planning_recommendation_state(user_id)
            except PlanningDecisionError as exc:
                _json_response(
                    self,
                    {
                        "ok": True,
                        "status": "missing",
                        "recommendation": None,
                        "diagnostics": [exc.code],
                    },
                )
                return
            except Exception:
                LOGGER.exception("Lecture PlanningRecommendation indisponible")
                _json_response(
                    self,
                    {"ok": False, "message": "PlanningRecommendation est indisponible."},
                    500,
                )
                return
            _json_response(self, {"ok": True, **state.model_dump(mode="json")})
            return
        if path == "/api/planning-decision":
            user_id = current_user_id()
            if not user_id:
                _json_response(
                    self,
                    {"ok": False, "message": "Utilisateur Motionest indisponible."},
                    401,
                )
                return
            try:
                state = get_planning_decision_state(user_id)
            except PlanningDecisionError as exc:
                _json_response(
                    self,
                    {
                        "ok": True,
                        "status": "missing",
                        "decision": None,
                        "plan_constraints": None,
                        "diagnostics": [exc.code],
                    },
                )
                return
            except Exception:
                LOGGER.exception("Lecture PlanningDecision indisponible")
                _json_response(
                    self,
                    {"ok": False, "message": "PlanningDecision est indisponible."},
                    500,
                )
                return
            _json_response(self, {"ok": True, **state.model_dump(mode="json")})
            return
        if path == "/api/plan-proposal":
            user_id = current_user_id()
            if not user_id:
                _json_response(self, {"ok": False, "message": "Utilisateur Motionest indisponible."}, 401)
                return
            try:
                state = get_plan_proposal_state(user_id)
            except PlanProposalError as exc:
                _json_response(
                    self,
                    {"ok": True, "status": "missing", "proposal": None, "diagnostics": [exc.code]},
                )
                return
            except Exception:
                LOGGER.exception("Lecture PlanProposal indisponible")
                _json_response(self, {"ok": False, "message": "PlanProposal est indisponible."}, 500)
                return
            payload = state.model_dump(mode="json")
            payload["proposal_fingerprint"] = (
                proposal_fingerprint(state.proposal) if state.proposal is not None else None
            )
            _json_response(self, {"ok": True, **payload})
            return
        if path == "/api/plan-proposal/decision":
            user_id = current_user_id()
            if not user_id:
                _json_response(self, {"ok": False, "message": "Utilisateur Motionest indisponible."}, 401)
                return
            try:
                state = get_plan_acceptance_state(user_id)
            except PlanProposalError as exc:
                _json_response(
                    self,
                    {"ok": True, "status": "missing", "decision": None, "diagnostics": [exc.code]},
                )
                return
            except Exception:
                LOGGER.exception("Lecture PlanAcceptance indisponible")
                _json_response(self, {"ok": False, "message": "PlanAcceptance est indisponible."}, 500)
                return
            _json_response(self, {"ok": True, **state.model_dump(mode="json")})
            return
        if path == "/api/garmin/workout-delivery":
            user_id = current_user_id()
            if not user_id:
                _json_response(
                    self,
                    {"ok": False, "message": "Utilisateur Motionest indisponible."},
                    401,
                )
                return
            try:
                state = get_garmin_delivery_state(user_id)
            except (GarminDeliveryError, PlanProposalError) as exc:
                _json_response(
                    self,
                    {
                        "ok": True,
                        "status": "unavailable",
                        "sessions": [],
                        "code": exc.code,
                        "message": str(exc),
                    },
                )
                return
            except Exception:
                LOGGER.exception("Lecture du suivi Garmin indisponible")
                _json_response(
                    self,
                    {"ok": False, "message": "Le suivi Garmin est indisponible."},
                    500,
                )
                return
            _json_response(self, {"ok": True, **state})
            return
        if path == "/api/garmin/setup-status":
            _json_response(
                self,
                {
                    "ok": True,
                    "connected": stored_tokens_available(),
                    "setup": setup_status(),
                    "historyImport": load_history_import(),
                },
            )
            return
        if path == "/api/manager/stats":
            if not manager_authorized(self.headers.get("Cookie", "")):
                _json_response(self, {"ok": False, "message": "Accès administrateur requis."}, 401)
                return
            _json_response(self, manager_stats())
            return
        if path.startswith("/reports/"):
            filename = Path(unquote(path.removeprefix("/reports/"))).name
            report = REPORTS_DIR / filename
            if not report.is_file() or report.suffix.lower() != ".md":
                self.send_error(404)
                return
            body = report.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {
            "/api/account/login",
            "/api/account/register",
            "/api/account/resend-verification",
            "/api/account/forgot-password",
            "/api/account/reset-password",
            "/api/login",
            "/api/logout",
            "/api/manager/logout",
        }:
            self._do_POST()
            return
        if path == "/api/coach/config":
            self._do_POST()
            return
        user_id = self._require_user(path)
        if not user_id:
            return
        with user_context(user_id):
            self._do_POST()

    def _do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._authorize_api(path):
            return
        if path == "/api/onboarding/continue-without-garmin":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 4096:
                    raise ValueError("Requête trop volumineuse.")
                self.rfile.read(length)  # No client-supplied identity is used.
            except ValueError as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, 400)
                return
            user_id = current_user_id()
            if not user_id or determine_user_stage(user_id) == "verify_email":
                _json_response(self, {"ok": False, "message": "Confirme ton compte Motionest avant de continuer."}, 403)
                return
            if stored_tokens_available():
                _json_response(self, {"ok": False, "message": "Garmin est déjà connecté; son parcours reste disponible."}, 409)
                return
            enable_discovery_without_garmin()
            _json_response(self, {"ok": True, "next": "/dashboard"})
            return
        if (path in {"/api/sync", "/api/garmin/workout-delivery", "/api/onboarding/plan-decision", "/api/plan/reschedule"}
                and load_onboarding().get("discovery_without_garmin") is True and not stored_tokens_available()):
            _json_response(self, {"ok": False, "code": "garmin_not_connected", "message": "Connecte Garmin avant cette action. Ton profil reste conservé."}, 409)
            return
        if path == "/api/logout":
            _json_response(
                self,
                {"ok": True, "redirect": "/"},
                headers={
                    "Set-Cookie": (
                        "motionest_session=; HttpOnly; SameSite=Strict; "
                        "Path=/; Max-Age=0"
                    )
                },
            )
            return
        if path in {
            "/api/onboarding/goal",
            "/api/onboarding/availability",
            "/api/onboarding/subjective-context",
        }:
            user_id = current_user_id()
            if not user_id:
                _json_response(self, {"ok": False, "message": "Utilisateur Motionest indisponible."}, 401)
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 0 or content_length > 16_384:
                    raise ValueError("Requête onboarding trop volumineuse.")
                body = json.loads(self.rfile.read(content_length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("Requête onboarding invalide.")
                now = datetime.now().astimezone().isoformat(timespec="seconds")
                current = load_motionest_inputs_v1(user_id)
                if path.endswith("/goal"):
                    intent = body.get("intent")
                    event_type = body.get("eventType") if intent == "race" else None
                    distance_km = body.get("distanceKm") if intent == "race" else None
                    if event_type in {"trail", "custom"} and (
                        isinstance(distance_km, bool)
                        or not isinstance(distance_km, (int, float))
                        or distance_km <= 0
                    ):
                        raise ValueError("Une distance positive est obligatoire pour ce type de course.")
                    existing_created = current.goal.created_at if current.goal else None
                    goal = build_goal_v1(
                        intent,
                        event_type=event_type,
                        distance_km=distance_km,
                        race_date=(body.get("raceDate") or None) if intent == "race" else None,
                        target_time=(body.get("targetTime") or None) if intent == "race" else None,
                        created_at=existing_created or now,
                        updated_at=now,
                    )
                    inputs = update_motionest_inputs_v1(user_id, goal=goal)
                    step = "goal"
                elif path.endswith("/availability"):
                    if "sessionsPerWeek" in body:
                        raise ValueError("La fréquence ne fait pas partie de cet onboarding.")
                    preferred = body.get("preferredSessionsPerWeek")
                    availability = build_availability_v1(
                        body.get("availableDays") or [],
                        preferred_sessions_per_week=preferred,
                        long_session_preferred_days=[],
                        double_session_days=body.get("doubleSessionDays") or [],
                        minimum_recovery_hours=body.get("minimumRecoveryHours"),
                        max_daily_duration_minutes=body.get("maxDailyDurationMinutes"),
                    )
                    inputs = update_motionest_inputs_v1(
                        user_id, availability=availability
                    )
                    step = "availability"
                else:
                    affecting = body.get("painOrInjuryAffectingTraining")
                    if not isinstance(affecting, bool):
                        raise ValueError("Réponds explicitement oui ou non pour la douleur.")
                    description = body.get("painDescription") if affecting else None
                    activities = body.get("unrecordedActivities") or []
                    preferences = body.get("importantPreferences") or []
                    if not isinstance(activities, list) or not isinstance(preferences, list):
                        raise ValueError("Contexte subjectif invalide.")
                    subjective = build_subjective_context_v1(
                        affecting_training=affecting,
                        pain_description=description,
                        unrecorded_activities=activities,
                        important_preferences=preferences,
                        reported_at=now,
                    )
                    inputs = update_motionest_inputs_v1(
                        user_id, subjective_context=subjective
                    )
                    step = "subjective_context"
                onboarding = complete_onboarding_v2_step(step)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, 400)
                return
            _json_response(
                self,
                {
                    "ok": True,
                    "onboarding": onboarding,
                    "motionestInputs": inputs.model_dump(mode="json"),
                    "nextStep": onboarding.get("input_progress", {}).get("current_step"),
                },
            )
            return
        if path in {
            "/api/first-run-journey/confirm-observations",
            "/api/first-run-journey/confirm-understanding",
        }:
            user_id = current_user_id()
            if not user_id:
                _json_response(
                    self,
                    {"ok": False, "message": "Utilisateur Motionest indisponible."},
                    401,
                )
                return
            step = "observations" if path.endswith("observations") else "understanding"
            try:
                journey = confirm_first_run_step(user_id, step)
            except ValueError as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, 409)
                return
            _json_response(self, {"ok": True, "journey": journey})
            return
        if path == "/api/manager/logout":
            _json_response(
                self,
                {"ok": True, "redirect": "/"},
                headers={
                    "Set-Cookie": (
                        "motionest_manager=; HttpOnly; SameSite=Strict; "
                        "Path=/; Max-Age=0"
                    )
                },
            )
            return
        if path in {
            "/api/account/login",
            "/api/account/register",
            "/api/account/resend-verification",
            "/api/account/forgot-password",
            "/api/account/reset-password",
        }:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 0 or content_length > 16_384:
                    raise ValueError("Requête de connexion trop volumineuse.")
                body = json.loads(self.rfile.read(content_length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("Requête de connexion invalide.")
                email = str(body.get("email") or "").strip()
                password = str(body.get("password") or "")
                if path != "/api/account/reset-password" and not email:
                    raise ValueError("L’adresse e-mail est requise.")
                if path in {"/api/account/login", "/api/account/register"} and not password:
                    raise ValueError("L’adresse e-mail et le mot de passe sont requis.")
                if path == "/api/account/register":
                    if password != str(body.get("passwordConfirm") or ""):
                        raise ValueError("Les deux mots de passe ne correspondent pas.")
                    if not verify_human(
                        str(body.get("turnstileToken") or ""),
                        self.client_address[0],
                        str(body.get("company") or ""),
                        local_bypass=self._local_auth_bypass(),
                    ):
                        _json_response(self, {"ok": False, "message": "La vérification anti-robot a échoué."}, 400)
                        return
                    account = register_account(
                        email,
                        password,
                        terms_accepted=body.get("termsAccepted") is True,
                    )
                    if self._local_account_bypass():
                        if not confirm_email(str(account["verificationToken"])):
                            raise ValueError("La validation locale du compte a échoué.")
                        user_id = str(account["id"])
                        next_step = determine_user_stage(user_id)
                        record_login(
                            str(account["email"]),
                            user_id,
                            imported=0,
                            onboarding_complete=next_step
                            in {"connect_garmin", "dashboard"},
                        )
                        user_session = create_session(user_id)
                        _json_response(
                            self,
                            {
                                "ok": True,
                                "role": "athlete",
                                "created": True,
                                "verificationPending": False,
                                "nextStep": next_step,
                                "redirect": _user_stage_redirect(next_step),
                                "message": "Compte local créé et connecté.",
                                "user": {"email": account["email"]},
                            },
                            status=201,
                            headers={"Set-Cookie": session_cookie(user_session)},
                        )
                        return
                    verification_url = send_account_email(
                        str(account["email"]), "verify", str(account["verificationToken"])
                    )
                    response = {
                        "ok": True,
                        "verificationPending": True,
                        "message": "Compte créé. Consulte ta messagerie pour confirmer ton adresse.",
                    }
                    if not production_mode() or self._local_auth_bypass():
                        response["developmentVerificationUrl"] = verification_url
                    _json_response(self, response, status=201)
                    return
                if path == "/api/account/resend-verification":
                    pending = issue_email_verification(email)
                    if pending:
                        send_account_email(pending[0], "verify", pending[1])
                    _json_response(self, {"ok": True, "message": "Si ce compte attend une confirmation, un nouveau lien a été envoyé."})
                    return
                if path == "/api/account/forgot-password":
                    reset = issue_password_reset(email)
                    development_url = send_account_email(reset[0], "reset", reset[1]) if reset else ""
                    response = {"ok": True, "message": "Si un compte confirmé correspond à cette adresse, un lien a été envoyé."}
                    if development_url and os.getenv("MOTIONEST_ENV", "development").lower() != "production":
                        response["developmentResetUrl"] = development_url
                    _json_response(self, response)
                    return
                if path == "/api/account/reset-password":
                    if password != str(body.get("passwordConfirm") or ""):
                        raise ValueError("Les deux mots de passe ne correspondent pas.")
                    if not reset_password(str(body.get("token") or ""), password):
                        _json_response(self, {"ok": False, "message": "Ce lien est invalide ou a expiré."}, 400)
                        return
                    _json_response(self, {"ok": True, "message": "Ton mot de passe a été modifié.", "redirect": "/?passwordReset=1"})
                    return

                account = authenticate_account(email, password)
                if account is None:
                    _json_response(
                        self,
                        {"ok": False, "message": "Adresse e-mail ou mot de passe incorrect."},
                        status=401,
                    )
                    return
                if not account.get("emailVerified") and self._local_account_bypass():
                    pending = issue_email_verification(email)
                    if pending is None or not confirm_email(pending[1]):
                        raise ValueError("La validation locale du compte a échoué.")
                    account["emailVerified"] = True
                if not account.get("emailVerified"):
                    pending = issue_email_verification(email)
                    verification_url = ""
                    if pending:
                        verification_url = send_account_email(
                            pending[0], "verify", pending[1]
                        )
                    response = {
                        "ok": False,
                        "verificationPending": True,
                        "nextStep": "verify_email",
                        "redirect": "/",
                        "message": "Confirme d’abord ton adresse e-mail.",
                    }
                    if verification_url and (
                        not production_mode() or self._local_auth_bypass()
                    ):
                        response["developmentVerificationUrl"] = verification_url
                    _json_response(self, response, status=403)
                    return
                remember = body.get("remember") is True
                user_id = str(account["id"])
                next_step = determine_user_stage(user_id)
                record_login(
                    str(account["email"]),
                    user_id,
                    imported=0,
                    onboarding_complete=next_step in {"connect_garmin", "dashboard"},
                )
                user_session = create_session(user_id, remember=remember)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, status=400)
                return
            _json_response(
                self,
                {
                    "ok": True,
                    "role": "athlete",
                    "created": False,
                    "nextStep": next_step,
                    "redirect": _user_stage_redirect(next_step),
                    "message": "Connexion réussie.",
                    "user": {
                        "email": account["email"],
                    },
                },
                headers={"Set-Cookie": session_cookie(user_session, remember=remember)},
            )
            return
        if path == "/api/garmin/connect":
            user_id = current_user_id()
            stage = determine_user_stage(user_id) if user_id else None
            discovery_connection = (stage == "dashboard" and load_onboarding().get("discovery_without_garmin") is True
                                    and not stored_tokens_available())
            if not user_id or (not discovery_connection and stage not in {
                "connect_garmin",
                "import_failed",
            }):
                _json_response(
                    self,
                    {"ok": False, "message": "Termine d’abord l’étape Motionest en cours."},
                    409,
                )
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 0 or content_length > 16_384:
                    raise ValueError("Requête de connexion trop volumineuse.")
                body = json.loads(self.rfile.read(content_length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("Requête de connexion invalide.")
                email = str(body.get("email") or "").strip()
                password = str(body.get("password") or "")
                mfa_code = str(body.get("mfaCode") or "").strip()
                history_scope = validate_scope(body.get("historyScope"))
                if not email or not password:
                    raise ValueError("L’adresse e-mail et le mot de passe Garmin sont requis.")
                client = authenticate_with_credentials(
                    email, password, mfa_code or None
                )
                persist_client_tokens(client)
                history_import = initialize_history_import(history_scope)
                update_garmin_setup("connected", message="Garmin connecté ✓")
                started = start_initial_setup(current_user_id())
            except GarminMFARequiredError:
                _json_response(
                    self,
                    {
                        "ok": False,
                        "mfaRequired": True,
                        "message": "Saisis le code de vérification envoyé par Garmin.",
                    },
                    409,
                )
                return
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, 400)
                return
            except GarminConnectAuthenticationError:
                _json_response(self, {"ok": False, "message": "Identifiants Garmin incorrects."}, 401)
                return
            except GarminConnectTooManyRequestsError:
                _json_response(self, {"ok": False, "message": "Garmin limite temporairement les connexions."}, 429)
                return
            except (GarminConnectConnectionError, GarthException):
                _json_response(self, {"ok": False, "message": "Garmin Connect est momentanément indisponible."}, 503)
                return
            except Exception:
                LOGGER.exception("Erreur interne pendant la connexion Garmin Motionest")
                _json_response(self, {"ok": False, "message": "Le serveur Motionest a rencontré une erreur interne."}, 500)
                return
            _json_response(
                self,
                {
                    "ok": True,
                    "started": started,
                    "historyImport": history_import,
                    "message": "Garmin connecté. L’import démarre en arrière-plan.",
                },
                202,
            )
            return
        if path == "/api/goal-assessment":
            user_id = current_user_id()
            if not user_id:
                _json_response(
                    self,
                    {"ok": False, "message": "Utilisateur Motionest indisponible."},
                    401,
                )
                return
            try:
                state = generate_goal_assessment(user_id)
            except GoalAssessmentError as exc:
                status = {
                    "goal_unavailable": 409,
                    "ai_not_configured": 503,
                    "provider_unavailable": 502,
                    "provider_timeout": 504,
                    "provider_rate_limited": 429,
                    "provider_quota_exhausted": 503,
                    "invalid_provider_response": 502,
                    "unknown_supporting_fact": 502,
                    "supporting_fact_required": 502,
                    "assessment_scope_mismatch": 502,
                    "assessment_scope_incompatible": 502,
                    "confidence_incompatible": 502,
                    "duplicate_supporting_fact": 502,
                }.get(exc.code, 422)
                _json_response(
                    self,
                    {"ok": False, "code": exc.code, "message": str(exc)},
                    status,
                )
                return
            except Exception:
                LOGGER.exception("Génération GoalAssessment indisponible")
                _json_response(
                    self,
                    {"ok": False, "message": "GoalAssessment est indisponible."},
                    500,
                )
                return
            _json_response(self, {"ok": True, **state.model_dump(mode="json")})
            return
        if path == "/api/planning-recommendation":
            user_id = current_user_id()
            if not user_id:
                _json_response(
                    self,
                    {"ok": False, "message": "Utilisateur Motionest indisponible."},
                    401,
                )
                return
            try:
                state = generate_planning_recommendation(user_id)
            except PlanningDecisionError as exc:
                status = {
                    "goal_unavailable": 409,
                    "availability_unavailable": 409,
                    "goal_assessment_required": 409,
                    "goal_assessment_stale": 409,
                    "ai_not_configured": 503,
                    "provider_unavailable": 502,
                    "provider_timeout": 504,
                    "provider_rate_limited": 429,
                    "provider_quota_exhausted": 503,
                    "invalid_provider_response": 502,
                    "unknown_supporting_fact": 502,
                    "duplicate_supporting_fact": 502,
                    "recommendation_outside_availability": 502,
                }.get(exc.code, 422)
                _json_response(
                    self, {"ok": False, "code": exc.code, "message": str(exc)}, status
                )
                return
            except Exception:
                LOGGER.exception("Génération PlanningRecommendation indisponible")
                _json_response(
                    self,
                    {"ok": False, "message": "PlanningRecommendation est indisponible."},
                    500,
                )
                return
            _json_response(self, {"ok": True, **state.model_dump(mode="json")})
            return
        if path == "/api/planning-decision":
            user_id = current_user_id()
            if not user_id:
                _json_response(
                    self,
                    {"ok": False, "message": "Utilisateur Motionest indisponible."},
                    401,
                )
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 0 or content_length > 4_096:
                    raise ValueError("Requête de décision trop volumineuse.")
                body = json.loads(self.rfile.read(content_length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("Requête de décision invalide.")
                state = confirm_planning_decision(
                    user_id,
                    confirmed_sessions_per_week=body.get(
                        "confirmedSessionsPerWeek",
                        body.get("confirmed_sessions_per_week"),
                    ),
                    recommendation_fingerprint=str(
                        body.get(
                            "recommendationFingerprint",
                            body.get("recommendation_fingerprint", ""),
                        )
                        or ""
                    ),
                )
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, 400)
                return
            except PlanningDecisionError as exc:
                status = {
                    "goal_unavailable": 409,
                    "availability_unavailable": 409,
                    "goal_assessment_required": 409,
                    "goal_assessment_stale": 409,
                    "recommendation_stale": 409,
                    "recommendation_insufficient_context": 409,
                    "decision_invalid": 422,
                    "decision_stale": 409,
                }.get(exc.code, 422)
                _json_response(
                    self, {"ok": False, "code": exc.code, "message": str(exc)}, status
                )
                return
            except Exception:
                LOGGER.exception("Confirmation PlanningDecision indisponible")
                _json_response(
                    self,
                    {"ok": False, "message": "PlanningDecision est indisponible."},
                    500,
                )
                return
            _json_response(self, {"ok": True, **state.model_dump(mode="json")})
            return
        if path == "/api/plan-proposal/generate":
            user_id = current_user_id()
            if not user_id:
                _json_response(self, {"ok": False, "message": "Utilisateur Motionest indisponible."}, 401)
                return
            try:
                state = generate_plan_proposal(user_id)
            except PlanProposalError as exc:
                status = 503 if exc.code == "ai_not_configured" else 409
                _json_response(self, {"ok": False, "code": exc.code, "message": str(exc)}, status)
                return
            except Exception:
                LOGGER.exception("Génération PlanProposal indisponible")
                _json_response(self, {"ok": False, "message": "PlanProposal est indisponible."}, 500)
                return
            _json_response(self, {"ok": True, **state.model_dump(mode="json")})
            return
        if path == "/api/plan-proposal/session":
            # This previously unwired endpoint must not bypass draft confirmation.
            _json_response(self, {"ok": False, "code": "confirmation_required",
                "message": "Demande un aperçu de la modification au coach, puis confirme ce brouillon avant toute écriture locale."}, 409)
            return
        if path == "/api/plan-proposal/decision":
            user_id = current_user_id()
            if not user_id:
                _json_response(self, {"ok": False, "message": "Utilisateur Motionest indisponible."}, 401)
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 0 or content_length > 8_192:
                    raise ValueError("Requête de décision trop volumineuse.")
                body = json.loads(self.rfile.read(content_length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("Requête de décision invalide.")
                state = record_plan_acceptance_decision(
                    user_id,
                    decision=body.get("decision"),
                    input_fingerprint=str(body.get("input_fingerprint") or ""),
                    expected_proposal_fingerprint=str(body.get("proposal_fingerprint") or ""),
                    feedback=body.get("feedback"),
                    **({"session_id": str(body["session_id"])} if body.get("session_id") is not None else {}),
                )
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, 400)
                return
            except PlanAcceptanceError as exc:
                status = 422 if exc.code == "decision_invalid" else 409
                _json_response(self, {"ok": False, "code": exc.code, "message": str(exc)}, status)
                return
            except PlanProposalError as exc:
                _json_response(self, {"ok": False, "code": exc.code, "message": str(exc)}, 409)
                return
            except Exception:
                LOGGER.exception("Décision PlanAcceptance indisponible")
                _json_response(self, {"ok": False, "message": "PlanAcceptance est indisponible."}, 500)
                return
            _json_response(self, {"ok": True, **state.model_dump(mode="json")})
            return
        if path == "/api/garmin/workout-delivery":
            user_id = current_user_id()
            if not user_id:
                _json_response(
                    self,
                    {"ok": False, "message": "Utilisateur Motionest indisponible."},
                    401,
                )
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 0 or content_length > 4_096:
                    raise ValueError("Requête Garmin trop volumineuse.")
                body = json.loads(self.rfile.read(content_length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("Requête Garmin invalide.")
                session_index = int(body.get("session_index"))
                requested_date = body.get("scheduled_date")
                scheduled_date = (
                    date.fromisoformat(str(requested_date)) if requested_date else None
                )
                try:
                    proposal_state = get_plan_proposal_state(user_id)
                except PlanProposalError:
                    proposal_state = None  # The existing publisher remains authoritative for unavailable legacy plans.
                if proposal_state is not None and proposal_state.proposal is not None and 0 <= session_index < len(proposal_state.proposal.sessions):
                    selected = proposal_state.proposal.sessions[session_index]
                    if selected.session_id is not None:
                        if body.get("session_id") != selected.session_id:
                            raise GarminDeliveryError("session_identity_conflict", "L'identité exacte de la séance est requise.")
                        if get_plan_acceptance_state(user_id, proposal_state=proposal_state, session_id=selected.session_id).status != "accepted":
                            raise GarminDeliveryError("session_not_accepted", "Valide explicitement cette séance avant son envoi.")
                assert_upload_retry_safe(user_id, session_index=session_index,
                    expected_session_id=str(body["session_id"]) if body.get("session_id") is not None else None)
                delivery = publish_accepted_session(
                    user_id,
                    connect(load_settings()),
                    session_index=session_index,
                    scheduled_date=scheduled_date,
                    **({"expected_session_id": str(body["session_id"])} if body.get("session_id") is not None else {}),
                )
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, 400)
                return
            except GarminDeliveryError as exc:
                status = 422 if exc.code in {
                    "sport_not_supported",
                    "session_structure_invalid",
                    "session_duration_mismatch",
                    "scheduled_date_in_past",
                } else 409
                _json_response(
                    self,
                    {"ok": False, "code": exc.code, "message": str(exc)},
                    status,
                )
                return
            except Exception:
                LOGGER.exception("Envoi de la séance Garmin indisponible")
                _json_response(
                    self,
                    {
                        "ok": False,
                        "message": "Garmin Connect n'a pas confirmé l'envoi.",
                    },
                    502,
                )
                return
            _json_response(self, {"ok": True, "delivery": delivery})
            return
        if path == "/api/garmin/setup-retry":
            user_id = current_user_id()
            if not user_id or determine_user_stage(user_id) not in {
                "connect_garmin",
                "import_failed",
            }:
                _json_response(
                    self,
                    {"ok": False, "message": "Aucun setup Garmin à relancer."},
                    409,
                )
                return
            if not stored_tokens_available():
                _json_response(
                    self,
                    {"ok": False, "message": "Reconnecte d’abord Garmin."},
                    409,
                )
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 0 or content_length > 4_096:
                    raise ValueError("Requête de reprise trop volumineuse.")
                body = json.loads(self.rfile.read(content_length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("Requête de reprise invalide.")
                history_scope = validate_scope(body.get("historyScope"))
                history_import = initialize_history_import(history_scope)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, 400)
                return
            started = start_initial_setup(current_user_id())
            _json_response(
                self,
                {
                    "ok": True,
                    "started": started,
                    "historyImport": history_import,
                    "message": "Nouvelle tentative lancée sans reconnexion Garmin.",
                },
                202,
            )
            return
        if path == "/api/login":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 0 or content_length > 16_384:
                    raise ValueError("Requête de connexion trop volumineuse.")
                body = json.loads(self.rfile.read(content_length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("Requête de connexion invalide.")
                email = str(body.get("email") or "").strip()
                password = str(body.get("password") or "")
                mfa_code = str(body.get("mfaCode") or "").strip()
                if not email or not password:
                    raise ValueError("L’adresse e-mail et le mot de passe sont requis.")
                if email.lower() == MANAGER_EMAIL.lower():
                    token = manager_login(email, password)
                    if not token:
                        _json_response(self, {"ok": False, "message": "Identifiants administrateur incorrects."}, 401)
                        return
                    _json_response(
                        self,
                        {"ok": True, "role": "manager", "redirect": "/manager", "message": "Connexion administrateur réussie."},
                        headers={"Set-Cookie": manager_cookie(token)},
                    )
                    return
                proposed_user_id, _ = user_id_for_email(email)
                # L'authentification réseau précède toute création de compte.
                client = (
                    authenticate_with_credentials(email, password, mfa_code)
                    if mfa_code
                    else authenticate_with_credentials(email, password)
                )
                user_id, _ = ensure_user_record(email, proposed_user_id)
                with user_login_lock(user_id):
                    with user_context(user_id):
                        first_connection = load_onboarding().get("status") == "not_started"
                        persist_client_tokens(client)
                        if first_connection:
                            inspect_garmin_account(client)
                        created = sync_once(
                            client,
                            settings=load_settings(),
                            limit=100 if first_connection else 20,
                        )
                        onboarding_complete = load_onboarding().get("status") == "complete"
                record_login(
                    email,
                    user_id,
                    imported=len(created),
                    onboarding_complete=onboarding_complete,
                )
                if created:
                    queue_post_sync_coaching(
                        user_id,
                        [path.stem.rsplit("-", 1)[-1] for path in created],
                        first_connection=first_connection,
                    )
            except GarminMFARequiredError as exc:
                _json_response(
                    self,
                    {
                        "ok": False,
                        "mfaRequired": True,
                        "message": (
                            "Garmin a envoyé un code de vérification par e-mail. "
                            "Saisis-le pour terminer la connexion."
                        ),
                    },
                    status=409,
                )
                return
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, status=400)
                return
            except GarminConnectAuthenticationError:
                _json_response(
                    self,
                    {"ok": False, "message": "Adresse e-mail ou mot de passe Garmin incorrect."},
                    status=401,
                )
                return
            except GarminConnectTooManyRequestsError:
                _json_response(
                    self,
                    {
                        "ok": False,
                        "message": (
                            "Garmin a temporairement limité les connexions après plusieurs essais. "
                            "Patiente quelques minutes avant de réessayer."
                        ),
                    },
                    status=429,
                )
                return
            except (GarminConnectConnectionError, GarthException):
                _json_response(self, {"ok": False, "message": "Garmin Connect est momentanément indisponible."}, status=503)
                return
            except Exception:
                LOGGER.exception("Erreur interne pendant la connexion Garmin")
                _json_response(self, {"ok": False, "message": "Le serveur Motionest a rencontré une erreur interne."}, status=500)
                return
            user_session = create_session(user_id)
            _json_response(
                self,
                {
                    "ok": True,
                    "role": "athlete",
                    "redirect": "/dashboard",
                    "imported": len(created),
                    "firstConnection": first_connection,
                    "message": (
                        f"Première connexion : {len(created)} activité(s) importée(s) et historique analysé."
                        if first_connection
                        else f"Mise à jour terminée : {len(created)} nouvelle(s) activité(s) trouvée(s)."
                        if created
                        else "Mise à jour terminée : aucune nouvelle activité."
                    ),
                }, headers={"Set-Cookie": session_cookie(user_session)},
            )
            return
        if path == "/api/profile":
            try:
                user_id = current_user_id()
                if user_id:
                    inputs = load_motionest_inputs_v1(user_id)
                    if inputs.source == "v1" and inputs.goal is not None:
                        _json_response(
                            self,
                            {
                                "ok": False,
                                "code": "canonical_v1_inputs_active",
                                "message": "Modifie cet objectif depuis le parcours Motionest.",
                            },
                            409,
                        )
                        return
                content_length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(content_length) or b"{}")
                objective = save_objective(body)
                mark_profile_ready()
                days = objective.get("availableDays") or []
                day_names = {
                    "mon": "lundi", "tue": "mardi", "wed": "mercredi",
                    "thu": "jeudi", "fri": "vendredi", "sat": "samedi",
                    "sun": "dimanche",
                }
                sessions = int(objective["sessionsPerWeek"])
                availability = (
                    f" sur tes jours disponibles ({', '.join(day_names.get(day, day) for day in days)})"
                    if days else ""
                )
                add_assistant_prompt(
                    (
                        f"Merci, j’ai maintenant les éléments utiles. Pour « {objective['title']} », "
                        f"je te propose de construire une semaine de {sessions} "
                        f"entraînement{'s' if sessions > 1 else ''}{availability}, avec une progression adaptée à ton objectif "
                        "et à tes préférences. On peut d’abord discuter des séances proposées ici : "
                        "dis-moi si tu préfères commencer prudemment, normalement, ou avec une semaine légère."
                    ),
                    "garmin-onboarding-training-discussion",
                )
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, status=400)
                return
            _json_response(
                self,
                {"ok": True, "objective": objective, "message": "Objectif enregistré."},
            )
            return
        if path in {"/api/coach/session-action/confirm", "/api/coach/session-action/cancel"}:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= content_length <= 4096:
                    raise ValueError("Confirmation trop volumineuse.")
                body = json.loads(self.rfile.read(content_length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("Confirmation invalide.")
                operation = confirm_session_action if path.endswith("/confirm") else cancel_session_action
                preview = operation(current_user_id(), str(body.get("draftId") or ""))
            except SessionActionError as exc:
                _json_response(self, {"ok": False, "code": exc.code, "message": str(exc)}, 409)
                return
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, 400)
                return
            except OSError as exc:
                LOGGER.warning("Local session action interrupted (%s)", type(exc).__name__)
                _json_response(self, {"ok": False, "code": "local_action_interrupted",
                    "message": "Application interrompue. Actualise pour vérifier son résultat ; elle n'est pas déclarée annulée."}, 503)
                return
            messages = {
                "confirmed": "Cette modification locale est confirmée. Aucun envoi Garmin.",
                "cancelled": "Ce brouillon est annulé ; il n'a pas modifié le plan.",
                "applying": "L'application a commencé ; vérifie son résultat avant toute autre action.",
            }
            _json_response(self, {"ok": True, "preview": preview,
                "message": messages.get(preview.get("status"), "Vérifie l'état actuel du brouillon.")})
            return
        if path == "/api/coach/messages":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(content_length) or b"{}")
                message = submit_message(
                    str(body.get("message") or ""),
                    str(body.get("coachMode") or "balanced"),
                )
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, status=400)
                return
            queue_coach_response(current_user_id(), str(message["id"]))
            _json_response(
                self,
                {
                    "ok": True,
                    "message": "Le coach prépare sa réponse.",
                    "request": message,
                },
                status=202,
            )
            return
        if path == "/api/coach/retry":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(content_length) or b"{}")
                request = retry_request(str(body.get("requestId") or ""))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, status=400)
                return
            queue_coach_response(current_user_id(), str(request["id"]))
            _json_response(
                self,
                {"ok": True, "message": "Le coach réessaie.", "request": request},
                status=202,
            )
            return
        if path == "/api/coach/config":
            if not manager_authorized(self.headers.get("Cookie", "")):
                _json_response(self, {"ok": False, "message": "Accès administrateur requis."}, 401)
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(content_length) or b"{}")
                extra = any(name in body for name in ("provider", "model", "baseUrl"))
                if extra:
                    status = save_coach_ai_config(
                        str(body.get("apiKey") or ""),
                        str(body.get("provider") or "custom"),
                        str(body.get("model") or "") or None,
                        str(body.get("baseUrl") or "") or None,
                    )
                else:
                    status = save_coach_ai_config(str(body.get("apiKey") or ""))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, status=400)
                return
            _json_response(self, {"ok": True, **status})
            return
        if path == "/api/coach/action-reject":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(content_length) or b"{}")
                answer = get_message(str(body.get("answerId") or ""))
                if not answer or answer.get("role") != "assistant" or answer.get("action") == "none":
                    raise ValueError("Proposition du coach introuvable.")
                if answer.get("action_status") in {"applied", "rejected"}:
                    raise ValueError("Cette proposition a déjà reçu une réponse.")
                mark_action_rejected(str(answer.get("id") or ""))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, status=400)
                return
            _json_response(self, {"ok": True, "message": "Proposition refusée."})
            return
        if path == "/api/coach/action":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(content_length) or b"{}")
                answer = get_message(str(body.get("answerId") or ""))
                if not answer or answer.get("role") != "assistant":
                    raise ValueError("Proposition du coach introuvable.")
                if answer.get("action_status") in {"applied", "rejected"}:
                    raise ValueError("Cette proposition a déjà reçu une réponse.")
                action = answer.get("action")
                action_onboarding = load_onboarding()
                if action in {"modify_session", "reschedule_session", "delete_sessions", "adapt_plan"}:
                    has_v2 = action_onboarding.get("onboarding_version") == 2
                    try:
                        has_v2 = has_v2 or get_plan_proposal_state(current_user_id()).status == "available"
                    except PlanProposalError:
                        pass  # Historical users without canonical planning inputs remain compatible.
                    if has_v2:
                        _json_response(self, {"ok": False, "code": "v2_calendar_action_required",
                            "message": "Cette ancienne proposition ne peut pas modifier le calendrier V2. Demande un aperçu local de la séance actuelle."}, 409)
                        return
                if (action_onboarding.get("discovery_without_garmin") is True and not stored_tokens_available()
                        and action != "update_objective"):
                    _json_response(self, {"ok": False, "code": "garmin_not_connected", "message": "Connecte Garmin avant une action sur un plan; tes déclarations restent modifiables."}, 409)
                    return
                if (
                    action_onboarding.get("onboarding_version") == 2
                    and action in {"create_plan", "update_objective"}
                ):
                    _json_response(
                        self,
                        {
                            "ok": False,
                            "code": "planning_flow_not_ready",
                            "message": (
                                "Confirme d'abord la recommandation d'entraînement "
                                "avant de préparer le plan."
                                if action == "create_plan"
                                else "Modifie ton objectif depuis le parcours structuré Motionest."
                            ),
                        },
                        status=409,
                    )
                    return
                if action == "create_plan":
                    existing_plan = load_relevant_plan()
                    remaining = (
                        existing_plan.get("seances_restantes", 0)
                        if isinstance(existing_plan, dict)
                        else 0
                    )
                    if remaining:
                        action = "continue_existing_plan"
                if action == "update_objective":
                    update = answer.get("objective_update") or {}
                    objective = apply_objective_update(update)
                    mark_profile_ready()
                    result = {"objective": objective}
                    confirmation = "Objectif confirmé."
                elif action == "create_plan":
                    onboarding = action_onboarding
                    if onboarding.get("status") != "ready":
                        raise ValueError(
                            "Confirme d'abord l'objectif et le nombre de séances."
                        )
                    if not stored_tokens_available() or not isinstance(
                        onboarding.get("garmin_summary"), dict
                    ):
                        raise ValueError(
                            "Connecte et importe Garmin avant de préparer le plan."
                        )
                    settings = load_settings()
                    target = generate_initial_plan(settings, onboarding)
                    mark_plan_proposed(str(target))
                    result = {"plan": str(target), "published": 0}
                    confirmation = (
                        "Proposition créée sans envoi. Vérifie la liste puis choisis OK ou Pas OK."
                    )
                elif action == "continue_existing_plan":
                    plan = load_relevant_plan()
                    remaining = plan.get("seances_restantes", 0) if isinstance(plan, dict) else 0
                    if not remaining:
                        raise ValueError("Aucune séance existante ne reste à terminer.")
                    mark_existing_plan_continuation()
                    result = {"remaining": remaining, "published": 0}
                    confirmation = (
                        f"Continuité confirmée : termine les {remaining} séance(s) prévue(s). "
                        "Le coach proposera ensuite la nouvelle liste avant tout envoi."
                    )
                elif action == "reschedule_session":
                    target = relevant_plan_path()
                    if target is None:
                        raise ValueError("Aucun plan actif.")
                    session_index = int(answer.get("session_index"))
                    new_date = str(answer.get("new_date") or "")
                    current_plan = json.loads(target.read_text(encoding="utf-8"))
                    current_sessions = current_plan.get("seances", [])
                    if (
                        0 <= session_index < len(current_sessions)
                        and str(current_sessions[session_index].get("date") or "") == new_date
                    ):
                        raise ValueError(
                            "Cette proposition est obsolÃ¨te : la sÃ©ance est dÃ©jÃ  Ã  cette date."
                        )
                    session = reschedule_session(
                        connect(load_settings()),
                        target,
                        session_index,
                        new_date,
                    )
                    result = {"session": session}
                    confirmation = "Séance déplacée dans Garmin Connect."
                elif action == "modify_session":
                    target = relevant_plan_path()
                    if target is None:
                        raise ValueError("Aucun plan actif.")
                    session = modify_planned_session(
                        target,
                        int(answer.get("session_index")),
                        dict(answer.get("session_patch") or {}),
                    )
                    removed, published = replace_future_plan(
                        connect(load_settings()), target
                    )
                    result = {
                        "session": session,
                        "removed": removed,
                        "published": published,
                    }
                    confirmation = (
                        f"Séance modifiée immédiatement : « {session['titre']} » ; "
                        f"{published} séance(s) future(s) synchronisée(s) avec Garmin."
                    )
                elif action == "delete_sessions":
                    target = relevant_plan_path()
                    if target is None:
                        raise ValueError("Aucun plan actif.")
                    indexes = sorted(
                        {int(index) for index in answer.get("session_indexes") or []}
                    )
                    payload = json.loads(target.read_text(encoding="utf-8"))
                    sessions = payload.get("seances") or []
                    if not indexes or any(index < 0 or index >= len(sessions) for index in indexes):
                        raise ValueError("La liste des séances à retirer est invalide.")
                    removed_from_garmin = remove_future_sessions(
                        connect(load_settings()), target, session_indexes=set(indexes)
                    )
                    removed_titles = [str(sessions[index].get("titre") or "Séance") for index in indexes]
                    payload["seances"] = [
                        session for index, session in enumerate(sessions) if index not in set(indexes)
                    ]
                    target.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    result = {
                        "removed": len(indexes),
                        "removedFromGarmin": removed_from_garmin,
                        "removedTitles": removed_titles,
                    }
                    confirmation = (
                        f"{len(indexes)} séance(s) retirée(s) du plan"
                        f" ; {removed_from_garmin} retirée(s) de Garmin."
                    )
                elif action == "adapt_plan":
                    target = relevant_plan_path()
                    if target is None:
                        raise ValueError("Aucun plan actif.")
                    result = adapt_plan(target, str(answer.get("plan_adjustment") or ""))
                    removed, published = replace_future_plan(
                        connect(load_settings()), target
                    )
                    result.update({"removed": removed, "published": published})
                    confirmation = (
                        f"Plan adapté immédiatement : {result['changed_sessions']} "
                        f"séance(s) mise(s) à jour et {published} envoyée(s) sur Garmin."
                    )
                else:
                    raise ValueError(
                        "Cette réponse ne contient aucune action applicable."
                    )
                mark_action_applied(answer["id"])
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, status=400)
                return
            except Exception as exc:
                _json_response(
                    self,
                    {
                        "ok": False,
                        "message": f"Action impossible : {type(exc).__name__}",
                    },
                    status=502,
                )
                return
            _json_response(
                self,
                {"ok": True, "message": confirmation, "followupQueued": False, **result},
            )
            return
        if path == "/api/onboarding/plan-decision":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(content_length) or b"{}")
                onboarding = load_onboarding()
                if onboarding.get("status") != "plan_proposed":
                    raise ValueError("Aucune proposition de plan n'attend de décision.")
                accepted = body.get("accepted") is True
                if accepted:
                    target = Path(str(onboarding.get("proposed_plan") or ""))
                    if not target.is_file() or target.parent.resolve() != PLANS_DIR.resolve():
                        raise ValueError("Le brouillon de plan est introuvable.")
                    settings = load_settings()
                    published = publish_plan(connect(settings), target)
                    mark_complete()
                    result = {
                        "accepted": True,
                        "published": published,
                        "message": f"Plan accepté : {published} séance(s) envoyée(s) sur Garmin.",
                    }
                    result["followupQueued"] = False
                else:
                    feedback = str(body.get("feedback") or "")
                    mark_plan_rejected(feedback)
                    add_assistant_prompt(
                        "Le plan n'a pas été envoyé. Dis-moi ce que tu veux modifier "
                        "(jours, durée, intensité ou type de séance), puis je proposerai une nouvelle liste.",
                        f"garmin-onboarding-revise-{datetime.now().timestamp()}",
                    )
                    result = {
                        "accepted": False,
                        "published": 0,
                        "message": "Plan refusé : aucune séance n'a été envoyée.",
                    }
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, status=400)
                return
            except Exception as exc:
                _json_response(
                    self,
                    {"ok": False, "message": f"Publication impossible : {type(exc).__name__}"},
                    status=502,
                )
                return
            _json_response(self, {"ok": True, **result})
            return
        if path == "/api/plan/skip":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(content_length) or b"{}")
                session_index = int(body.get("sessionIndex"))
                target = relevant_plan_path()
                if target is None:
                    raise ValueError("Aucun plan actif.")
                session = mark_session_skipped(target, session_index)
                settings = load_settings()
                next_plan = maybe_review_and_plan(settings)
                if next_plan:
                    client = connect(settings)
                    publish_plan(client, next_plan)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, status=400)
                return
            except Exception as exc:
                _json_response(
                    self,
                    {"ok": False, "message": f"Confirmation impossible : {type(exc).__name__}"},
                    status=502,
                )
                return
            _json_response(
                self,
                {
                    "ok": True,
                    "session": session,
                    "message": "Séance confirmée comme non faite.",
                    "nouveauPlan": bool(next_plan),
                },
            )
            return
        if path == "/api/plan/reschedule":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(content_length) or b"{}")
                session_index = int(body.get("sessionIndex"))
                new_date = str(body.get("date") or "")
                target = relevant_plan_path()
                if target is None:
                    raise ValueError("Aucun plan actif.")
                client = connect(load_settings())
                session = reschedule_session(client, target, session_index, new_date)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, status=400)
                return
            except Exception as exc:
                _json_response(
                    self,
                    {"ok": False, "message": f"Déplacement impossible : {type(exc).__name__}"},
                    status=502,
                )
                return
            _json_response(
                self,
                {"ok": True, "session": session, "message": "Séance déplacée dans Garmin Connect."},
            )
            return
        if path != "/api/sync":
            self.send_error(404)
            return
        try:
            settings = load_settings()
            client = connect(settings)
            onboarding = inspect_garmin_account(client)
            created = sync_once(client, settings=settings)
        except Exception as exc:  # L'interface doit rester disponible si Garmin répond mal.
            _json_response(
                self,
                {"ok": False, "message": f"Synchronisation impossible : {type(exc).__name__}"},
                status=502,
            )
            return
        activities = load_public_activities()
        latest = activities[-1] if activities else None
        user_id = current_user_id()
        if created and user_id:
            queue_post_sync_coaching(
                user_id, [path.stem.rsplit("-", 1)[-1] for path in created]
            )
            message = (
                f"{len(created)} nouvelle(s) course(s) importée(s). "
                "Le coach met l'analyse et le plan à jour."
            )
        elif latest:
            message = "Garmin est à jour. Aucune nouvelle course."
        else:
            message = "Garmin est à jour. Aucune course trouvée."
        if created and latest and onboarding.get("status") not in {"not_started", "needs_profile"}:
            add_assistant_prompt(
                (
                    f"J'ai importé ta course de {latest.get('distanceKm', '—')} km. "
                    "Comment te sens-tu après cette séance : effort facile, normal "
                    "ou difficile ? As-tu une fatigue inhabituelle ou une douleur ?"
                ),
                f"post-run-{latest['id']}",
            )
        _json_response(
            self,
            {"ok": True, "created": len(created), "message": message},
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tableau de bord Motionest")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not os.getenv(
        "GARMIN_COACH_MOBILE_TOKEN", ""
    ).strip():
        raise SystemExit(
            "GARMIN_COACH_MOBILE_TOKEN est requis pour exposer le serveur au réseau."
        )
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Motionest est disponible sur {url}")
    print("Appuie sur Ctrl+C pour arrêter.")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTableau de bord arrêté.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
