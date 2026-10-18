"""Bounded real-coach checks; only local plans and existing mock clients/helpers."""
from __future__ import annotations
import argparse, json, sys, tempfile
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from garmin_coach import coach_chat, config, dashboard
from garmin_coach.training_cycle import PlannedSession
from garmin_coach.garmin_workouts import build_running_workout
from tests.test_garmin_workouts import make_session

SCENARIOS = {
    'recovery_30': {'recovery':0.5, 'repetitions':4, 'effort':3, 'duration':29, 'distance':6},
    'recovery_45': {'recovery':0.75, 'repetitions':4, 'effort':3, 'duration':30, 'distance':6},
    'recovery_90': {'recovery':1.5, 'repetitions':4, 'effort':3, 'duration':33, 'distance':6},
    'repetitions_15': {'recovery':1, 'repetitions':15, 'effort':3, 'duration':75, 'distance':15},
    'long_180': {'recovery':0, 'repetitions':1, 'effort':165, 'duration':180, 'distance':30},
}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--real',action='store_true');parser.add_argument('--env',type=Path)
    parser.add_argument('--output',type=Path,default=Path('reports/phase-3a-real.json'));args=parser.parse_args()
    if not args.real:raise SystemExit('Use --real explicitly; no calls made.')
    if args.env:load_dotenv(args.env,override=True)
    settings=replace(config.load_settings(),email=None,password=None)
    if not settings.ai_api_key or settings.ai_provider!='openai' or settings.ai_base_url:raise SystemExit('Configured native OpenAI provider required; no calls made.')
    report={'synthetic_only':True,'real_garmin_calls':0,'api_attempts':0,'profiles':[]};original=coach_chat.OpenAI
    class Responses:
        def __init__(self,inner):self.inner=inner
        def parse(self,**kwargs):
            report['api_attempts']+=1
            assert 'PRIVATE-' not in kwargs['input']
            return self.inner.parse(**kwargs)
    class Client:
        def __init__(self,**kwargs):self.responses=Responses(original(**kwargs).responses)
    def save():
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='motionest-session-3a-') as tmp:
        root=Path(tmp)
        with patch.multiple(config, GLOBAL_DATA_DIR=root, USERS_DATA_DIR=root/'users', SECRETS_DIR=root/'secrets'),patch.object(coach_chat,'OpenAI',Client):
            for name,expected in SCENARIOS.items():
                with config.user_context(name):
                    session=make_session(date=str(date.today()+timedelta(days=2)))
                    plan={'seances':[session.model_dump(mode='json')]};target=root/f'{name}.json'
                    target.write_text(json.dumps(plan,ensure_ascii=False),encoding='utf-8')
                    prompt=(f'Modifie la séance 1 : {expected["repetitions"]} répétition(s) homogène(s) de {expected["effort"]} minutes, '
                        f'avec {expected["recovery"]*60:g} secondes de récupération dans chaque répétition, '
                        f'échauffement 10 minutes et retour au calme 5 minutes. Durée totale {expected["duration"]} minutes, '
                        f'distance estimée {expected["distance"]} km. Conserve sa date. '
                        'Je confirme cette modification précise du contenu ; propose son action structurée sans publication Garmin.')
                    context={'objectif':{'title':'Course à pied synthétique','sessionsPerWeek':3},'plan_actif':plan,
                             'onboarding':{'status':'ready'},'conversation_recente':[], 'memoire_pertinente':[]}
                    item={'name':name,'prompt':prompt,'expected':expected};report['profiles'].append(item)
                    try:
                        response=coach_chat.generate_response({'content':prompt,'coach_mode':'deep'},settings,context)
                        item.update(response=response.model_dump(mode='json'),technical_success=True,functional_success=False)
                        if response.action=='modify_session' and response.session_index==0 and response.session_patch:
                            changed=dashboard.modify_planned_session(target,0,response.session_patch.model_dump(exclude_none=True))
                            validated=PlannedSession.model_validate(changed);workout=build_running_workout(validated)
                            main=workout.workoutSegments[0].workoutSteps[1]
                            item['persisted']=changed;item['workout']=workout.model_dump(mode='json')
                            values_ok=(validated.recuperation_minutes==expected['recovery'] and validated.repetitions_bloc==expected['repetitions']
                                and validated.effort_minutes==expected['effort'] and validated.duree_estimee_minutes==expected['duration']
                                and validated.distance_estimee_km==expected['distance'] and validated.date==session.date
                                and workout.estimatedDurationInSecs==expected['duration']*60)
                            if expected['repetitions']>1:
                                values_ok=values_ok and main.numberOfIterations==expected['repetitions'] and main.workoutSteps[1].endConditionValue==expected['recovery']*60
                            item['functional_success']=bool(values_ok)
                    except Exception as exc:item.update(error_type=type(exc).__name__,technical_success=False,functional_success=False)
                    usage=root/'users'/name/'ai_usage.json';item['usage']=json.loads(usage.read_text()) if usage.exists() else []
                    save();print(name, 'technical',item.get('technical_success'), 'functional',item['functional_success'],flush=True)
    report['input_tokens']=sum(e['input_tokens'] for p in report['profiles'] for e in p['usage'])
    report['output_tokens']=sum(e['output_tokens'] for p in report['profiles'] for e in p['usage'])
    report['total_tokens']=report['input_tokens']+report['output_tokens'];save()
    print(json.dumps({k:v for k,v in report.items() if k!='profiles'}))
    if any(not p['functional_success'] for p in report['profiles']):raise SystemExit(1)

if __name__=='__main__':main()
