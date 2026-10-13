import json
from datetime import date, timedelta

import pytest

from garmin_coach import config, dashboard, history_import, history_storage
from garmin_coach.coach_sport_context import build_coach_sport_context

REFERENCE = date(2026, 10, 12)


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'GLOBAL_DATA_DIR', tmp_path)
    monkeypatch.setattr(config, 'USERS_DATA_DIR', tmp_path / 'users')
    monkeypatch.setattr(config, 'SECRETS_DIR', tmp_path / '.secrets')


def history(user_id, sports, age=0):
    with config.user_context(user_id):
        history_import.initialize_history_import('all', reference_date=REFERENCE)
        for i, sport in enumerate(sports):
            history_storage.save_activity_metadata({
                'activityId': f'{user_id}-{i}',
                'activityName': 'PRIVATE NAME',
                'ownerFullName': 'PRIVATE OWNER',
                'startLatitude': 50.123,
                'activityType': {'typeKey': sport},
                'startTimeLocal': f'{(REFERENCE-timedelta(days=age+i)).isoformat()}T08:00:00',
                'movingDuration': 2400,
                'distance': 6000,
            })


def test_coach_context_includes_multisport_evidence_and_provenance():
    history('u_multi', ['running', 'soccer', 'cycling', 'lap_swimming'])
    with config.user_context('u_multi'):
        context = dashboard.coach_context()
    multi = context['contexte_multisport']
    assert multi['status'] == 'available'
    summary = multi['training_history_summary']
    assert summary['source'] == 'multisport'
    assert summary['as_of'] == REFERENCE.isoformat()
    assert summary['coverage']['activity_count'] == 4
    counts = summary['windows']['last_28_days']['sport_activity_counts']
    assert counts['soccer'] == counts['cycling'] == counts['swimming'] == 1
    assert 'soccer' in multi['athlete_state']['sport_profile']['sports_observed']
    assert isinstance(context['courses_recentes'], list)
    assert counts['running'] == 1
    payload = json.dumps(multi)
    assert 'PRIVATE' not in payload
    assert 'startLatitude' not in payload
    assert 'u_multi' not in payload
    assert multi['athlete_state']['confidence']['heart_rate'] == 'none'


def test_multisport_context_isolated_and_empty_does_not_invent_metrics():
    history('u_soccer', ['soccer'])
    history('u_bike', ['cycling', 'cycling'])
    soccer = build_coach_sport_context('u_soccer')
    bike = build_coach_sport_context('u_bike')
    empty = build_coach_sport_context('u_empty', reference_date=REFERENCE)
    assert soccer['athlete_state']['sport_profile']['sports_observed'] == ['soccer']
    assert bike['athlete_state']['sport_profile']['sports_observed'] == ['cycling']
    assert empty['training_history_summary']['coverage']['activity_count'] == 0
    assert empty['athlete_state']['confidence']['current_state'] == 'none'
    assert empty['athlete_state']['confidence']['heart_rate'] == 'none'
    assert empty['athlete_state']['sport_profile']['sports_observed'] == []


def test_old_history_not_presented_as_recent():
    history('u_old', ['cycling', 'running'], age=240)
    context = build_coach_sport_context('u_old')
    assert context['training_history_summary']['coverage']['activity_count'] == 2
    assert context['training_history_summary']['windows']['last_28_days']['activity_count'] == 0
    assert context['athlete_state']['confidence']['current_state'] in {'none', 'low'}
    assert context['athlete_state']['current_training']['last_activity_age_days'] == 240


def test_context_failure_is_explicit_and_conversation_stays_available(monkeypatch):
    def fail(_):
        raise ValueError('PRIVATE ERROR')
    monkeypatch.setattr(dashboard, 'build_coach_sport_context', fail)
    with config.user_context('u_failure'):
        context = dashboard.coach_context()
    assert context['contexte_multisport'] == {'status': 'unavailable'}
    assert 'conversation_recente' in context
    assert 'PRIVATE ERROR' not in json.dumps(context)


def test_missing_duration_remains_unknown():
    with config.user_context('u_missing'):
        history_import.initialize_history_import('all', reference_date=REFERENCE)
        history_storage.save_activity_metadata({
            'activityId': 'missing-duration',
            'activityType': {'typeKey': 'soccer'},
            'startTimeLocal': REFERENCE.isoformat() + 'T08:00:00',
        })
    context = build_coach_sport_context('u_missing')
    window = context['training_history_summary']['windows']['last_28_days']
    assert window['activity_count'] == 1
    assert window['observed_duration_seconds'] is None
    assert window['metric_coverage']['duration']['available_count'] == 0


def test_multisport_evidence_reaches_ai_request_without_live_api(monkeypatch):
    from garmin_coach import coach_chat
    from garmin_coach.config import Settings

    history('u_ai', ['soccer', 'cycling'])
    captured = {}
    expected = coach_chat.CoachResponse(message='Le contexte comprend le football et le vélo.')

    class Responses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return type('Result', (), {'output_parsed': expected})()

    class Client:
        def __init__(self, **kwargs):
            self.responses = Responses()

    monkeypatch.setattr(coach_chat, 'OpenAI', Client)
    with config.user_context('u_ai'):
        context = dashboard.coach_context()
        result = coach_chat.generate_response(
            {'content': 'Que vois-tu dans mon historique multisport ?'},
            Settings(None, None, 300, 'test-key', 'test-model'),
            context,
        )
    payload = json.loads(captured['input'])
    evidence = payload['contexte']['contexte_multisport']['training_history_summary']
    assert {s['sport'] for s in evidence['sports']} == {'soccer', 'cycling'}
    assert evidence['source'] == 'multisport'
    assert result.action == 'none'
