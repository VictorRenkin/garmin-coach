import json
from pathlib import Path

from garmin_coach import dashboard


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

def test_manual_analysis_request_is_not_duplicated(monkeypatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(
        dashboard,
        "list_messages",
        lambda: [
            {
                "role": "user",
                "content": "Analyse la séance Garmin 42 après mon actualisation.",
                "status": "pending",
            }
        ],
    )
    monkeypatch.setattr(dashboard, "submit_message", sent.append)

    assert dashboard.request_manual_analysis("42") is False
    assert sent == []


def test_dashboard_uses_manual_refresh_and_separate_time_charts() -> None:
    html = dashboard.TEMPLATE_FILE.read_text(encoding="utf-8")

    assert "Actualiser et analyser" in html
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
    monkeypatch.setattr(dashboard, "request_manual_analysis", forbidden)
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
    assert "renderCharts();renderZones();renderLaps();renderAnalysis(a)" in html
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
    assert "valeur(s) aberrante(s) masquée(s)" in html


def test_dashboard_chat_supports_confirmable_actions_and_waiting_state() -> None:
    html = dashboard.TEMPLATE_FILE.read_text(encoding="utf-8")

    assert "/api/coach/action" in html
    assert "Confirmer le nouvel objectif" in html
    assert "Confirmer le déplacement" in html
    assert "Le coach réfléchit…" in html
    assert 'id="chatSend"' in html
