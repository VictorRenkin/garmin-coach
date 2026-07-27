from pathlib import Path

from garmin_coach import sync


class FakeGarmin:
    def get_activities(self, start: int, limit: int) -> list[dict]:
        assert (start, limit) == (0, 20)
        return [
            {
                "activityId": 456,
                "activityName": "Footing test",
                "activityType": {"typeKey": "running"},
                "startTimeLocal": "2026-07-22 08:00:00",
                "distance": 5000,
                "movingDuration": 1800,
            }
        ]


def test_a_run_is_reported_only_once(tmp_path: Path, monkeypatch) -> None:
    state_file = tmp_path / "state.json"
    reports_dir = tmp_path / "reports"
    activities_dir = tmp_path / "activities"
    reports_dir.mkdir()
    activities_dir.mkdir()

    monkeypatch.setattr("garmin_coach.storage.STATE_FILE", state_file)
    monkeypatch.setattr("garmin_coach.storage.ACTIVITIES_DIR", activities_dir)
    monkeypatch.setattr(sync, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(sync, "ensure_directories", lambda: None)
    monkeypatch.setattr("garmin_coach.storage.ensure_directories", lambda: None)

    first = sync.sync_once(FakeGarmin())
    second = sync.sync_once(FakeGarmin())

    assert len(first) == 1
    assert first[0].exists()
    assert "5.00 km" in first[0].read_text(encoding="utf-8")
    assert second == []



def test_internal_sync_never_calls_ai(tmp_path: Path, monkeypatch) -> None:
    reports_dir = tmp_path / "reports"
    activities_dir = tmp_path / "activities"
    reports_dir.mkdir()
    activities_dir.mkdir()
    monkeypatch.setattr("garmin_coach.storage.STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr("garmin_coach.storage.ACTIVITIES_DIR", activities_dir)
    monkeypatch.setattr(sync, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(sync, "ensure_directories", lambda: None)
    monkeypatch.setattr("garmin_coach.storage.ensure_directories", lambda: None)

    assert not hasattr(sync, "analyze_and_save")
    assert len(sync.sync_once(FakeGarmin())) == 1


def test_watch_is_inert(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("watch ne doit rien déclencher")

    monkeypatch.setattr(sync, "sync_once", fail)
    sync.watch(object(), object())