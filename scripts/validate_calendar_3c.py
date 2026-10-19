"""Bounded synthetic 3C validation: four coach intents and one V2 generation.

Explicit --real is mandatory. Fake Garmin clients only; no real athlete data.
"""
from __future__ import annotations
import argparse
import json
import sys
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from garmin_coach import config,coach_chat,ai_provider,plan_proposal,plan_acceptance,garmin_delivery
from tests.test_calendar_3c import source_for
from tests.test_garmin_delivery import VerifiedClient

CASES={
 'explicit_double':('ready_for_proposal','Je demande une double journée le 13 octobre 2099 : une course facile de 30 minutes à 08h00+02:00 puis une autre de 30 minutes à 16h00+02:00. Mes autorisations et bornes sont déclarées. Peut-on préparer cette proposition, sans encore modifier ni publier ?'),
 'beginner':('not_appropriate','Je débute la course et je voudrais deux séances de 30 minutes le 13 octobre 2099 à 08h00+02:00 et 16h00+02:00. Est-ce adapté à ma situation ?'),
 'ambiguous':('needs_clarification','Je voudrais peut-être courir deux fois un jour. Je ne sais pas encore quel jour ni à quelle heure.'),
 'existing_calendar':('blocked','Ajoute une seconde séance le 13 octobre 2099 à 16h00+02:00 malgré la séance externe déjà programmée ce jour-là. Ne supprime aucune séance existante.'),
 'v2_generation':(None,'Prépare exactement deux séances de course facile de 30 minutes chacune le 13 octobre 2099 : la première à 08h00+02:00, la seconde à 16h00+02:00. Les identifiants doivent être distincts. Aucun autre jour.')}


def seconds(steps):
 return sum(item.numberOfIterations*seconds(item.workoutSteps) if hasattr(item,'numberOfIterations') else item.endConditionValue for item in steps)


def main():
 parser=argparse.ArgumentParser();parser.add_argument('--real',action='store_true');parser.add_argument('--env',type=Path)
 parser.add_argument('--case',action='append',choices=list(CASES));parser.add_argument('--output',type=Path,default=Path('reports/phase-3c-real.json'));args=parser.parse_args()
 if not args.real:raise SystemExit('Use --real explicitly; no calls made. Cases: '+', '.join(CASES))
 if args.env:load_dotenv(args.env,override=True)
 settings=replace(config.load_settings(),email=None,password=None)
 if not settings.ai_api_key or settings.ai_provider!='openai' or settings.ai_base_url:raise SystemExit('Configured native OpenAI required; no calls made.')
 selected=args.case or list(CASES)
 report={'synthetic_only':True,'real_garmin_calls':0,'api_attempts':0,'api_call_ceiling':len(selected),'profiles':[]}
 original=coach_chat.OpenAI
 class Responses:
  def __init__(self,inner):self.inner=inner
  def parse(self,**kwargs):
   assert report['api_attempts']<report['api_call_ceiling'],'Call ceiling reached'
   report['api_attempts']+=1
   assert 'PRIVATE-' not in kwargs['input']
   result=self.inner.parse(**kwargs);report['profiles'][-1]['technical_success']=True;return result
 class Client:
  def __init__(self,**kwargs):
   kwargs['max_retries']=0;self.responses=Responses(original(**kwargs).responses)
 def save():
  usage=[entry for item in report['profiles'] for entry in item.get('usage',[])]
  for key in ('input_tokens','output_tokens','total_tokens'):report[key]=sum(entry[key] for entry in usage)
  args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 with tempfile.TemporaryDirectory(prefix='motionest-calendar-3c-') as tmp:
  root=Path(tmp)
  with patch.multiple(config,GLOBAL_DATA_DIR=root,USERS_DATA_DIR=root/'users',SECRETS_DIR=root/'secrets'),patch.object(coach_chat,'OpenAI',Client),patch.object(ai_provider,'OpenAI',Client):
   for name in selected:
    user='synthetic-'+name;expected,prompt=CASES[name]
    item={'name':name,'request':prompt,'technical_success':False,'functional_success':False};report['profiles'].append(item)
    with config.user_context(user):
     try:
      source=source_for(user)
      if name=='v2_generation':
       source.important_preferences=[prompt]
       with patch.object(plan_proposal,'build_plan_generation_input_v2',lambda *a,**kw:source):
        current=plan_proposal.generate_plan_proposal(user,settings=settings)
       item['state']=current.model_dump(mode='json')
       assert current.status=='available' and current.proposal is not None
       sessions=current.proposal.sessions
       assert len(sessions)==2 and {session.date for session in sessions}=={date(2099,10,13)}
       assert all(session.session_id and session.starts_at and session.sport=='running' and session.duration_minutes==30 for session in sessions)
       assert len({session.session_id for session in sessions})==2
       assert [session.starts_at.isoformat() for session in sessions]==['2099-10-13T08:00:00+02:00','2099-10-13T16:00:00+02:00']
       assert plan_proposal.validate_plan_proposal(current.proposal,source).valid
       for session in sessions:
        workout=garmin_delivery.build_plan_session_workout(session)
        assert seconds(workout.workoutSegments[0].workoutSteps)==1800==workout.estimatedDurationInSecs
       with patch.object(garmin_delivery,'get_plan_proposal_state',lambda user_id:current):
        client=VerifiedClient()
        def accept(session):
         plan_acceptance.record_plan_acceptance_decision(user,decision='accepted',input_fingerprint=current.input_fingerprint,
          expected_proposal_fingerprint=plan_proposal.proposal_fingerprint(current.proposal),proposal_state=current,session_id=session.session_id)
        accept(sessions[0]);first=garmin_delivery.publish_accepted_session(user,client,session_index=0,expected_session_id=sessions[0].session_id)
        assert garmin_delivery.publish_accepted_session(user,client,session_index=0,expected_session_id=sessions[0].session_id)==first
        try:garmin_delivery.publish_accepted_session(user,client,session_index=1,expected_session_id=sessions[1].session_id)
        except garmin_delivery.GarminDeliveryError as error:assert error.code=='session_not_accepted'
        else:raise AssertionError('Second session implicitly accepted')
        assert client.uploads==1
        accept(sessions[1]);second=garmin_delivery.publish_accepted_session(user,client,session_index=1,expected_session_id=sessions[1].session_id)
        assert first['garmin_workout_id']!=second['garmin_workout_id'] and client.uploads==2
        item['simulated_deliveries']=[first,second]
      else:
       goal='beginner' if name=='beginner' else 'progress'
       availability={'available_days':['tue'],'double_session_days':['tue'],'minimum_recovery_hours':6,'max_daily_duration_minutes':90}
       if name=='ambiguous':availability={'available_days':['tue']}
       context={'objectif':{'title':'Course synthétique','sessionsPerWeek':2},'onboarding':{'status':'ready'},
        'declarations_v1':{'goal':{'intent':goal},'availability':availability,'subjective_context':{'pain_or_injury':{'affecting_training':False}}},
        'contexte_multisport':{'status':'available','context':{'data_confidence':{'recovery':'high'}},'history':{}},
        'calendrier_plan_v2':{'status':'available','sessions':[{'session_id':'morning','date':'2099-10-13','starts_at':'2099-10-13T08:00:00+02:00','duration_minutes':30}],
          'blocked_dates':['2099-10-13'] if name=='existing_calendar' else []},'plan_actif':None,'memoire_pertinente':[],'conversation_recente':[]}
       response=coach_chat.generate_response({'content':prompt,'coach_mode':'deep'},settings,context)
       item['response']=response.model_dump(mode='json')
       assert response.action=='none' and not response.plan_modifie and response.calendar_request is not None
       assert response.calendar_request.decision==expected
       assert response.calendar_request.target_date==(None if name=='ambiguous' else date(2099,10,13))
      item['functional_success']=True
     except Exception as error:item['error_type']=type(error).__name__
     usage=root/'users'/user/'ai_usage.json';item['usage']=json.loads(usage.read_text(encoding='utf-8')) if usage.exists() else []
     save();print(name,'technical',item['technical_success'],'functional',item['functional_success'],flush=True)
 save();print(json.dumps({key:value for key,value in report.items() if key!='profiles'}))
 if any(not item['functional_success'] for item in report['profiles']):raise SystemExit(1)

if __name__=='__main__':main()
