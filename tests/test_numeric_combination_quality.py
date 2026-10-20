"""Missing interaction coverage: actual history/context/conversation + 3B patch.

The six-call measurement harness is exercised offline, with zero model requests.
Existing 3B/3C contract and delivery campaigns are reused rather than duplicated.
"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from garmin_coach import coach_chat, config


def load_harness(monkeypatch):
    root=Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root/'scripts'))
    spec=importlib.util.spec_from_file_location('numeric_combination_harness',root/'scripts'/'validate_numeric_combination.py')
    harness=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    return harness


def test_six_combined_hypotheses_are_checked_offline_through_real_local_paths(tmp_path,monkeypatch):
    harness=load_harness(monkeypatch)
    def parse(**kwargs):
        payload=json.loads(kwargs['input'])
        name=next(name for name,prompt in harness.PROMPTS.items() if prompt==payload['demande'])
        context=payload['contexte']
        if name=='complex_blocks':
            response=coach_chat.CoachResponse(message='Je propose ces trois blocs sans publication.',
                action='modify_session',session_index=0,session_patch=coach_chat.SessionPatch(
                    blocks=[dict(effort_minutes=3,repetitions=2,recovery_minutes=.5,intensity_instruction='Facile.'),
                            dict(effort_minutes=2,repetitions=2,recovery_minutes=.75,intensity_instruction='Facile.'),
                            dict(effort_minutes=3,repetitions=1,recovery_minutes=1.5,intensity_instruction='Facile.')],
                    duree_estimee_minutes=32))
        elif name=='triathlon':
            distances=context['contexte_multisport']['weeks']['last_7_days']['summary']['observed_distance_by_sport_km']
            response=coach_chat.CoachResponse(message=f"Course {distances['running']:g} km ; vélo {distances['cycling']:g} km ; natation {distances['swimming']:g} km. Distances distinctes.")
        elif name=='double_day':
            response=coach_chat.CoachResponse(message='Deux séances prévues : 1 heure au total. Import incomplet : leur durée ne prouve pas la fatigue.')
        elif name=='duration_84':
            response=coach_chat.CoachResponse(message='30 h 30 min et 2 h 32 min 30 s par semaine, observées.')
        else:
            response=coach_chat.CoachResponse(message='10 h 30 min et 2 h 37 min 30 s par semaine, observées.')
        return SimpleNamespace(output_parsed=response)
    monkeypatch.setattr(coach_chat,'OpenAI',lambda **kwargs:SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    monkeypatch.setattr(coach_chat,'record_usage',lambda *args,**kwargs:None)
    monkeypatch.setattr(config,'load_settings',lambda:config.Settings(None,None,300,'synthetic-key','synthetic-model'))
    output=tmp_path/'offline-harness.json'
    monkeypatch.setattr('sys.argv',['validate_numeric_combination.py','--real','--output',str(output)])
    harness.main()
    report=json.loads(output.read_text(encoding='utf-8'))
    assert report['api_attempts']==6 and report['api_call_ceiling']==6
    assert all(item['functional_success'] for item in report['profiles'])
    assert report['profiles'][-1]['conversation_messages']>=3
    interrupted=next(item for item in report['profiles'] if item['name']=='double_day')
    assert interrupted['import_status']=='interrupted'
    assert interrupted['history_search']['import_status']=='interrupted'
    assert report['real_garmin_calls']==0 and report['total_tokens']==0


def test_numeric_answer_checker_rejects_original_wrong_conversion_and_weekly_average(monkeypatch):
    harness=load_harness(monkeypatch)
    numeric={'summaries':{'example':{'observed_duration_seconds':37800}}}
    for message in ('37 h 48 min, 2 h 37 min 30 s par semaine.',
                    '10 h 30 min, 2 h 32 min 30 s par semaine.'):
        try:
            harness.validate_response('duration_28',coach_chat.CoachResponse(message=message),{},numeric)
        except AssertionError:
            pass
        else:
            raise AssertionError('The known wrong numeric result passed measurement checks')
    context={'contexte_multisport':{'weeks':{'last_7_days':{'summary':{
        'observed_distance_by_sport_km':{'running':12,'cycling':60,'swimming':3}}}}}}
    harness.validate_response('triathlon',coach_chat.CoachResponse(
        message='12 km en course ; 60 km en vélo ; 3 km en natation.'),context,numeric)
    try:
        harness.validate_response('triathlon',coach_chat.CoachResponse(
            message='Course : 99 km, vélo : 12 km, natation : 60 km et total 3 km.'),context,numeric)
    except AssertionError:
        pass
    else:
        raise AssertionError('Values from another discipline were incorrectly accepted')
