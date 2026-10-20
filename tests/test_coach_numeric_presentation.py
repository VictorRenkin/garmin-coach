"""Regression cases at the model boundary; canonical facts stay unchanged."""
import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from garmin_coach import coach_chat
from garmin_coach.coach_numeric_presentation import build_numeric_presentation
from garmin_coach.config import Settings


def context():
    return {"contexte_multisport": {
        "status": "available",
        "history": {"windows": {
            "last_28_days": {"start_date": "2026-09-18", "end_date": "2026-10-15",
                "observed_duration_seconds": 37800,
                "observed_distance_by_sport_km": {"running": 124},
                "metric_coverage": {"duration": {"status": "partial"}}},
            "last_84_days": {"start_date": "2026-07-24", "end_date": "2026-10-15",
                "observed_duration_seconds": 109800,
                "observed_distance_by_sport_km": {"running": 364}},
        }, "scope": "all", "confidence": {"current_state_limitation": "missing_recovery"}},
        "weeks": {
            "last_7_days": {"summary": {"start_date": "2026-10-09", "end_date": "2026-10-15",
                "observed_duration_seconds": 12600,
                "observed_distance_by_sport_km": {"running": 12, "cycling": 60, "swimming": 3}},
                "activities": [], "activity_list_complete": False, "activities_omitted": 10},
            "previous_7_days": {"summary": {"start_date": "2026-10-02", "end_date": "2026-10-08",
                "observed_duration_seconds": 10800,
                "observed_distance_by_sport_km": {"running": 12, "cycling": 50, "swimming": 3}}},
        }, "budget": {"max_bytes": 48000, "activities_truncated": True},
    }}


def display(value, suffix):
    return next(v for k, v in value["summaries"].items() if k.endswith(suffix))


def test_reference_durations_and_calendar_weekly_averages():
    source = context()
    before = copy.deepcopy(source)
    result = build_numeric_presentation(source)
    recent = display(result, "last_28_days")
    baseline = display(result, "last_84_days")
    assert recent["observed_duration_seconds"] == 37800
    assert recent["observed_duration_hms"] == "10 h 30 min 00 s"
    assert recent["average_weekly_observed_duration_seconds"] == 9450
    assert recent["average_weekly_observed_duration_hms"] == "2 h 37 min 30 s"
    assert baseline["observed_duration_hms"] == "30 h 30 min 00 s"
    assert baseline["average_weekly_observed_duration_seconds"] == 9150
    assert baseline["average_weekly_observed_duration_hms"] == "2 h 32 min 30 s"
    assert display(result, "last_7_days.summary")["observed_duration_hms"] == "3 h 30 min 00 s"
    assert source == before


@pytest.mark.parametrize("seconds,label", [(None, None), (0, "0 min 00 s"),
    (59.6, "1 min 00 s"), (3600, "1 h 00 min 00 s"), (True, None),
    (-1, None), (float('nan'), None), (float('inf'), None)])
def test_missing_zero_rounding_and_invalid_durations(seconds, label):
    source = context()
    source['contexte_multisport']['history']['windows']['last_28_days']['observed_duration_seconds'] = seconds
    result = display(build_numeric_presentation(source), 'last_28_days')
    assert result['observed_duration_hms'] == label
    if label is None:
        assert result['average_weekly_observed_duration_seconds'] is None
        assert result['average_weekly_observed_duration_hms'] is None


@pytest.mark.parametrize('start,end', [(None, None), ('invalid', '2026-10-15'),
    ('2026-10-16', '2026-10-15')])
def test_no_weekly_average_without_valid_calendar_period(start, end):
    source = context()
    source['contexte_multisport']['history']['windows']['last_28_days'].update(start_date=start, end_date=end)
    item = display(build_numeric_presentation(source), 'last_28_days')
    assert item['observed_duration_hms'] == '10 h 30 min 00 s'
    assert item['average_weekly_observed_duration_hms'] is None


def test_calendar_history_is_formatted_from_its_own_summary():
    source = context()
    source['recherche_historique'] = {'status': 'available', 'import_status': 'interrupted',
        'periods': [{'summary': {'start_date': '2026-09-01', 'end_date': '2026-09-30',
                                'observed_duration_seconds': 37800},
                     'coverage_status': 'incomplete_import'}]}
    before = copy.deepcopy(source)
    item = display(build_numeric_presentation(source), 'periods[0].summary')
    assert item['period_days'] == 30
    assert item['average_weekly_observed_duration_seconds'] == 8820
    assert item['average_weekly_observed_duration_hms'] == '2 h 27 min 00 s'
    assert source == before


@pytest.mark.parametrize('provider', ['openai', 'compatible'])
def test_model_receives_display_without_mutating_distances_confidence_or_import(monkeypatch, provider):
    source = context()
    source['recherche_historique'] = {'status': 'available', 'import_status': 'interrupted',
        'periods': [{'summary': {'observed_duration_seconds': None}, 'coverage_status': 'incomplete_import'}]}
    before = copy.deepcopy(source)
    captured = {}
    response = coach_chat.CoachResponse(message='Volumes observés par discipline.')
    def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_parsed=response)
    def completion(settings, **kwargs):
        captured.update(kwargs)
        return response
    monkeypatch.setattr(coach_chat, 'OpenAI', lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    monkeypatch.setattr(coach_chat, 'structured_completion', completion)
    monkeypatch.setattr(coach_chat, 'record_usage', lambda *args, **kwargs: None)
    settings = Settings(None, None, 300, 'synthetic-key', 'synthetic-model')
    if provider == 'compatible':
        settings = replace(settings, ai_base_url='https://synthetic.invalid/v1')
    coach_chat.generate_response({'content': 'Compare les volumes observés des deux semaines.'}, settings, source)
    sent = (json.loads(captured['input']) if provider == 'openai' else captured['payload'])['contexte']
    assert source == before
    assert sent['contexte_multisport'] == before['contexte_multisport']
    assert sent['recherche_historique'] == before['recherche_historique']
    assert display(sent['presentation_numerique'], 'last_28_days')['observed_duration_hms'] == '10 h 30 min 00 s'
    assert display(sent['presentation_numerique'], 'periods[0].summary')['observed_duration_hms'] is None
    # Canonical multisport distances remain separate; no new overall distance or inferred load.
    assert sent['contexte_multisport']['weeks']['last_7_days']['summary']['observed_distance_by_sport_km'] == {'running': 12, 'cycling': 60, 'swimming': 3}
    encoded = json.dumps(sent['presentation_numerique'])
    assert 'distance' not in encoded and 'training_load' not in encoded
    assert len(encoded.encode('utf-8')) < 4096
    assert 'Ne les additionne pas dans un total multisport' in captured['instructions']
    assert "n'est pas une mesure de" in captured['instructions']


def test_no_presentation_invented_when_canonical_context_unavailable():
    assert build_numeric_presentation({'contexte_multisport': {'status': 'unavailable'}}) == {}
    assert build_numeric_presentation({}) == {}


def test_companion_is_bounded_and_does_not_copy_private_or_activity_fields():
    source = context()
    example = source['contexte_multisport']['history']['windows']['last_28_days']
    source['recherche_historique'] = {'status': 'available', 'periods': [
        {'summary': dict(example, ownerEmail='PRIVATE EMAIL', activityId='PRIVATE ID'),
         'activities': [{'duration_seconds': 3600, 'name': 'PRIVATE TITLE'}] * 1000}
        for _ in range(3)]}
    result = build_numeric_presentation(source)
    assert len(result['summaries']) == 6
    encoded = json.dumps(result)
    assert len(encoded.encode('utf-8')) < 4096
    assert 'PRIVATE' not in encoded
    assert 'activities' not in encoded
