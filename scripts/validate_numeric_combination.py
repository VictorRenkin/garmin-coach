"""Six bounded 3C/4A hypotheses. Real calls require explicit --real approval.

Synthetic histories use the existing FakeGarmin seed. Session changes are applied
only to temporary files and converted to SDK DTOs; nothing is uploaded remotely.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import tempfile
import unicodedata
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from garmin_coach import coach_chat, config, dashboard, history_import, sync
from garmin_coach.coach_numeric_presentation import build_numeric_presentation
from garmin_coach.garmin_workouts import build_running_workout
from garmin_coach.training_cycle import PlannedSession
from tests.test_garmin_workouts import make_session
from tests.test_calendar_3c import double_proposal, source_for
from validate_conversations_2a import REFERENCE, seed
from validate_numeric_4a import ScenarioDate

CASES = ('duration_28', 'duration_84', 'triathlon', 'double_day', 'complex_blocks', 'followup')
PROMPTS = {
    'duration_28': 'Donne mon temps observé sur 28 jours et sa moyenne hebdomadaire exacte en heures, minutes et secondes. Distingue les mesures enregistrées de ma charge physiologique. Ne change rien.',
    'duration_84': 'Donne mon temps observé sur 84 jours et sa moyenne hebdomadaire exacte en heures, minutes et secondes. Ne modifie aucune séance.',
    'triathlon': 'Pour la semaine récente last_7_days seulement, donne séparément les kilomètres enregistrés en course, vélo et natation. Explique pourquoi ces distances ne donnent pas une charge physiologique comparable. Ne crée aucune action.',
    'double_day': 'Le calendrier contient deux séances faciles de 30 minutes chacune à 08h00 et 16h00 le même jour. Analyse mes activités du 2026-09-18 au 2026-10-15 et compare le temps prévu total aux durées enregistrées sur cette période. Mon import est interrompu : explicite cette limite et les limites pour estimer ma fatigue. Ne crée, ne modifie et ne publie aucune séance.',
    'complex_blocks': 'Je confirme la modification de la séance 1, indice 0 : échauffement 10 minutes, puis bloc 1 deux répétitions de 3 minutes avec récupération de 30 secondes après chaque effort ; bloc 2 deux répétitions de 2 minutes avec récupération de 45 secondes après chaque effort ; bloc 3 une répétition de 3 minutes avec récupération de 90 secondes après cet effort. Retour au calme 5 minutes. Durée totale 32 minutes. Les récupérations comprennent la dernière de chaque bloc. Conserve sa date et ses autres paramètres. Produis modify_session avec blocks ; aucune publication Garmin.',
    'followup': 'Reprends les mêmes chiffres sur 28 jours et la même moyenne hebdomadaire que dans notre échange précédent, toujours en heures, minutes et secondes. Quels chiffres sont observés et pourquoi ne prouvent-ils pas ma fatigue ? Ne change rien.',
}


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))


def contains_duration(text, hours, minutes, seconds=None):
    # Accept compact French labels without relying on a single prose template.
    pattern = rf'\b{hours}\s*(?:h|heures?)\s*{minutes}\s*(?:min(?:utes?)?)?'
    if seconds is not None:
        pattern += rf'\s*{seconds}\s*(?:s|secondes?)\b'
    return bool(re.search(pattern, normalized(text)))


def executed_seconds(steps):
    return sum(step.numberOfIterations * executed_seconds(step.workoutSteps)
               if hasattr(step, 'numberOfIterations') else step.endConditionValue for step in steps)


def recoveries(steps):
    values = []
    for step in steps:
        if hasattr(step, 'numberOfIterations'):
            values.extend(recoveries(step.workoutSteps))
        elif step.stepType.get('stepTypeKey') == 'recovery':
            values.append(step.endConditionValue)
    return values


def validate_response(name, response, context, numeric, target=None):
    """Return reproducible assertions, beyond a successful API response."""
    checks = []
    assert numeric and len(json.dumps(numeric).encode('utf-8')) < 4096
    checks.append('deterministic_numeric_companion_present_and_bounded')
    if name == 'complex_blocks':
        assert response.action == 'modify_session' and response.session_index == 0 and response.session_patch
        before = json.loads(target.read_text(encoding='utf-8'))
        changed = dashboard.modify_planned_session(target, 0, response.session_patch.model_dump(exclude_none=True))
        validated = PlannedSession.model_validate(changed)
        assert validated.date == before['seances'][0]['date'] and validated.blocks
        assert [(b.repetitions, b.effort_minutes, b.recovery_minutes) for b in validated.blocks] == [(2,3,.5),(2,2,.75),(1,3,1.5)]
        assert validated.duree_estimee_minutes == 32
        dto = build_running_workout(validated)
        assert executed_seconds(dto.workoutSegments[0].workoutSteps) == 1920 == dto.estimatedDurationInSecs
        assert recoveries(dto.workoutSegments[0].workoutSteps) == [30,45,90]
        checks.extend(['structured_modify_session_index_zero', 'patch_persisted_and_revalidated',
                       'three_distinct_blocks_and_seconds_preserved_in_sdk', 'actual_executable_total_1920_seconds'])
        return checks
    assert response.action == 'none' and not response.plan_modifie
    checks.append('no_plan_action_or_publication')
    if name in ('duration_28', 'followup'):
        assert contains_duration(response.message, 10, 30)
        assert contains_duration(response.message, 2, 37, 30)
        checks.extend(['answer_10h30', 'answer_weekly_2h37m30s'])
    elif name == 'duration_84':
        assert contains_duration(response.message, 30, 30)
        assert contains_duration(response.message, 2, 32, 30)
        checks.extend(['answer_30h30', 'answer_weekly_2h32m30s'])
    elif name == 'triathlon':
        by_sport = context['contexte_multisport']['weeks']['last_7_days']['summary']['observed_distance_by_sport_km']
        text = normalized(response.message)
        for sport, words in [('running',r'(?:course|pied)'),('cycling',r'(?:velo|cycl)'),('swimming',r'natation')]:
            value = by_sport[sport]
            number = f'{value:g}'.replace('.', r'[.,]')
            if float(value).is_integer():
                number += r'(?:[.,]0+)?'
            measured=rf'\b{number}\s*km\b'
            between=r'(?:(?!(?:course|pied|velo|cycl|natation))[^;\n]){0,120}?'
            assert (re.search(words + between + measured, text)
                    or re.search(measured + between + words, text)), (sport, value)
        checks.append('response_distances_match_canonical_per_sport_week')
    elif name == 'double_day':
        sessions = context['calendrier_plan_v2']['sessions']
        assert len({s['session_id'] for s in sessions}) == 2
        assert [s['starts_at'][11:16] for s in sessions] == ['08:00','16:00']
        assert sum(s['duration_minutes'] for s in sessions) == 60
        assert re.search(r'\b(?:60\s*min(?:utes?)?|1\s*(?:h|heure))\b', normalized(response.message))
        assert re.search(r'incomplet|interromp|partiel', normalized(response.message))
        checks.extend(['distinct_calendar_ids_and_08h_16h', 'planned_total_60_minutes_repeated'])
    return checks


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--real',action='store_true')
    parser.add_argument('--env',type=Path)
    parser.add_argument('--case',action='append',choices=CASES)
    parser.add_argument('--output',type=Path,default=Path('reports/numeric-combination-real.json'))
    args=parser.parse_args()
    if not args.real:
        raise SystemExit('Use --real explicitly; no requests made. Ceiling: six, no retries.')
    selected=args.case or list(CASES)
    if len(selected)!=len(set(selected)) or ('followup' in selected and 'duration_28' not in selected):
        raise SystemExit('Cases must be unique; followup requires duration_28 in the same run.')
    selected=[name for name in CASES if name in selected]
    if args.env:
        load_dotenv(args.env,override=True)
    settings=replace(config.load_settings(),email=None,password=None)
    if not settings.ai_api_key or settings.ai_provider!='openai' or settings.ai_base_url:
        raise SystemExit('Configured native OpenAI required; no requests made.')
    report={'synthetic_only':True,'real_garmin_calls':0,'api_attempts':0,'api_call_ceiling':len(selected),
            'reference_date':str(REFERENCE),'profiles':[],'manual_review_required':['physiological_claims','heterogeneous_distance_addition','capacity_claims']}
    captured=[]
    original=coach_chat.OpenAI
    class Responses:
        def __init__(self,inner):self.inner=inner
        def parse(self,**kwargs):
            assert report['api_attempts']<report['api_call_ceiling']<=6
            payload=json.loads(kwargs['input'])
            text=json.dumps(payload)
            assert all(marker not in text for marker in ('PRIVATE-ID-','PRIVATE TITLE','private-synthetic@example.com','latitude','longitude'))
            sent=payload['contexte']; numeric=sent.get('presentation_numerique')
            assert numeric and len(json.dumps(numeric).encode())<4096
            assert sent['contexte_multisport']==report['profiles'][-1]['canonical_context']
            if report['profiles'][-1]['name']=='double_day':
                assert sent['calendrier_plan_v2']==report['profiles'][-1]['calendar_context']
                search=sent['recherche_historique']
                assert search['status']=='available' and search['import_status']=='interrupted'
                report['profiles'][-1]['history_search']=search
            captured.append(payload)
            report['api_attempts']+=1
            result=self.inner.parse(**kwargs)
            report['profiles'][-1]['technical_success']=True
            return result
    class Client:
        def __init__(self,**kwargs):
            kwargs['max_retries']=0
            self.responses=Responses(original(**kwargs).responses)
    def save():
        usage=[entry for profile in report['profiles'] for entry in profile.get('usage_new',[])]
        for key in ('input_tokens','output_tokens','total_tokens'):
            report[key]=sum(entry.get(key,0) for entry in usage)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='motionest-numeric-combination-') as temp:
        root=Path(temp)
        with patch.multiple(config,GLOBAL_DATA_DIR=root,USERS_DATA_DIR=root/'users',SECRETS_DIR=root/'secrets'),patch.object(coach_chat,'OpenAI',Client):
            for name in selected:
                user='synthetic-'+('duration_28' if name=='followup' else name)
                item={'name':name,'prompt':PROMPTS[name],'technical_success':False,'functional_success':False}
                report['profiles'].append(item)
                with config.user_context(user):
                    usage_path=root/'users'/user/'ai_usage.json'
                    before_usage=json.loads(usage_path.read_text(encoding='utf-8')) if usage_path.exists() else []
                    try:
                        if name!='followup':
                            with patch.object(sync,'date',ScenarioDate):
                                seed('triathlon' if name=='triathlon' else 'confirmed',
                                     ['running','cycling','lap_swimming']*2 if name=='triathlon' else ['running']*5)
                            if name=='double_day':
                                history_import.update_history_import({'status':'interrupted','pagination_complete':False})
                        request=coach_chat.submit_message(PROMPTS[name])
                        context=dashboard.coach_context()
                        target=None
                        if name=='double_day':
                            plan=double_proposal(source_for(user))
                            context['calendrier_plan_v2']={'status':'available','sessions':[
                                {key:s.model_dump(mode='json')[key] for key in ('session_id','date','starts_at','duration_minutes')}
                                for s in plan.sessions],'blocked_dates':[]}
                            item['calendar_fixture']=plan.model_dump(mode='json')
                            item['calendar_context']=copy.deepcopy(context['calendrier_plan_v2'])
                        elif name=='complex_blocks':
                            plan={'seances':[make_session(date='2099-10-13').model_dump(mode='json')]}
                            target=root/'synthetic-plan.json'
                            target.write_text(json.dumps(plan),encoding='utf-8')
                            context['plan_actif']=plan
                        if name=='followup':
                            assert len(context['conversation_recente'])>=3
                            assert any(m['role']=='assistant' for m in context['conversation_recente'])
                        numeric=build_numeric_presentation(context)
                        item.update(canonical_context=copy.deepcopy(context['contexte_multisport']),numeric=numeric,
                                    conversation_messages=len(context['conversation_recente']),
                                    import_status=history_import.load_history_import(detect_stale=False)['status'])
                        windows=context['contexte_multisport']['history']['windows']
                        if name!='triathlon':
                            assert windows['last_28_days']['observed_duration_seconds']==37800
                            assert windows['last_84_days']['observed_duration_seconds']==109800
                        before=len(captured)
                        response=coach_chat.generate_response(request,settings,context)
                        assert len(captured)==before+1,'Expected one measured real model call'
                        item['response']=response.model_dump(mode='json')
                        # Preserve a technically successful exchange even if its
                        # numeric assertions fail, so the follow-up remains real.
                        coach_chat.store_response(request['id'],response)
                        item['payload_bytes']=len(json.dumps(captured[-1],ensure_ascii=False,separators=(',',':')).encode())
                        item['checks']=validate_response(name,response,context,numeric,target)
                        item['functional_success']=True
                    except Exception as error:
                        item['error_type']=type(error).__name__
                        if isinstance(error,AssertionError):
                            item['error_detail']=str(error)[:300]
                    usage=json.loads(usage_path.read_text(encoding='utf-8')) if usage_path.exists() else []
                    item['usage_new']=usage[len(before_usage):]
                    save()
                    print(name,'technical',item['technical_success'],'functional',item['functional_success'],flush=True)
    save()
    print(json.dumps({key:value for key,value in report.items() if key!='profiles'}))
    if any(not item['functional_success'] for item in report['profiles']):
        raise SystemExit(1)


if __name__=='__main__':main()
