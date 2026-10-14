"""Controlled real conversation validation on isolated synthetic history.

No Garmin client or real athlete data is used. Run --real --env <existing .env>
from the phase 1.2B worktree. Only provider usage and synthetic results are saved.
"""
from __future__ import annotations
import argparse
import json
import sys
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from garmin_coach import config, dashboard, coach_chat, history_import, history_storage

REFERENCE = date(2026, 10, 14)


def seed():
    history_import.initialize_history_import('all', reference_date=REFERENCE)
    sessions = [
        ('2025-06-05','running',5000,1800), ('2025-06-12','running',10000,3600),
        ('2025-06-20','cycling',20000,3600),
        ('2025-07-05','cycling',15000,2400), ('2025-07-19','cycling',25000,3600),
        ('2025-09-02','cycling',20000,3000), ('2025-09-08','cycling',30000,4200),
        ('2025-09-20','cycling',40000,5400),
        ('2026-09-04','running',6000,2160), ('2026-09-15','running',18000,6480),
        ('2026-09-22','cycling',50000,7200),
        ('2026-10-10','running',5000,1800), ('2026-10-13','running',6000,2160),
    ]
    for i,(day,sport,distance,duration) in enumerate(sessions):
        history_storage.save_activity_metadata({
            'activityId':f'private-id-{i}', 'activityName':'PRIVATE_SYNTHETIC_TITLE',
            'ownerEmail':'private-synthetic@example.com', 'latitude':50.0,
            'activityType':{'typeKey':sport},'startTimeLocal':f'{day}T08:00:00',
            'distance':distance,'movingDuration':duration,'averageHR':135,
            'maxHR':155,'elevationGain':25,'activityTrainingLoad':40,
        })
    history_import.update_history_import({'status':'complete','pagination_complete':True})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--real',action='store_true')
    parser.add_argument('--env',type=Path)
    parser.add_argument('--scenario', choices=['june_followup','compare','cycling_followup','long_run','empty'])
    parser.add_argument('--output',type=Path,default=Path('reports/phase-1.2b-real-conversations.json'))
    args=parser.parse_args()
    if not args.real: raise SystemExit('Use --real explicitly; this validation calls the existing AI provider.')
    if args.env: load_dotenv(args.env,override=True)
    settings=replace(config.load_settings(),email=None,password=None)
    if not settings.ai_api_key: raise SystemExit('No configured AI key; no requests made.')
    report={'reference_date':str(REFERENCE),'configured_provider':settings.ai_provider,
            'configured_model':settings.ai_model,'exchanges':[],'api_attempts':0,
            'only_synthetic_data':True,'garmin_calls':0}
    captured=[]
    original_client=coach_chat.OpenAI
    class ResponsesProxy:
        def __init__(self, inner): self.inner=inner
        def parse(self, **kwargs):
            sent=json.loads(kwargs['input'])
            encoded=json.dumps(sent,ensure_ascii=False)
            assert all(marker not in encoded for marker in ('PRIVATE_SYNTHETIC_TITLE','private-synthetic@example.com','private-id-','latitude'))
            captured.append(sent)
            report['api_attempts']+=1
            return self.inner.parse(**kwargs)
    class ClientProxy:
        def __init__(self,**kwargs):
            inner=original_client(**kwargs)
            self.responses=ResponsesProxy(inner.responses)
    coach_chat.OpenAI=ClientProxy
    with tempfile.TemporaryDirectory(prefix='motionest-12b-real-') as temporary:
        root=Path(temporary)
        config.GLOBAL_DATA_DIR=root
        config.USERS_DATA_DIR=root/'users'
        config.SECRETS_DIR=root/'secrets'
        with config.user_context('synthetic-athlete'):
            seed()
            for name,prompts in (
                ('june_followup',['Analyse mes entraînements du mois de juin.','2025']),
                ('compare',['Compare septembre 2025 avec juillet 2025.']),
                ('cycling_followup',['Comment mon volume de vélo a-t-il évolué ?','De juillet à septembre 2025.']),
                ('long_run',['Retrouve les informations disponibles sur ma sortie longue de course du mois dernier.']),
                ('empty',['Analyse mes entraînements de mai 2025.']),
            ):
                if args.scenario and name != args.scenario: continue
                for prompt in prompts:
                    request=coach_chat.submit_message(prompt)
                    context=dashboard.coach_context()
                    previous=len(captured)
                    result={'scenario':name,'prompt':prompt}
                    try:
                        response=coach_chat.generate_response(request,settings,context)
                        coach_chat.store_response(request['id'],response)
                        result.update(message=response.message,action=response.action,
                            conversation_state=response.conversation_state,
                            real_call=len(captured)>previous)
                        assert response.action=='none', 'A historical consultation must not propose a training action'
                        if len(captured)>previous:
                            search=captured[-1]['contexte'].get('recherche_historique')
                            result['retrieved_facts']=search
                            assert search and search.get('status')=='available', 'Real response must receive historical facts'
                    except Exception as exc:
                        result['error_type']=type(exc).__name__
                        coach_chat.mark_request_failed(request['id'])
                    report['exchanges'].append(result)
                    print(f'{name}: {result.get("conversation_state", result.get("error_type"))}',flush=True)
            usage=root/'users'/'synthetic-athlete'/'ai_usage.json'
            report['usage']=json.loads(usage.read_text(encoding='utf-8')) if usage.exists() else []
            report['recorded_input_tokens']=sum(entry['input_tokens'] for entry in report['usage'])
            report['recorded_output_tokens']=sum(entry['output_tokens'] for entry in report['usage'])
            report['recorded_total_tokens']=sum(entry['total_tokens'] for entry in report['usage'])
    target=args.output
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('exchanges','usage')},ensure_ascii=False))
    if any('error_type' in e for e in report['exchanges']): raise SystemExit(1)

if __name__=='__main__': main()
