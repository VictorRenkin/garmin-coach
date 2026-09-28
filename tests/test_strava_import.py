import csv

from garmin_coach import strava_import


def test_import_strava_csv_normalizes_runs(tmp_path, monkeypatch):
    source = tmp_path / "activities.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "Activity ID", "Activity Date", "Activity Name", "Activity Type",
            "Elapsed Time", "Distance", "Average Heart Rate", "Max Heart Rate", "Elevation Gain",
        ])
        writer.writeheader()
        writer.writerow({
            "Activity ID": "123", "Activity Date": "Aug 20, 2026, 07:30:00 AM",
            "Activity Name": "Sortie facile", "Activity Type": "Run",
            "Elapsed Time": "1800", "Distance": "5.0",
            "Average Heart Rate": "142", "Max Heart Rate": "166", "Elevation Gain": "35",
        })
        writer.writerow({"Activity ID": "999", "Activity Type": "Ride"})

    saved = []
    monkeypatch.setattr(strava_import, "ensure_directories", lambda: None)
    monkeypatch.setattr(strava_import, "load_seen_ids", lambda: set())
    monkeypatch.setattr(strava_import, "save_seen_ids", lambda ids: None)
    monkeypatch.setattr(strava_import, "save_activity", lambda activity: saved.append(activity))
    monkeypatch.setattr(strava_import, "REPORTS_DIR", tmp_path)

    reports = strava_import.import_strava(source)

    assert len(reports) == 1
    assert saved[0]["activityId"] == "strava-123"
    assert saved[0]["distance"] == 5000
    assert saved[0]["source"] == "strava-export"
