"""Independent phase-5 account/route invariants; all data and clients synthetic."""
import json
import threading
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pytest

from garmin_coach import account_security, athlete_memory, coach_chat, config, dashboard
from garmin_coach import garmin_setup, history_import, history_storage, user_accounts
from garmin_coach.availability import build_availability_v1
from garmin_coach.goal import build_goal_v1
from garmin_coach.runtime_context import save_motionest_inputs_v1
from garmin_coach.local_activity_history import load_local_activity_history
from garmin_coach.subjective_context import build_subjective_context_v1
from garmin_coach.user_journey import determine_user_stage
from tests.test_user_journey import _write_tokens


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'GLOBAL_DATA_DIR',tmp_path)
    monkeypatch.setattr(config,'USERS_DATA_DIR',tmp_path/'users')
    monkeypatch.setattr(config,'SECRETS_DIR',tmp_path/'.secrets')
    monkeypatch.setattr(user_accounts,'USERS_FILE',tmp_path/'users.json')
    monkeypatch.setattr(user_accounts,'SESSION_KEY_FILE',tmp_path/'session.key')
    monkeypatch.setattr(account_security,'OUTBOX_DIR',tmp_path/'outbox')
    monkeypatch.delenv('MOTIONEST_LOCAL_AUTH_BYPASS',raising=False)
    (tmp_path/'users.json').write_text(json.dumps([
        {'id':'alice','email':'alice@example.invalid','email_verified':True},
        {'id':'bob','email':'bob@example.invalid','email_verified':True},
        {'id':'unverified','email':'unverified@example.invalid','email_verified':False},
    ]),encoding='utf-8')
    for user in ('alice','bob','unverified'):
        with config.user_context(user):
            config.ensure_directories()
            config.ONBOARDING_FILE.write_text(json.dumps({'status':'ready','onboarding_version':2}),encoding='utf-8')
    yield tmp_path


@pytest.fixture
def server():
    # Real handler and real signed sessions: no authentication bypass.
    httpd=ThreadingHTTPServer(('127.0.0.1',0),dashboard.DashboardHandler)
    worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
    yield f'http://127.0.0.1:{httpd.server_port}'
    httpd.shutdown();httpd.server_close();worker.join(timeout=3)


def request(base,path,*,user=None,payload=None,method='GET'):
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    headers={}
    if user:headers['Cookie']=f'motionest_session={user_accounts.create_session(user)}'
    data=None
    if payload is not None:
        headers['Content-Type']='application/json';data=json.dumps(payload).encode()
    try:response=build_opener(NoRedirect()).open(Request(base+path,data=data,headers=headers,method=method),timeout=4)
    except HTTPError as error:response=error
    with response:
        body=response.read()
        return response.status,response.headers,json.loads(body) if 'application/json' in response.headers.get('Content-Type','') else body.decode('utf-8')


def declare(user):
    save_motionest_inputs_v1(user,goal=build_goal_v1('progress'),
        availability=build_availability_v1(['tue','sat'],preferred_sessions_per_week=2),
        subjective_context=build_subjective_context_v1(affecting_training=False))
    with config.user_context(user):
        config.PROFILE_FILE.write_text(json.dumps({'objective':{'title':'Course déclarée 10km','distanceKm':10,'sessionsPerWeek':2}}),encoding='utf-8')
        message=coach_chat.submit_message('Je préfère le mardi et le samedi.')
        coach_chat.store_response(message['id'],coach_chat.CoachResponse(message='Préférences déclarées conservées.'))
        athlete_memory.apply_memory_updates([dict(operation='add',category='preference',key='preferred_days',scope='durable',
            text='Préférence pour mardi et samedi.',source_excerpt='mardi et le samedi')],
            source_message_id=message['id'],source_text=message['content'])
        return {str(path):path.read_bytes() for path in (config.PROFILE_FILE,config.MOTIONEST_INPUTS_FILE,
            config.COACH_MEMORY_FILE,coach_chat.MESSAGES_FILE)}


@pytest.mark.parametrize('user,expected',[(None,401),('unverified',403)])
def test_discovery_opt_in_requires_existing_verified_session(server,user,expected):
    status,_,_=request(server,'/api/onboarding/continue-without-garmin',user=user,payload={},method='POST')
    assert status==expected
    for identity in ('alice','bob','unverified'):
        with config.user_context(identity):
            assert not json.loads(config.ONBOARDING_FILE.read_text()).get('discovery_without_garmin')


def test_explicit_choice_is_per_account_idempotent_and_never_seeds_real_history(server,monkeypatch):
    snapshots=declare('alice')
    def forbidden(*args,**kwargs):raise AssertionError('Discovery must not connect or start Garmin automatically')
    monkeypatch.setattr(dashboard,'connect',forbidden)
    monkeypatch.setattr(dashboard,'start_initial_setup',forbidden)
    for _ in range(2):
        status,_,_=request(server,'/api/onboarding/continue-without-garmin',user='alice',
            payload={'user_id':'bob'},method='POST')
        assert status==200
    with config.user_context('alice'):
        assert json.loads(config.ONBOARDING_FILE.read_text())['discovery_without_garmin'] is True
        assert dashboard.load_public_activities()==[]
        context=dashboard.coach_context()
        assert context['connexion_sportive']['garmin_connected'] is False
        assert context['connexion_sportive']['discovery_without_garmin'] is True
        assert all(week['summary']['activity_count']==0 for week in context['contexte_multisport']['weeks'].values())
    with config.user_context('bob'):
        assert not json.loads(config.ONBOARDING_FILE.read_text()).get('discovery_without_garmin')
        assert coach_chat.list_messages()==[]
    assert determine_user_stage('alice')=='dashboard'
    assert request(server,'/dashboard',user='alice')[0]==200
    for path,content in snapshots.items():
        assert Path(path).read_bytes()==content


def test_discovery_never_starts_garmin_for_sync_or_publication(server,monkeypatch):
    declare('alice')
    assert request(server,'/api/onboarding/continue-without-garmin',user='alice',payload={},method='POST')[0]==200
    calls=[]
    monkeypatch.setattr(dashboard,'connect',lambda *a,**kw:calls.append('connect'))
    monkeypatch.setattr(dashboard,'start_initial_setup',lambda *a,**kw:calls.append('setup'))
    status,_,body=request(server,'/api/sync',user='alice',payload={},method='POST')
    assert status in (400,409) and body.get('ok') is False
    status,_,body=request(server,'/api/garmin/workout-delivery',user='alice',payload={'sessionIndex':0},method='POST')
    assert status in (400,409) and body.get('ok') is False
    assert calls==[]


def test_late_synthetic_import_preserves_declared_profile_memory_and_conversation(server,isolated,monkeypatch):
    snapshots=declare('alice')
    assert request(server,'/api/onboarding/continue-without-garmin',user='alice',payload={},method='POST')[0]==200
    _write_tokens(isolated,'alice')
    events=[]
    monkeypatch.setattr(garmin_setup,'connect',lambda settings:events.append('explicit-connect') or object())
    def import_metadata(client):
        history_import.initialize_history_import('all',reference_date=date(2026,10,20))
        history_storage.save_activity_metadata({'activityId':9001,'activityName':'Imported synthetic fixture',
            'startTimeLocal':'2026-10-19T08:00:00','activityType':{'typeKey':'running'},'distance':5000,'duration':1800})
        history_import.update_history_import({'status':'complete','pagination_complete':True})
    monkeypatch.setattr(garmin_setup,'paginate_activity_history',import_metadata)
    monkeypatch.setattr(garmin_setup,'import_multisport_details',lambda *a,**kw:{})
    garmin_setup.run_initial_setup('alice')
    assert events==['explicit-connect']
    for path,content in snapshots.items():
        assert Path(path).read_bytes()==content
    with config.user_context('alice'):
        assert [str(item['activityId']) for item in load_local_activity_history()]==['9001']
    with config.user_context('bob'):
        assert dashboard.load_public_activities()==[]


def test_existing_garmin_account_keeps_access_even_with_old_discovery_flag(server,isolated):
    declare('alice');_write_tokens(isolated,'alice')
    with config.user_context('alice'):
        config.ONBOARDING_FILE.write_text(json.dumps({'status':'complete','discovery_without_garmin':True}),encoding='utf-8')
    assert determine_user_stage('alice')=='dashboard'
    assert request(server,'/dashboard',user='alice')[0]==200
    assert request(server,'/api/activities',user='alice')[0]==200
    with config.user_context('alice'):
        source=dashboard.coach_context()['connexion_sportive']
        assert source['garmin_connected'] is True and source['discovery_without_garmin'] is False


def test_discovery_does_not_open_protected_apis_to_anonymous(server):
    for route in ('/api/activities','/api/profile','/api/coach/messages'):
        assert request(server,route)[0]==401


def test_discovery_dashboard_initial_read_routes_are_available_without_recommendation(server):
    declare('alice')
    assert request(server,'/api/onboarding/continue-without-garmin',user='alice',payload={},method='POST')[0]==200
    # These are exactly the real loadAll() Promise.all reads, not a mocked API.
    paths=('/api/activities','/api/plan','/api/plan-proposal','/api/plan-proposal/decision',
           '/api/garmin/workout-delivery','/api/profile','/api/coach/messages','/api/coach/status',
           '/api/onboarding','/api/first-run-journey')
    states={path:request(server,path,user='alice')[0] for path in paths}
    assert all(status==200 for status in states.values()),states
