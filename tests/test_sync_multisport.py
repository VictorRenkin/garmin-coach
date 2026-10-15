from datetime import date, timedelta

import pytest

from garmin_coach import config, sync
from garmin_coach.history_import import initialize_history_import, load_history_import, update_history_import
from garmin_coach.history_storage import save_activity_metadata
from garmin_coach.local_activity_history import load_local_activity_history, local_history_scope
from garmin_coach.storage import save_activity


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'GLOBAL_DATA_DIR', tmp_path / 'data')
    monkeypatch.setattr(config, 'USERS_DATA_DIR', tmp_path / 'data' / 'users')
    monkeypatch.setattr(sync, 'fetch_and_save_activity_bundle', lambda *args: None)


def activity(identifier, sport='cycling', day=None):
    return {'activityId': identifier, 'activityType': {'typeKey': sport},
            'startTimeLocal': str(day or date.today()) + ' 08:00:00',
            'distance': 5000, 'duration': 1800, 'ownerEmail': 'private@example.com',
            'startLatitude': 123}


class Client:
    def __init__(self, activities):
        self.activities = activities
        self.calls = []

    def get_activities(self, start, limit):
        self.calls.append((start, limit))
        return self.activities[start:start+limit]


def test_recent_multisport_advances_effective_reference_only():
    yesterday = date.today() - timedelta(days=1)
    with config.user_context('athlete_a'):
        before = initialize_history_import(reference_date=yesterday)
        update_history_import({'status': 'complete', 'pagination_complete': True, 'next_offset': 50})
        assert sync.sync_once(Client([activity('new')])) == []
        after = load_history_import(detect_stale=False)
        assert after['reference_date'] == before['reference_date']
        assert after['cutoff_date'] == before['cutoff_date']
        assert after['next_offset'] == 50 and after['pagination_complete']
        assert after['status'] == 'complete'
        assert local_history_scope()['reference_date'] == date.today().isoformat()
        rows = load_local_activity_history()
        assert [row['activityId'] for row in rows] == ['new']
        assert 'ownerEmail' not in rows[0] and 'startLatitude' not in rows[0]
    with config.user_context('athlete_b'):
        assert load_local_activity_history() == []


def test_over_twenty_recent_activities_and_idempotence():
    yesterday = date.today() - timedelta(days=1)
    with config.user_context('athlete_a'):
        initialize_history_import(reference_date=yesterday)
        save_activity_metadata(activity('old', day=yesterday))
        rows = [activity(str(index), sport='swimming' if index % 2 else 'cycling') for index in range(45)] + [activity('old', day=yesterday)]
        client = Client(rows)
        assert sync.sync_once(client) == []
        assert client.calls == [(0, 20), (20, 20), (40, 20)]
        assert len(load_local_activity_history()) == 46
        assert sync.sync_once(Client(rows)) == []
        assert len(load_local_activity_history()) == 46


def test_legacy_courses_remain_when_first_multisport_sync():
    with config.user_context('athlete_a'):
        config.ensure_directories()
        save_activity(activity('legacy', 'running', date.today() - timedelta(days=100)))
        assert sync.sync_once(Client([activity('bike')])) == []
        assert {row['activityId'] for row in load_local_activity_history()} == {'legacy', 'bike'}
        assert load_history_import(detect_stale=False)['status'] == 'idle'


def test_invalid_future_date_does_not_advance_watermark():
    with config.user_context('athlete_a'):
        initialize_history_import(reference_date=date.today() - timedelta(days=1))
        with pytest.raises(ValueError, match='future'):
            sync.sync_once(Client([activity('future', day=date.today()+timedelta(days=1))]))
        assert load_history_import(detect_stale=False)['latest_sync_date'] is None
