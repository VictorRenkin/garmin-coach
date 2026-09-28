from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import threading
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from dotenv import set_key
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
    GarthException,
)

from .ai_analysis import analyze_and_save, load_analysis
from .activity_data import latest_hr_zone_boundaries, public_detail
from .athlete_memory import activity_trace, load_memory
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
    store_response,
    submit_message,
)
from .config import ACTIVITIES_DIR, PLANS_DIR, PROFILE_FILE, REPORTS_DIR, ROOT_DIR, current_user_id, ensure_directories, load_settings, user_context
from .garmin import (
    GarminMFARequiredError,
    authenticate_with_credentials,
    connect,
    persist_client_tokens,
)
from .garmin_workouts import publish_plan, remove_future_sessions, replace_future_plan
from .manager import MANAGER_EMAIL, manager_authorized, manager_cookie, manager_login, manager_stats
from .user_accounts import (
    create_session,
    ensure_user_record,
    record_login,
    session_cookie,
    user_from_cookie,
    user_id_for_email,
    user_login_lock,
)
from .onboarding import (
    inspect_garmin_account,
    load_onboarding,
    mark_complete,
    mark_existing_plan_continuation,
    mark_plan_proposed,
    mark_plan_rejected,
    mark_profile_ready,
)
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
    "availableDays": [],
    "constraints": "",
    "injuryPain": "",
    "preferences": "",
}


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
    if not PROFILE_FILE.is_file():
        return dict(DEFAULT_OBJECTIVE)
    try:
        payload = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_OBJECTIVE)
    objective = payload.get("objective") if isinstance(payload, dict) else None
    return objective if isinstance(objective, dict) else dict(DEFAULT_OBJECTIVE)


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

    objective = {
        "goalType": goal_type,
        "title": title,
        "distanceKm": round(distance, 2) if distance is not None else None,
        "sessionsPerWeek": frequency,
        "raceDate": race_date,
        "availableDays": available_days,
        "constraints": optional_note("constraints", "contraintes"),
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
            retry_delays = (0.0, 2.5, 6.0)
            for attempt, delay in enumerate(retry_delays, start=1):
                if delay:
                    threading.Event().wait(delay)
                try:
                    response = generate_response(request, load_settings(), coach_context())
                    store_response(request_id, response)
                    return
                except Exception:
                    if attempt < len(retry_delays):
                        LOGGER.warning(
                            "RÃ©ponse du coach interrompue, nouvelle tentative %s/%s",
                            attempt + 1,
                            len(retry_delays),
                        )
                    else:
                        LOGGER.exception("RÃ©ponse du coach impossible aprÃ¨s plusieurs tentatives")
            mark_request_failed(request_id)

    threading.Thread(
        target=runner,
        daemon=True,
        name=f"motionest-coach-{user_id[:12]}-{request_id[:8]}",
    ).start()


def coach_context() -> dict[str, Any]:
    """Contexte sportif utile, sans identité ni coordonnées GPS."""
    conversation = [
        {
            "role": item.get("role"),
            "content": item.get("content"),
            "action": item.get("action"),
            "action_status": item.get("action_status"),
        }
        for item in list_messages()[-12:]
        if item.get("role") in {"user", "assistant"}
    ]
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
    return {
        "objectif": load_objective(),
        "plan_actif": plan,
        "courses_recentes": [
            {key: value for key, value in activity.items() if key != "analysis"}
            for activity in activities
        ],
        "trace_sportive": activity_trace(all_activities),
        "memoire_coureur": load_memory(),
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
        public = path in {"/", "/api/mobile/health"} or path.startswith("/assets/")
        manager = path == "/manager" or path.startswith("/api/manager/")
        if public or manager:
            self._do_GET()
            return
        user_id = self._require_user(path)
        if not user_id:
            return
        with user_context(user_id):
            self._do_GET()

    def _do_GET(self) -> None:
        path = urlparse(self.path).path
        if not self._authorize_api(path):
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
        if path == "/dashboard":
            body = TEMPLATE_FILE.read_bytes()
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
            _json_response(self, {"ok": True, "service": "Garmin Coach", "version": 1})
            return
        if path == "/api/coach/status":
            _json_response(self, coach_ai_status())
            return
        if path == "/api/onboarding":
            _json_response(self, {"onboarding": load_onboarding()})
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
        if path in {"/api/login", "/api/logout", "/api/manager/logout"}:
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
                        "et à tes contraintes. On peut d’abord discuter des séances proposées ici : "
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
                    onboarding = load_onboarding()
                    if onboarding.get("status") != "ready":
                        raise ValueError(
                            "Confirme d'abord l'objectif et le nombre de séances."
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
    parser = argparse.ArgumentParser(description="Tableau de bord Garmin Coach")
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
    print(f"Garmin Coach est disponible sur {url}")
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
