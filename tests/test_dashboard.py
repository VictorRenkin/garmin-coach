import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from garmin_coach import dashboard
from garmin_coach import coach_chat
from garmin_coach import user_accounts
from garmin_coach.config import Settings


def test_public_activity_only_exposes_training_metrics() -> None:
    result = dashboard._public_activity(
        {
            "activityId": 42,
            "ownerFullName": "Private Runner",
            "activityName": "Test Run",
            "startTimeLocal": "2026-07-22 08:00:00",
            "distance": 5000,
            "movingDuration": 1500,
            "averageHR": 150,
        }
    )

    assert result["distanceKm"] == 5
    assert result["paceSeconds"] == 300
    assert result["averageHr"] == 150
    assert result["garminActivityUrl"] == "https://connect.garmin.com/modern/activity/42"
    assert "ownerFullName" not in result


def test_load_public_activities_sorts_by_date(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard, "ACTIVITIES_DIR", tmp_path)
    for activity_id, date in ((2, "2026-07-22 09:00:00"), (1, "2026-07-20 09:00:00")):
        (tmp_path / f"{activity_id}.json").write_text(
            json.dumps(
                {
                    "activityId": activity_id,
                    "activityType": {"typeKey": "running"},
                    "startTimeLocal": date,
                    "distance": 3000,
                    "movingDuration": 1000,
                }
            ),
            encoding="utf-8",
        )

    assert [item["id"] for item in dashboard.load_public_activities()] == ["1", "2"]





def test_save_objective_persists_validated_profile(tmp_path: Path, monkeypatch) -> None:
    profile = tmp_path / "profile.json"
    monkeypatch.setattr(dashboard, "PROFILE_FILE", profile)
    monkeypatch.setattr(dashboard, "ensure_directories", lambda: None)

    objective = dashboard.save_objective(
        {
            "title": "Courir mon premier 10 km",
            "distanceKm": 10,
            "sessionsPerWeek": 4,
            "raceDate": "2026-10-04",
        }
    )

    assert objective["distanceKm"] == 10
    assert dashboard.load_objective() == objective


def test_save_objective_accepts_coaching_goal_without_race_distance(
    tmp_path: Path, monkeypatch
) -> None:
    profile = tmp_path / "profile.json"
    monkeypatch.setattr(dashboard, "PROFILE_FILE", profile)
    monkeypatch.setattr(dashboard, "ensure_directories", lambda: None)

    objective = dashboard.save_objective(
        {
            "goalType": "wellbeing",
            "title": "Retrouver une routine durable",
            "distanceKm": None,
            "sessionsPerWeek": 3,
            "raceDate": None,
            "availableDays": ["tue", "thu", "sun"],
            "constraints": "Travail tard le mercredi.",
            "injuryPain": "Aucune douleur actuelle.",
            "preferences": "Natation le samedi si la fatigue est basse.",
        }
    )

    assert objective["distanceKm"] is None
    assert objective["availableDays"] == ["tue", "thu", "sun"]
    assert objective["constraints"] == "Travail tard le mercredi."
    assert objective["preferences"].startswith("Natation")


def test_coach_objective_update_preserves_unspecified_fields(
    tmp_path: Path, monkeypatch
) -> None:
    profile = tmp_path / "profile.json"
    monkeypatch.setattr(dashboard, "PROFILE_FILE", profile)
    monkeypatch.setattr(dashboard, "ensure_directories", lambda: None)
    dashboard.save_objective(
        {
            "title": "Terminer un semi-marathon",
            "distanceKm": 21.1,
            "sessionsPerWeek": 3,
            "raceDate": "2026-10-04",
        }
    )

    updated = dashboard.apply_objective_update({"sessions_per_week": 4})

    assert updated["sessionsPerWeek"] == 4
    assert updated["distanceKm"] == 21.1
    assert updated["raceDate"] == "2026-10-04"

def test_reschedule_session_updates_garmin_and_local_plan(tmp_path: Path) -> None:
    target = tmp_path / "plan.json"
    target.write_text(
        json.dumps(
            {
                "seances": [
                    {
                        "date": "2026-07-30",
                        "titre": "Intervalles",
                        "garmin": {"workout_id": 123, "scheduled": True},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class FakeGarth:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete(self, service: str, url: str, api: bool) -> None:
            self.deleted.append(url)

    class FakeClient:
        def __init__(self) -> None:
            self.garth = FakeGarth()
            self.scheduled: list[tuple[int, str]] = []

        def connectapi(self, url: str) -> dict:
            return {
                "calendarItems": [
                    {
                        "id": 456,
                        "itemType": "workout",
                        "workoutId": 123,
                        "date": "2026-07-30",
                    }
                ]
            }

        def schedule_workout(self, workout_id: int, new_date: str) -> None:
            self.scheduled.append((workout_id, new_date))

    client = FakeClient()
    result = dashboard.reschedule_session(client, target, 0, "2026-07-25")

    assert result["date"] == "2026-07-25"
    assert client.scheduled == [(123, "2026-07-25")]
    assert client.garth.deleted == ["/workout-service/schedule/456"]
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["seances"][0]["date"] == "2026-07-25"

def test_dashboard_history_starts_with_five_courses() -> None:
    html = dashboard.TEMPLATE_FILE.read_text(encoding="utf-8")

    assert "items.slice(0,5)" in html
    assert 'id="historyToggle"' in html
    assert "historyExpanded" in html

def test_refresh_selects_latest_activity() -> None:
    html = dashboard.TEMPLATE_FILE.read_text(encoding="utf-8")

    assert "loadAll({selectLatest=false}={})" in html
    assert "loadAll({selectLatest:true})" in html


def test_dashboard_uses_manual_refresh_and_separate_time_charts() -> None:
    html = dashboard.TEMPLATE_FILE.read_text(encoding="utf-8")

    assert "Mettre à jour mes activités" in html
    assert "setInterval(" not in html
    assert 'id="syncBtn"' not in html
    assert 'id="paceChart"' in html
    assert 'id="hrChart"' in html
    assert 'id="elevationChart"' in html
    assert "Temps écoulé (min:s)" in html



def test_mark_session_skipped_requires_explicit_action(tmp_path: Path) -> None:
    target = tmp_path / "plan.json"
    target.write_text('{"seances":[{"date":"2026-07-20"}]}', encoding="utf-8")

    result = dashboard.mark_session_skipped(target, 0)

    assert result["statut"] == "skippee"
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["seances"][0]["statut"] == "skippee"


def test_dashboard_startup_does_not_sync_or_request_ai(monkeypatch) -> None:
    class FakeServer:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            pass

    def forbidden(*args, **kwargs):
        raise AssertionError("Le démarrage ne doit ni synchroniser ni demander une analyse")

    monkeypatch.setattr(dashboard, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(dashboard, "sync_once", forbidden)
    monkeypatch.setattr(dashboard.threading, "Timer", forbidden)
    monkeypatch.setattr("sys.argv", ["dashboard", "--no-browser"])

    assert dashboard.main() == 0

def test_dashboard_chart_visibility_controls_and_tooltips() -> None:
    html = dashboard.TEMPLATE_FILE.read_text(encoding="utf-8")

    assert 'id="paceChartCard"' in html
    assert 'id="hrChartCard" hidden' in html
    assert 'id="elevationChartCard" hidden' in html
    assert 'data-chart="hrChartCard"' in html
    assert 'data-chart="elevationChartCard"' in html
    assert "tab.addEventListener('click'" in html
    assert "pointermove" in html
    assert "fmtDuration(point.time)" in html
    assert "chart.label(point.value)" in html


def test_dashboard_pace_chart_keeps_heart_rate_zone_information() -> None:
    html = dashboard.TEMPLATE_FILE.read_text(encoding="utf-8")

    assert "directHeartRate" in html
    assert "heartRateZones" in html
    assert "const colors=['#58e09b','#35d0e8','#ffad62','#ff6876','#a990ff']" in html


def test_dashboard_history_is_clickable_and_loads_full_activity_detail() -> None:
    html = dashboard.TEMPLATE_FILE.read_text(encoding="utf-8")

    assert 'class="row-btn"' in html
    assert 'class="history-row' in html
    assert 'data-activity-id="' in html
    assert "row.addEventListener('click'" in html
    assert "row.addEventListener('keydown'" in html
    assert "/details`" in html
    assert "renderCharts();renderZones();renderLaps();renderMessages()" in html
    assert "a.analysis?'Disponible':'En attente'" in html


def test_dashboard_contains_no_known_corrupted_french_text() -> None:
    html = dashboard.TEMPLATE_FILE.read_text(encoding="utf-8")
    corrupted = ["S?ance", "pass?e", "activit?", "d?cid?", "termin?e", "r?el", "?chauffement", "?t?"]

    assert not any(fragment in html for fragment in corrupted)

def test_dashboard_coach_card_is_compact() -> None:
    html = dashboard.TEMPLATE_FILE.read_text(encoding="utf-8")

    assert "analysis-points" in html
    assert "ai.points_reussis" in html
    assert "ai.axes_progression" in html
    assert "ai.contexte_terrain" not in html
    assert "ai.conseil_prochaine_seance" not in html

def test_dashboard_filters_implausible_chart_values() -> None:
    html = dashboard.TEMPLATE_FILE.read_text(encoding="utf-8")

    assert "filterChartPoints" in html
    assert "min:120,max:1200" in html
    assert "maxRatio:2.5" in html
    assert 'id="paceChartQuality"' in html
    assert "valeur(s) aberrante(s) masquée(s)" not in html


def test_dashboard_chat_supports_confirmable_actions_and_waiting_state() -> None:
    html = dashboard.TEMPLATE_FILE.read_text(encoding="utf-8")

    assert "/api/coach/action" in html
    assert "Accepter l’objectif" in html
    assert "markCoachAlertSeen" in html
    assert "IntersectionObserver" in html
    assert "Accepter le déplacement" in html
    assert "Accepter la continuité" in html
    assert "Préparer la proposition" in html
    assert "Refuser" in html
    assert "/api/coach/action-reject" in html
    assert "Créer et envoyer le plan Garmin" not in html
    assert 'aria-label="Motionest écrit une réponse"' in html
    assert 'id="chatSend"' in html
    assert "event.key==='Enter'&&!event.shiftKey" in html
    assert "requestSubmit()" in html
    assert "AbortController" not in html
    assert "coach_acceptance_followup" in dashboard.coach_acceptance_followup.__name__
    assert "dataset.distance" in html
    assert 'id="coachLevel"' in html
    assert 'id="coachConfigDialog"' not in html
    assert "/api/coach/status" in html
    assert "/api/coach/config" not in html
    assert "Pose ta question à Motionest" in html
    assert "width:min(100%,900px);margin-inline:auto" in html


def test_chat_http_endpoint_queues_ai_response(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(coach_chat, "MESSAGES_FILE", tmp_path / "messages.json")
    monkeypatch.setattr(coach_chat, "COACH_DRAFTS_DIR", tmp_path / "drafts")
    monkeypatch.setattr(coach_chat, "ensure_directories", lambda: None)
    monkeypatch.setattr(
        dashboard, "load_settings", lambda: Settings(None, None, 300, None)
    )
    monkeypatch.setattr(
        dashboard,
        "coach_context",
        lambda: {"objectif": {"title": "Terminer un semi-marathon"}},
    )
    queued = []
    monkeypatch.setattr(
        dashboard,
        "queue_coach_response",
        lambda user_id, request_id: queued.append((user_id, request_id)),
    )
    server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cookie = f"motionest_session={user_accounts.create_session('u_test_chat')}"
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/coach/messages",
            data=json.dumps({"message": "Bonjour"}).encode(),
            headers={"Content-Type": "application/json", "Cookie": cookie},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            assert response.status == 202
            payload = json.loads(response.read())
        assert payload["request"]["status"] == "pending"
        assert queued == [("u_test_chat", payload["request"]["id"])]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_coach_status_and_config_http_endpoints_do_not_return_key(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard,
        "coach_ai_status",
        lambda: {"configured": False, "model": None},
    )
    monkeypatch.setattr(
        dashboard,
        "save_coach_ai_config",
        lambda key: {"configured": bool(key), "model": "test-model"},
    )
    server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cookie = f"motionest_session={user_accounts.create_session('u_test_status')}"
        with urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/coach/status",
                headers={"Cookie": cookie},
            ),
            timeout=3,
        ) as response:
            status_payload = json.loads(response.read())
        assert status_payload == {"configured": False, "model": None}

        secret = "sk-test-secret-that-must-not-leak"
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/coach/config",
            data=json.dumps({"apiKey": secret}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(request, timeout=3)
        assert denied.value.code == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    ("adjustment", "duration", "distance", "session_type"),
    [
        ("lighter", 32, 4.8, "intervalles"),
        ("progressive", 44, 6.6, "intervalles"),
        ("recovery", 28, 4.2, "récupération"),
    ],
)
def test_adapt_plan_changes_future_sessions(
    tmp_path, adjustment, duration, distance, session_type
) -> None:
    target = tmp_path / "plan.json"
    target.write_text(
        json.dumps(
            {
                "seances": [
                    {
                        "date": "2099-08-01",
                        "type": "intervalles",
                        "titre": "Fractions",
                        "duree_estimee_minutes": 40,
                        "effort_minutes": 20,
                        "distance_estimee_km": 6.0,
                        "garmin": {"workout_id": 42},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = dashboard.adapt_plan(target, adjustment)
    session = result["plan"]["seances"][0]

    assert result["changed_sessions"] == 1
    assert session["duree_estimee_minutes"] == duration
    assert session["distance_estimee_km"] == distance
    assert session["type"] == session_type
    # L'identifiant reste disponible jusqu'au remplacement effectif dans Garmin.
    assert session["garmin"]["workout_id"] == 42
    assert "confirmation dans le chat" in result["plan"]["adaptation"]


def test_adapt_plan_rejects_unknown_adjustment(tmp_path) -> None:
    target = tmp_path / "plan.json"
    target.write_text('{"seances": []}', encoding="utf-8")

    with pytest.raises(ValueError, match="invalide"):
        dashboard.adapt_plan(target, "dangerous")


def test_profile_http_endpoint_does_not_invoke_chat(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dashboard, "PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(dashboard, "ensure_directories", lambda: None)
    monkeypatch.setattr(
        dashboard,
        "generate_response",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("chat appelé")),
    )
    server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cookie = f"motionest_session={user_accounts.create_session('u_test_profile')}"
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/profile",
            data=json.dumps(
                {
                    "title": "Préparer un 10 km",
                    "distanceKm": 10,
                    "sessionsPerWeek": 3,
                    "raceDate": None,
                }
            ).encode(),
            headers={"Content-Type": "application/json", "Cookie": cookie},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read())
        assert response.status == 200
        assert payload["objective"]["distanceKm"] == 10
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
