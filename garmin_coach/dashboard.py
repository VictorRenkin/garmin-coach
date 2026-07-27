from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .ai_analysis import load_analysis
from .activity_data import latest_hr_zone_boundaries, public_detail
from .coach_chat import (
    add_assistant_prompt,
    generate_response,
    get_message,
    list_messages,
    mark_action_applied,
    store_response,
    submit_message,
)
from .config import ACTIVITIES_DIR, PLANS_DIR, PROFILE_FILE, REPORTS_DIR, ensure_directories, load_settings
from .garmin import connect
from .garmin_workouts import publish_plan
from .summary import is_running
from .storage import load_activities
from .sync import sync_once
from .training_cycle import (
    completed_session_indexes,
    load_plan,
    maybe_review_and_plan,
    monday_of,
    session_statuses,
)


TEMPLATE_FILE = Path(__file__).with_name("dashboard_v2.html")
DEFAULT_OBJECTIVE = {
    "title": "Terminer un semi-marathon",
    "distanceKm": 21.1,
    "sessionsPerWeek": 3,
    "raceDate": None,
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
    distance = float(payload.get("distanceKm"))
    frequency = int(payload.get("sessionsPerWeek"))
    if not 1 <= distance <= 200:
        raise ValueError("La distance doit être comprise entre 1 et 200 km.")
    if not 1 <= frequency <= 14:
        raise ValueError("Le nombre de séances doit être compris entre 1 et 14.")
    race_date = str(payload.get("raceDate") or "").strip() or None
    if race_date:
        date.fromisoformat(race_date)
    objective = {
        "title": title,
        "distanceKm": round(distance, 2),
        "sessionsPerWeek": frequency,
        "raceDate": race_date,
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
            "title": update.get("title", current.get("title")),
            "distanceKm": update.get("distance_km", current.get("distanceKm")),
            "sessionsPerWeek": update.get(
                "sessions_per_week", current.get("sessionsPerWeek")
            ),
            "raceDate": update.get("race_date", current.get("raceDate")),
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
    return {
        "id": activity_id,
        "garminActivityUrl": f"https://connect.garmin.com/modern/activity/{activity_id}",
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


def request_manual_analysis(activity_id: str) -> bool:
    marker = f"Analyse la séance Garmin {activity_id} après mon actualisation."
    for message in list_messages():
        if (
            message.get("role") == "user"
            and message.get("content") == marker
            and message.get("status") == "pending"
        ):
            return False
    submit_message(marker)
    return True


def coach_context() -> dict[str, Any]:
    """Contexte sportif utile, sans identité ni coordonnées GPS."""
    conversation = [
        {"role": item.get("role"), "content": item.get("content")}
        for item in list_messages()[-20:]
        if item.get("role") in {"user", "assistant"}
    ]
    activities = load_public_activities()[-8:]
    return {
        "objectif": load_objective(),
        "plan_actif": load_relevant_plan(),
        "courses_recentes": [
            {key: value for key, value in activity.items() if key != "analysis"}
            for activity in activities
        ],
        "conversation_recente": conversation,
    }

def load_relevant_plan() -> dict[str, Any] | None:
    current_week = monday_of(date.today())
    plan = load_plan(current_week) or load_plan(current_week + timedelta(days=7))
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


def _json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            body = TEMPLATE_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
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
        if path == "/api/profile":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(content_length) or b"{}")
                objective = save_objective(body)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, status=400)
                return
            try:
                answer = store_response(
                    message["id"],
                    generate_response(message, load_settings(), coach_context()),
                )
            except Exception as exc:
                _json_response(
                    self,
                    {"ok": False, "message": str(exc), "request": message},
                    status=503,
                )
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
                message = submit_message(str(body.get("message") or ""))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                _json_response(self, {"ok": False, "message": str(exc)}, status=400)
                return
            _json_response(
                self,
                {
                    "ok": True,
                    "message": "Réponse du coach reçue.",
                    "request": message,
                    "answer": answer,
                },
                status=201,
            )
            return
        if path == "/api/coach/action":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(content_length) or b"{}")
                answer = get_message(str(body.get("answerId") or ""))
                if not answer or answer.get("role") != "assistant":
                    raise ValueError("Proposition du coach introuvable.")
                if answer.get("action_status") == "applied":
                    raise ValueError("Cette proposition a déjà été appliquée.")
                action = answer.get("action")
                if action == "update_objective":
                    update = answer.get("objective_update") or {}
                    objective = apply_objective_update(update)
                    result = {"objective": objective}
                    confirmation = "Objectif mis à jour."
                elif action == "reschedule_session":
                    target = relevant_plan_path()
                    if target is None:
                        raise ValueError("Aucun plan actif.")
                    session = reschedule_session(
                        connect(load_settings()),
                        target,
                        int(answer.get("session_index")),
                        str(answer.get("new_date") or ""),
                    )
                    result = {"session": session}
                    confirmation = "Séance déplacée dans Garmin Connect."
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
            _json_response(self, {"ok": True, "message": confirmation, **result})
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
            created = sync_once(client, settings=settings)
            next_plan = maybe_review_and_plan(settings)
            if next_plan:
                publish_plan(client, next_plan)
        except Exception as exc:  # L'interface doit rester disponible si Garmin répond mal.
            _json_response(
                self,
                {"ok": False, "message": f"Synchronisation impossible : {type(exc).__name__}"},
                status=502,
            )
            return
        activities = load_public_activities()
        latest = activities[-1] if activities else None
        analysis_ready = bool(latest and latest.get("analysis"))
        requested = False
        if latest and not analysis_ready:
            requested = request_manual_analysis(str(latest["id"]))
        if created and analysis_ready:
            message = f"{len(created)} nouvelle(s) course(s) importée(s) et analysée(s)."
        elif latest and not analysis_ready:
            message = (
                "Garmin est à jour. Analyse du coach demandée manuellement."
                if requested
                else "Garmin est à jour. L'analyse du coach est déjà demandée."
            )
        elif latest:
            message = "Garmin est à jour. La dernière course est déjà analysée."
        else:
            message = "Garmin est à jour. Aucune course trouvée."
        if created and latest:
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
