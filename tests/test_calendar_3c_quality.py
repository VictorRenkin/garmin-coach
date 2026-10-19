"""Independent calendar adversaries; synthetic state and VerifiedClient only."""
import hashlib
import json
from datetime import date, datetime, timedelta

import pytest
from pydantic import ValidationError

from garmin_coach import config, garmin_delivery as delivery, plan_acceptance as acceptance
from garmin_coach import plan_proposal as proposal, training_cycle
from garmin_coach.plan_proposal import PlanProposalV2, PlanProposalStateV2, PlanSessionV2
from tests.test_garmin_delivery import VerifiedClient, proposal_state, running_session
from tests.test_plan_proposal import runtime_for, states


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config,'GLOBAL_DATA_DIR',tmp_path)
    monkeypatch.setattr(config,'USERS_DATA_DIR',tmp_path/'users')
    monkeypatch.setattr(config,'SECRETS_DIR',tmp_path/'secrets')


def source():
    runtime=runtime_for('quality-calendar')
    recommended,decided=states(runtime,confirmed=2)
    value=proposal.build_plan_generation_input_v2('quality-calendar',runtime,
        recommendation_state=recommended,decision_state=decided,period_start=date(2099,10,12)).model_dump()
    value['plan_constraints'].update(double_session_days=['tue'],minimum_recovery_hours=6,
        max_daily_duration_minutes=90)
    value['confidence']['recovery']='high'
    value['training_facts']['pain_affecting_training']=False
    return proposal.PlanGenerationInputV2.model_validate(value)


def session(identity,start):
    return PlanSessionV2(session_id=identity,date=date.fromisoformat(start[:10]),
        starts_at=datetime.fromisoformat(start),sport='running',session_type='easy',
        objective='Une séance synthétique.',duration_minutes=30,
        intensity_instruction='Rester facile.',explanation='Répartition synthétique.',
        structure=dict(warmup_minutes=5,main_set='Vingt minutes faciles.',effort_minutes=20,cooldown_minutes=5))


def current(first='2099-10-13T08:00:00+02:00',second='2099-10-13T14:30:00+02:00'):
    plan=PlanProposalV2(period=source().period,summary='Calendrier synthétique.',
        strategy='Deux séances distinctes et espacées.',sessions=[session('alpha',first),session('beta',second)])
    return PlanProposalStateV2(status='available',input_fingerprint='a'*64,proposal=plan)


def decide(state,identity=None,decision='accepted',user='alice'):
    return acceptance.record_plan_acceptance_decision(user,decision=decision,
        input_fingerprint=state.input_fingerprint,
        expected_proposal_fingerprint=proposal.proposal_fingerprint(state.proposal),
        proposal_state=state,session_id=identity,
        feedback='Revoir les horaires.' if decision=='revision_requested' else None)


def publish(state,monkeypatch,client,index=0,identity='alpha',user='alice'):
    monkeypatch.setattr(delivery,'get_plan_proposal_state',lambda _:state)
    return delivery.publish_accepted_session(user,client,session_index=index,expected_session_id=identity)


@pytest.mark.parametrize('delta,valid',[(0,True),(-1,False),(1,True)])
def test_recovery_boundary_is_measured_after_end_not_start(delta,valid):
    start=datetime.fromisoformat('2099-10-13T14:30:00+02:00')+timedelta(seconds=delta)
    state=current(second=start.isoformat())
    result=proposal.validate_plan_proposal(state.proposal,source())
    assert result.valid is valid
    assert ('insufficient_recovery' in {e.code for e in result.errors}) is (not valid)


def test_utc_order_and_rest_are_not_inferred_from_displayed_local_hours():
    state=current(first='2099-10-13T08:00:00-10:00',second='2099-10-13T14:30:00+14:00')
    errors={e.code for e in proposal.validate_plan_proposal(state.proposal,source()).errors}
    assert 'session_order_invalid' in errors
    reverse=state.proposal.model_copy(update={'sessions':list(reversed(state.proposal.sessions))})
    assert proposal.validate_plan_proposal(reverse,source()).valid


@pytest.mark.parametrize('start',['2099-10-13T08:00:00','2099-10-14T08:00:00+02:00'])
def test_naive_or_wrong_local_date_is_rejected(start):
    raw=current().proposal.sessions[0].model_dump();raw['starts_at']=start
    with pytest.raises(ValidationError):
        PlanSessionV2.model_validate(raw)


def test_duplicate_identity_is_rejected_even_on_distinct_dates():
    raw=current().proposal.model_dump()
    raw['sessions'][1].update(session_id='alpha',date=date(2099,10,15),starts_at=datetime.fromisoformat('2099-10-15T08:00:00+02:00'))
    with pytest.raises(ValidationError,match='distincts'):
        PlanProposalV2.model_validate(raw)


def test_midnight_overflow_and_daily_cap_use_execution_duration():
    state=current(first='2099-10-13T12:00:00+02:00',second='2099-10-13T23:45:00+02:00')
    assert 'session_crosses_midnight' in {e.code for e in proposal.validate_plan_proposal(state.proposal,source()).errors}
    state=current()
    raw=source().model_dump();raw['plan_constraints']['max_daily_duration_minutes']=59
    assert 'daily_duration_limit' in {e.code for e in proposal.validate_plan_proposal(state.proposal,proposal.PlanGenerationInputV2.model_validate(raw)).errors}


@pytest.mark.parametrize('decision',['refused','revision_requested'])
def test_global_withdrawal_and_later_global_accept_do_not_grant_unit_permissions(monkeypatch,decision):
    state=current();decide(state,'alpha');decide(state,decision=decision);decide(state)
    client=VerifiedClient()
    for index,identity in enumerate(('alpha','beta')):
        with pytest.raises(delivery.GarminDeliveryError):
            publish(state,monkeypatch,client,index,identity)
    assert client.uploads==0 and client.schedules==[]
    decide(state,'alpha')
    first=publish(state,monkeypatch,client)
    assert publish(state,monkeypatch,client)==first
    with pytest.raises(delivery.GarminDeliveryError):
        publish(state,monkeypatch,client,1,'beta')
    assert client.uploads==1 and len(client.schedules)==1


def test_unit_acceptance_follows_identity_when_list_order_changes(monkeypatch):
    state=current();decide(state,'alpha');client=VerifiedClient()
    first=publish(state,monkeypatch,client)
    state.proposal.sessions.reverse()
    with pytest.raises(delivery.GarminDeliveryError) as error:
        publish(state,monkeypatch,client,0,'alpha')
    assert error.value.code=='session_identity_conflict'
    assert publish(state,monkeypatch,client,1,'alpha')['garmin_workout_id']==first['garmin_workout_id']
    assert client.uploads==1 and len(client.schedules)==1


def test_two_acceptances_keep_ids_separate_and_accounts_isolated(monkeypatch):
    state=current();decide(state,'alpha');decide(state,'beta');client=VerifiedClient()
    first=publish(state,monkeypatch,client)
    second=publish(state,monkeypatch,client,1,'beta')
    assert first['garmin_workout_id']!=second['garmin_workout_id']
    with pytest.raises(delivery.GarminDeliveryError):
        publish(state,monkeypatch,client,1,'beta',user='bob')
    assert publish(state,monkeypatch,client,1,'beta')==second
    assert client.uploads==2 and len(client.schedules)==2


@pytest.mark.parametrize('item',[{'itemType':'activity','date':'2099-10-13','activityId':99},
    {'itemType':'workout','date':'2099-10-13','workoutId':99},
    {'itemType':'workout','workoutId':99}])
def test_external_calendar_evidence_blocks_before_upload(monkeypatch,item):
    state=current();decide(state,'alpha');client=VerifiedClient();client.items=[item]
    with pytest.raises(delivery.GarminDeliveryError):
        publish(state,monkeypatch,client)
    assert client.uploads==0 and client.schedules==[]


@pytest.mark.parametrize('items',[[None],['malformed'],[{'itemType':'workout','date':'not-a-date','workoutId':99}]])
def test_malformed_calendar_entries_are_not_proof_of_an_empty_calendar(monkeypatch,items):
    state=current();decide(state,'alpha');client=VerifiedClient();client.items=list(items)
    with pytest.raises(delivery.GarminDeliveryError) as error:
        publish(state,monkeypatch,client)
    assert error.value.code=='calendar_unavailable'
    assert client.uploads==0 and client.schedules==[]


def test_local_future_plan_collision_is_not_mistaken_for_empty_remote_calendar(monkeypatch):
    state=current();decide(state,'alpha');client=VerifiedClient()
    with config.user_context('alice'):
        config.ensure_directories()
        (config.PLANS_DIR/'2099-10-12.json').write_text(json.dumps({'seances':[{'date':'2099-10-13'}]}),encoding='utf-8')
    with pytest.raises(delivery.GarminDeliveryError) as error:
        publish(state,monkeypatch,client)
    assert error.value.code=='calendar_collision' and client.uploads==0


def test_imported_activity_collision_is_distinct_from_legacy_future_plan(monkeypatch):
    from garmin_coach import local_activity_history
    monkeypatch.setattr(local_activity_history,'iter_local_activity_history',lambda **kw:iter([{'startTimeLocal':'2099-10-13T07:00:00'}]))
    state=current();decide(state,'alpha');client=VerifiedClient()
    with pytest.raises(delivery.GarminDeliveryError) as error:
        publish(state,monkeypatch,client)
    assert error.value.code=='calendar_collision' and client.uploads==0


def test_completion_date_fallback_never_double_counts_ambiguous_activities(tmp_path,monkeypatch):
    plans=tmp_path/'plans';plans.mkdir();monkeypatch.setattr(training_cycle,'PLANS_DIR',plans)
    sessions=[{'date':'2099-10-13','garmin':{'workout_id':11}}, {'date':'2099-10-13','garmin':{'workout_id':22}}]
    (plans/'2099-10-12.json').write_text(json.dumps({'seances':sessions}),encoding='utf-8')
    activities=[{'startTimeLocal':'2099-10-13T09:00:00','workoutId':999}]*2
    assert training_cycle.completed_session_indexes(sessions,activities)==set()
    assert training_cycle.session_statuses(sessions,activities)==['a_venir','a_venir']
    assert training_cycle.find_planned_session(activities[0]) is None
    activities.append({'startTimeLocal':'2099-10-13T09:00:00','workoutId':22})
    assert training_cycle.completed_session_indexes(sessions,activities)=={1}
    assert training_cycle.session_statuses(sessions,activities)==['a_venir','terminee']


def test_old_proposal_digest_acceptance_and_delivery_are_preserved(monkeypatch,tmp_path):
    state=proposal_state(running_session())
    old=state.proposal.model_dump(mode='json')
    for item in old['sessions']:
        item.pop('session_id');item.pop('starts_at')
    payload={'schema_version':2,'prompt_version':1,'proposal':old}
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    assert proposal.proposal_fingerprint(PlanProposalV2.model_validate(old))==digest
    user='alice';decide(state,user=user)
    target=config.USERS_DATA_DIR/user/'plan_acceptance_v2.json'
    raw=json.loads(target.read_text(encoding='utf-8'))
    for decision in raw['decisions']:
        decision.pop('session_id',None);decision.pop('session_fingerprint',None)
    target.write_text(json.dumps(raw),encoding='utf-8');before=target.read_bytes()
    assert acceptance.get_plan_acceptance_state(user,proposal_state=state).status=='accepted'
    assert target.read_bytes()==before
    client=VerifiedClient();first=publish(state,monkeypatch,client,identity=None)
    store_path=config.USERS_DATA_DIR/user/'garmin_delivery_v1.json'
    if not store_path.exists():
        store_path=next(path for path in (config.USERS_DATA_DIR/user).glob('*.json')
                        if 'deliveries' in json.loads(path.read_text(encoding='utf-8')))
    stored=json.loads(store_path.read_text(encoding='utf-8'))
    assert list(stored['deliveries'])==[f'{digest}:0']
    for record in stored['deliveries'].values():
        record.pop('session_id',None);record.pop('session_fingerprint',None)
    store_path.write_text(json.dumps(stored),encoding='utf-8')
    before=store_path.read_bytes()
    second=publish(state,monkeypatch,client,identity=None)
    assert first==second and client.uploads==1 and len(client.schedules)==1
    assert store_path.read_bytes()==before
