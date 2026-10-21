"""Independent checks of current multisport sync and preserved import boundaries."""
import json
from datetime import date, timedelta

import pytest

from garmin_coach import config, dashboard, history_import, history_storage
from garmin_coach import local_activity_history, storage, sync


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.setattr(sync, "fetch_and_save_activity_bundle", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "build_first_run_journey", lambda *args: {})


def activity(ident, sport="cycling", days=0):
    return {"activityId": ident, "activityName": "PRIVATE SYNC TITLE", "ownerEmail": "sync@example.com", "latitude": 50.123,
            "activityType": {"typeKey": sport}, "startTimeLocal": (date.today() - timedelta(days=days)).isoformat() + "T10:00:00",
            "distance": 5000, "movingDuration": 1800}


class Pages:
    def __init__(self, activities):
        self.activities = activities
        self.calls = []

    def get_activities(self, start, limit):
        self.calls.append((start, limit))
        return self.activities[start:start + limit]


def imported(user="alice", status="complete", scope="all"):
    with config.user_context(user):
        history_import.initialize_history_import(scope, reference_date=date.today() - timedelta(days=30))
        history_import.update_history_import({"status": status, "next_offset": 40})
        return history_import.load_history_import(detect_stale=False)


def test_more_than_twenty_current_multisport_activities_reach_local_reader():
    imported()
    values = [activity(str(i), ["running", "cycling", "swimming", "soccer"][i % 4]) for i in range(45)]
    client = Pages(values)
    with config.user_context("alice"):
        reports = sync.sync_once(client)
        loaded = local_activity_history.load_local_activity_history()
    assert len(loaded) == 45
    assert {local_activity_history.activity_sport_type(a) for a in loaded} == {"running", "cycling", "swimming", "soccer"}
    assert [start for start, limit in client.calls] == [0, 20, 40]
    assert len(reports) == 12


@pytest.mark.parametrize("status", ["complete", "interrupted", "idle"])
def test_sync_advances_effective_reference_without_restarting_import(status):
    before = imported(status=status, scope="3_months")
    with config.user_context("alice"):
        sync.sync_once(Pages([activity("fresh")]))
        after = history_import.load_history_import(detect_stale=False)
        scope = local_activity_history.local_history_scope()
        loaded = local_activity_history.load_local_activity_history()
    for field in ["status", "scope", "cutoff_date", "reference_date", "next_offset"]:
        assert after[field] == before[field]
    assert scope["reference_date"] == date.today().isoformat()
    assert len(loaded) == 1


def test_sync_is_idempotent_for_metadata_and_legacy_reports():
    imported()
    client = Pages([activity("run", "running"), activity("bike")])
    with config.user_context("alice"):
        first = sync.sync_once(client)
        second = sync.sync_once(client)
        loaded = local_activity_history.load_local_activity_history()
        legacy = storage.load_activities()
    assert len(first) == 1
    assert second == []
    assert len(loaded) == 2
    assert [a["activityId"] for a in legacy] == ["run"]


def test_no_import_migrates_old_legacy_runs_before_multisport_source_switch():
    with config.user_context("alice"):
        storage.save_activity(activity("old-run", "running", days=300))
        storage.save_seen_ids({"old-run"})
        sync.sync_once(Pages([activity("new-bike")]))
        loaded = local_activity_history.load_local_activity_history()
    assert {a["activityId"] for a in loaded} == {"old-run", "new-bike"}


def test_bootstrap_preserves_new_multisport_metadata_without_run_alerts():
    with config.user_context("alice"):
        result = sync.sync_once(Pages([activity("bootstrap-run", "running"), activity("bootstrap-bike")]), bootstrap=True)
        loaded = local_activity_history.load_local_activity_history()
        seen = storage.load_seen_ids()
    assert result == []
    assert {a["activityId"] for a in loaded} == {"bootstrap-run", "bootstrap-bike"}
    assert "bootstrap-run" in seen


def test_current_sync_isolated_between_accounts_and_safe_for_coach_payload():
    for user, ident, sport in [("alice", "alice-bike", "cycling"), ("bob", "bob-swim", "swimming")]:
        imported(user)
        with config.user_context(user):
            sync.sync_once(Pages([activity(ident, sport)]))
    with config.user_context("alice"):
        context = dashboard.coach_context()
        loaded = local_activity_history.load_local_activity_history()
    assert [a["activityId"] for a in loaded] == ["alice-bike"]
    serialized = json.dumps(context["contexte_multisport"])
    for secret in ["PRIVATE SYNC TITLE", "sync@example.com", "latitude", "alice-bike", "bob-swim"]:
        assert secret not in serialized
    assert "cycling" in serialized
    assert "swimming" not in serialized


def test_repeated_full_page_fails_explicitly_without_advancing_watermark():
    before = imported()
    class Repeated:
        def get_activities(self, start, limit):
            return [activity(str(i)) for i in range(limit)]
    with config.user_context("alice"):
        with pytest.raises(Exception):
            sync.sync_once(Repeated())
        after = history_import.load_history_import(detect_stale=False)
    assert after.get("latest_sync_date") == before.get("latest_sync_date")


def test_page_error_does_not_advance_watermark():
    before = imported()
    class Broken:
        def get_activities(self, start, limit):
            if start:
                raise RuntimeError("network unavailable")
            return [activity(str(i)) for i in range(limit)]
    with config.user_context("alice"):
        with pytest.raises(Exception):
            sync.sync_once(Broken())
        after = history_import.load_history_import(detect_stale=False)
    assert after.get("latest_sync_date") == before.get("latest_sync_date")


@pytest.mark.parametrize("timestamp", ["not-a-date", "2099-01-01T10:00:00"])
def test_invalid_or_future_activity_never_advances_reference(timestamp):
    before = imported()
    bad = activity("bad")
    bad["startTimeLocal"] = timestamp
    with config.user_context("alice"):
        with pytest.raises(Exception):
            sync.sync_once(Pages([bad]))
        after = history_import.load_history_import(detect_stale=False)
    assert after.get("latest_sync_date") == before.get("latest_sync_date")


def test_explicit_page_budget_is_not_silent_success():
    before = imported()
    class Endless:
        def __init__(self):
            self.calls = 0
        def get_activities(self, start, limit):
            self.calls += 1
            return [activity(str(start + index)) for index in range(limit)]
    client = Endless()
    with config.user_context("alice"):
        with pytest.raises(Exception):
            sync.sync_once(client)
        after = history_import.load_history_import(detect_stale=False)
    assert client.calls == 10
    assert after.get("latest_sync_date") == before.get("latest_sync_date")


def test_retry_after_partial_persistence_does_not_stop_on_unsuccessful_head():
    imported()
    values = [activity(str(index)) for index in range(45)]
    class BreakAfterHead(Pages):
        def get_activities(self, start, limit):
            if start:
                raise RuntimeError("temporary failure")
            return super().get_activities(start, limit)
    with config.user_context("alice"):
        with pytest.raises(RuntimeError):
            sync.sync_once(BreakAfterHead(values))
        retry = Pages(values)
        sync.sync_once(retry)
        loaded = local_activity_history.load_local_activity_history()
    assert len(loaded) == 45
    assert [start for start, limit in retry.calls] == [0, 20, 40]


def test_empty_success_advances_date_without_fabricating_activities():
    imported()
    with config.user_context("alice"):
        assert sync.sync_once(Pages([])) == []
        state = history_import.load_history_import(detect_stale=False)
        assert state["latest_sync_date"] == date.today().isoformat()
        assert local_activity_history.load_local_activity_history() == []
