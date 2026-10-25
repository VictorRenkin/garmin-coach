"""Isolated synthetic Web fixture for accompanied browser checks; no real services.

Starts a loopback server with the production handler/UI/worker and a fake model.
Writes metadata into --output. Create the indicated stop file to stop it.
"""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    candidate=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='motionest-v2-browser-') as temporary:
        root=Path(temporary)
        shutil.copytree(candidate/'garmin_coach',root/'garmin_coach',ignore=shutil.ignore_patterns('__pycache__'))
        sys.path[:0]=[str(root),str(candidate)]
        from garmin_coach import config, dashboard, user_accounts, coach_chat, plan_proposal, plan_acceptance, garmin_delivery
        from garmin_coach import coach_session_actions as actions
        from tests.test_calendar_3c import source_for, double_proposal
        from tests.test_plan_proposal import settings
        assert config.ROOT_DIR.samefile(root)
        user='synthetic-browser-v2';source=source_for(user)
        raw=source.model_dump(mode='json')
        raw['period']={'start':'2026-10-26','end':'2026-11-01'}
        raw['allowed_dates']=['2026-10-26','2026-10-27','2026-10-29','2026-10-31']
        raw['blocked_dates']=[]
        source=plan_proposal.PlanGenerationInputV2.model_validate(raw)
        proposal_raw=double_proposal().model_dump(mode='json')
        proposal_raw['period']=raw['period']
        for index,session in enumerate(proposal_raw['sessions']):
            session.update(date='2026-10-27',starts_at=f'2026-10-27T{8 if index==0 else 18:02d}:00:00+01:00',
                duration_minutes=35,objective='Endurance facile synthétique',intensity_instruction='Rester en aisance respiratoire.',
                structure={'warmup_minutes':5,'main_set':'Courir facilement 25 minutes.','effort_minutes':25,'cooldown_minutes':5})
        proposal=plan_proposal.PlanProposalV2.model_validate(proposal_raw)
        assert plan_proposal.validate_plan_proposal(proposal,source).valid
        plan_proposal.build_plan_generation_input_v2=lambda *a,**kw:source
        plan_proposal.ai_provider.structured_completion=lambda *a,**kw:proposal
        actions._source=lambda _:source
        user_accounts.USERS_FILE.write_text(json.dumps([{'id':user,'email':'browser@example.invalid','email_verified':True}]),encoding='utf-8')
        remote_calls=[]
        def forbidden(*a,**kw):
            remote_calls.append('forbidden');raise AssertionError('Real service forbidden in browser fixture')
        for key in ('connect','authenticate_with_credentials','start_initial_setup','replace_future_plan','remove_future_sessions','publish_plan'):
            setattr(dashboard,key,forbidden)
        dashboard.load_settings=lambda:config.Settings(None,None,300,'synthetic-key','synthetic-model')
        dashboard.coach_ai_status=lambda:{'configured':True,'model':'synthetic-model','provider':'simulated'}
        def state():
            with config.user_context(user):return plan_proposal._state(source)
        dashboard.build_first_run_journey=lambda _:dict(stage='dashboard',actions=[],
            plan_proposal=state().model_dump(mode='json'),plan_acceptance=plan_acceptance.get_plan_acceptance_state(user,proposal_state=state()).model_dump(mode='json'))
        def reset():
            with config.user_context(user):
                for target in (config.PLAN_PROPOSAL_FILE,config.PLAN_ACCEPTANCE_FILE,config.GARMIN_DELIVERY_FILE,coach_chat.MESSAGES_FILE,actions._path()):
                    target.unlink(missing_ok=True)
                config.ONBOARDING_FILE.write_text(json.dumps({'status':'complete','onboarding_version':2,'discovery_without_garmin':True}),encoding='utf-8')
                plan_proposal.generate_plan_proposal(user,settings=settings())
        def snapshot():
            with config.user_context(user):
                return {str(path.name):hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None for path in
                    (config.PLAN_PROPOSAL_FILE,config.PLAN_ACCEPTANCE_FILE,config.GARMIN_DELIVERY_FILE)}
        calls=[]
        class Responses:
            def parse(self,**kw):
                payload=json.loads(kw['input']);content=payload['demande'].lower();calls.append(content)
                if 'supprim' in content:
                    response=coach_chat.CoachResponse(message='Le retrait local n’est pas actif : il changerait la fréquence confirmée. Aucune séance supprimée.')
                else:
                    intent=dict(action='modify',session_id='morning',target_duration_minutes=25)
                    if 'jeudi' in content:intent=dict(action='move',session_id='evening',target_date='2026-10-29')
                    elif 'heure' in content:intent=dict(action='change_time',session_id='evening',target_time='20:00')
                    elif 'facile' in content:intent=dict(action='modify',session_id='morning',patch={'intensity_instruction':'Très facile, sans rechercher la vitesse.'})
                    response=coach_chat.CoachResponse(message='Proposition locale synthétique.',session_action_v2=intent)
                return SimpleNamespace(output_parsed=response)
        coach_chat.OpenAI=lambda **kw:SimpleNamespace(responses=Responses())
        with config.user_context(user):
            reset()
            request=coach_chat.submit_message('Raccourcis la séance du matin à 25 minutes.')
            coach_chat.store_response(request['id'],coach_chat.CoachResponse(message='Aperçu synthétique.',
                session_action_v2=actions.SessionActionIntent(action='modify',session_id='morning',target_duration_minutes=25)))
        class Handler(dashboard.DashboardHandler):
            def do_POST(self):
                if not self.path.startswith('/__fixture__/'):return super().do_POST()
                authenticated=self._require_user(self.path)
                if authenticated!=user:return
                with config.user_context(user):
                    if self.path.endswith('/reset'):reset()
                    elif self.path.endswith('/stale'):
                        plan_proposal.revise_plan_session(user,session_id='evening',source=source,
                            expected_proposal_fingerprint=plan_proposal.proposal_fingerprint(state().proposal),
                            patch={'objective':'Autre version synthétique.'})
                    elif self.path.endswith('/published'):
                        garmin_delivery._write_store({'schema_version':1,'deliveries':{'synthetic':{'session_id':'morning','workout_id':701}}})
                    dashboard._json_response(self,{'ok':True,'snapshot':snapshot(),'proposal':state().proposal.model_dump(mode='json')})
        server=dashboard.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        stop_file=out/f'STOP-{uuid.uuid4().hex}'
        metadata={'url':f'http://127.0.0.1:{server.server_port}','user':user,'cookie':user_accounts.create_session(user),
            'stop_file':str(stop_file),'initial_snapshot':snapshot(),'source_sha256':hashlib.sha256((candidate/'garmin_coach/dashboard_v2.html').read_bytes()).hexdigest()}
        (out/'server.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
        print(json.dumps({'url':metadata['url'],'metadata':str(out/'server.json')}),flush=True)
        try:
            while not stop_file.exists():time.sleep(.2)
        finally:
            server.shutdown();server.server_close();worker.join(timeout=3)
            for thread in threading.enumerate():
                if thread.name.startswith('motionest-coach-'):thread.join(timeout=3)
            (out/'server-results.json').write_text(json.dumps({'fake_model_calls':len(calls),'garmin_calls':len(remote_calls),
                'server_stopped':not worker.is_alive(),'final_snapshot':snapshot()},indent=2),encoding='utf-8')


if __name__=='__main__':main()
