from datetime import date, timedelta

from garmin_coach import athlete_memory


def test_memory_deduplicates_explicit_facts(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.json"
    monkeypatch.setattr(athlete_memory, "COACH_MEMORY_FILE", target)
    monkeypatch.setattr(athlete_memory, "ensure_directories", lambda: None)

    athlete_memory.remember_facts([
        "Préfère courir le mardi.",
        "Préfère courir le mardi.",
    ])

    assert [item["fact"] for item in athlete_memory.load_memory()] == [
        "Préfère courir le mardi."
    ]


def test_activity_trace_combines_recent_detail_with_global_averages() -> None:
    today = date.today()
    activities = [
        {
            "date": (today - timedelta(days=10)).isoformat(),
            "distanceKm": 5.0,
            "paceSeconds": 360,
        },
        {
            "date": (today - timedelta(days=120)).isoformat(),
            "distanceKm": 10.0,
            "paceSeconds": 420,
        },
    ]

    trace = athlete_memory.activity_trace(activities)

    assert trace["ensemble_historique"]["courses"] == 2
    assert trace["ensemble_historique"]["distance_moyenne_km"] == 7.5
    assert trace["derniers_28_jours"]["courses"] == 1
    assert trace["derniers_90_jours"]["courses"] == 1
