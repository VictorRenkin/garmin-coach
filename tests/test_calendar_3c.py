from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from garmin_coach import config, plan_proposal as module, plan_acceptance, garmin_delivery, training_cycle
from garmin_coach.availability import AvailabilityV1, build_availability_v1
from garmin_coach.plan_proposal import PlanSessionV2, PlanProposalV2, PlanProposalStateV2, proposal_fingerprint
from tests.test_plan_proposal import runtime_for, states, settings
from tests.test_garmin_delivery import VerifiedClient


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'GLOBAL_DATA_DIR', tmp_path)
    monkeypatch.setattr(config, 'USERS_DATA_DIR', tmp_path/'users')
    monkeypatch.setattr(config, 'SECRETS_DIR', tmp_path/'secrets')


def source_for(user='calendar'):
    runtime=runtime_for(user)
    recommendation,decision=states(runtime,confirmed=2)
    source=module.build_plan_generation_input_v2(user,runtime,recommendation_state=recommendation,
        decision_state=decision,period_start=date(2099,10,12))
    raw=source.model_dump(mode='json')
    raw['plan_constraints'].update(double_session_days=['tue'],minimum_recovery_hours=6,max_daily_duration_minutes=90)
    raw['confidence']['recovery']='high'
    raw['training_facts']['pain_affecting_training']=False
    return module.PlanGenerationInputV2.model_validate(raw)


def double_proposal(source=None):
    source=source or source_for()
    def session(identity,hour):
        return PlanSessionV2(session_id=identity, starts_at=datetime.fromisoformat(f'2099-10-13T{hour}:00:00+02:00'),
            date=date(2099,10,13),sport='running',session_type='easy_run',objective='Course synthétique facile.',
            duration_minutes=30,intensity_instruction='Rester à une intensité facile.',explanation='Répartition synthétique.',
            structure=dict(warmup_minutes=5,main_set='Vingt minutes faciles.',effort_minutes=20,cooldown_minutes=5))
    return PlanProposalV2(period=source.period,summary='Deux séances distinctes.',strategy='Matin puis fin de journée.',
        sessions=[session('morning','08'),session('evening','16')])


def state(proposal=None):
    return PlanProposalStateV2(status='available',input_fingerprint='a'*64,proposal=proposal or double_proposal())


def accept(user,current,identity):
    return plan_acceptance.record_plan_acceptance_decision(user,decision='accepted',input_fingerprint=current.input_fingerprint,
        expected_proposal_fingerprint=proposal_fingerprint(current.proposal),proposal_state=current,session_id=identity)


def test_explicit_double_day_keeps_seven_weekly_ceiling_and_available_days():
    availability=build_availability_v1(['tue'],preferred_sessions_per_week=2,double_session_days=['tue'],
        minimum_recovery_hours=6,max_daily_duration_minutes=90)
    assert availability.weekly_capacity==2
    assert build_availability_v1(['mon','tue','wed','thu','fri','sat','sun'],double_session_days=['tue'],
        minimum_recovery_hours=6,max_daily_duration_minutes=90).weekly_capacity==7
    with pytest.raises(ValidationError):
        build_availability_v1(['tue'],preferred_sessions_per_week=2)
    with pytest.raises(ValidationError):
        build_availability_v1(['tue'],double_session_days=['thu'],minimum_recovery_hours=6,max_daily_duration_minutes=90)


@pytest.mark.parametrize('value',[True,float('nan'),float('inf'),-1,0])
def test_recovery_bounds_must_be_explicit_positive_finite_numbers(value):
    with pytest.raises(ValidationError):
        AvailabilityV1(available_days=['tue'],double_session_days=['tue'],minimum_recovery_hours=value,max_daily_duration_minutes=90)


def test_authorized_double_day_has_distinct_identity_order_and_exact_recovery():
    source=source_for()
    proposal=double_proposal(source)
    assert module.validate_plan_proposal(proposal,source).valid
    assert proposal.sessions[0].session_id!=proposal.sessions[1].session_id
    raw=proposal.model_dump()
    raw['sessions'].reverse()
    assert 'session_order_invalid' in {error.code for error in module.validate_plan_proposal(PlanProposalV2.model_validate(raw),source).errors}


@pytest.mark.parametrize('change,code',[
    ('permission','duplicate_session_date'),('beginner','double_day_inappropriate'),
    ('comeback','double_day_inappropriate'),('pain','double_day_safety_uncertain'),
    ('uncertain','double_day_safety_uncertain'),('unavailable','unavailable_day'),
    ('existing','existing_calendar_conflict'),('duration','daily_duration_limit'),('rest','insufficient_recovery')])
def test_unsafe_or_unauthorized_double_day_is_refused(change,code):
    source=source_for();proposal=double_proposal(source)
    raw=source.model_dump()
    if change=='permission':raw['plan_constraints']['double_session_days']=[]
    elif change in {'beginner','comeback'}:raw['goal']['intent']=change
    elif change=='pain':raw['training_facts']['pain_affecting_training']=True
    elif change=='uncertain':raw['confidence']['recovery']='none'
    elif change=='unavailable':raw['allowed_dates']=[date(2099,10,12)]
    elif change=='existing':raw['blocked_dates']=[date(2099,10,13)]
    elif change=='duration':raw['plan_constraints']['max_daily_duration_minutes']=59
    elif change=='rest':raw['plan_constraints']['minimum_recovery_hours']=8
    result=module.validate_plan_proposal(proposal,module.PlanGenerationInputV2.model_validate(raw))
    assert not result.valid and code in {error.code for error in result.errors}


@pytest.mark.parametrize('update',[{'session_id':None},{'starts_at':None}])
def test_double_day_requires_identities_and_aware_times(update):
    source=source_for();raw=double_proposal(source).model_dump()
    for item in raw['sessions']:item.update(update)
    try:
        proposal=PlanProposalV2.model_validate(raw)
    except ValidationError:
        return
    assert not module.validate_plan_proposal(proposal,source).valid


def test_accepting_one_session_does_not_accept_the_second_or_duplicate_delivery(monkeypatch):
    current=state()
    monkeypatch.setattr(garmin_delivery,'get_plan_proposal_state',lambda user: current)
    client=VerifiedClient()
    accept('alice',current,'morning')
    first=garmin_delivery.publish_accepted_session('alice',client,session_index=0,expected_session_id='morning')
    assert garmin_delivery.publish_accepted_session('alice',client,session_index=0,expected_session_id='morning')==first
    with pytest.raises(garmin_delivery.GarminDeliveryError,match='Valide explicitement'):
        garmin_delivery.publish_accepted_session('alice',client,session_index=1)
    with pytest.raises(garmin_delivery.GarminDeliveryError):
        garmin_delivery.publish_accepted_session('bob',client,session_index=0)
    assert client.uploads==1 and len(client.schedules)==1
    accept('alice',current,'evening')
    second=garmin_delivery.publish_accepted_session('alice',client,session_index=1,expected_session_id='evening')
    assert first['garmin_workout_id']!=second['garmin_workout_id']
    assert client.uploads==2 and len(client.schedules)==2


@pytest.mark.parametrize('payload',[None,{},'unknown',{'calendarItems':None},
    {'calendarItems':[{'itemType':'activity','date':'2099-10-13','id':99}]},
    {'calendarItems':[{'itemType':'workout','date':'2099-10-13','workoutId':99}]}])
def test_calendar_unknown_or_external_collision_never_uploads(monkeypatch,payload):
    current=state();accept('alice',current,'morning')
    monkeypatch.setattr(garmin_delivery,'get_plan_proposal_state',lambda user:current)
    client=VerifiedClient()
    client.get_scheduled_workouts=lambda year,month:payload
    with pytest.raises(garmin_delivery.GarminDeliveryError):
        garmin_delivery.publish_accepted_session('alice',client,session_index=0)
    assert client.uploads==0 and client.schedules==[]


def test_date_alone_never_completes_or_matches_both_sessions(monkeypatch,tmp_path):
    plans=tmp_path/'plans';plans.mkdir()
    monkeypatch.setattr(training_cycle,'PLANS_DIR',plans)
    sessions=[{'date':'2099-10-13','garmin':{'workout_id':1}}, {'date':'2099-10-13','garmin':{'workout_id':2}}]
    (plans/'2099-10-12.json').write_text(json.dumps({'seances':sessions}),encoding='utf-8')
    activity={'startTimeLocal':'2099-10-13T09:00:00'}
    assert training_cycle.completed_session_indexes(sessions,[activity])==set()
    assert training_cycle.find_planned_session(activity) is None
    activity['workoutId']=2
    assert training_cycle.completed_session_indexes(sessions,[activity])=={1}
    assert training_cycle.find_planned_session(activity)==sessions[1]
    with pytest.raises(ValueError,match='legacy'):
        training_cycle._session_offsets(2,['tue'])


def test_revision_keeps_second_identity_content_and_acceptance(monkeypatch):
    user='revision';source=source_for(user);proposal=double_proposal(source)
    monkeypatch.setattr(module.ai_provider,'structured_completion',lambda *a,**kw:proposal)
    with monkeypatch.context() as scoped:
        scoped.setattr(module,'build_plan_generation_input_v2',lambda *a,**kw:source)
        generated=module.generate_plan_proposal(user,settings=settings())
    accept(user,generated,'morning');accept(user,generated,'evening')
    before=generated.proposal.sessions[1].model_dump(mode='json')
    revised=module.revise_plan_session(user,session_id='morning',expected_proposal_fingerprint=proposal_fingerprint(proposal),
        patch={'objective':'Une intention différente pour le matin.'},source=source)
    assert revised.proposal.sessions[1].model_dump(mode='json')==before
    assert plan_acceptance.get_plan_acceptance_state(user,proposal_state=revised,session_id='morning').status=='stale'
    assert plan_acceptance.get_plan_acceptance_state(user,proposal_state=revised,session_id='evening').status=='accepted'


@pytest.mark.parametrize('decision',['refused','revision_requested'])
def test_later_global_withdrawal_revokes_unit_acceptance_until_explicit_unit_reacceptance(monkeypatch,decision):
    current=state();user='withdrawn';accept(user,current,'morning')
    monkeypatch.setattr(garmin_delivery,'get_plan_proposal_state',lambda user:current)
    plan_acceptance.record_plan_acceptance_decision(user,decision=decision,input_fingerprint=current.input_fingerprint,
        expected_proposal_fingerprint=proposal_fingerprint(current.proposal),proposal_state=current,
        feedback='Revoir ce plan.' if decision=='revision_requested' else None)
    plan_acceptance.record_plan_acceptance_decision(user,decision='accepted',input_fingerprint=current.input_fingerprint,
        expected_proposal_fingerprint=proposal_fingerprint(current.proposal),proposal_state=current)
    client=VerifiedClient()
    with pytest.raises(garmin_delivery.GarminDeliveryError):
        garmin_delivery.publish_accepted_session(user,client,session_index=0)
    assert client.uploads==0
    assert not accept(user,current,'morning').idempotent
    garmin_delivery.publish_accepted_session(user,client,session_index=0)
    with pytest.raises(garmin_delivery.GarminDeliveryError):
        garmin_delivery.publish_accepted_session(user,client,session_index=1)
    assert client.uploads==1


def test_calendar_ai_harness_is_bounded_and_validated_offline(monkeypatch,tmp_path):
    import importlib.util
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from garmin_coach import coach_chat
    spec=importlib.util.spec_from_file_location('calendar_harness',Path(__file__).resolve().parents[1]/'scripts'/'validate_calendar_3c.py')
    harness=importlib.util.module_from_spec(spec);spec.loader.exec_module(harness)

    class Responses:
        def parse(self,**kwargs):
            name=config.current_user_id().removeprefix('synthetic-')
            if name=='v2_generation':
                payload=json.loads(kwargs['input'])
                output=double_proposal()
                output.period=module.PlanPeriodV2.model_validate(payload['period'])
            else:
                output=coach_chat.CoachResponse(message='Réponse synthétique bornée.',action='none',
                    calendar_request=coach_chat.CalendarRequest(decision=harness.CASES[name][0],
                        target_date=None if name=='ambiguous' else date(2099,10,13)))
            return SimpleNamespace(output_parsed=output,usage=SimpleNamespace(input_tokens=100,output_tokens=50,total_tokens=150))

    class Client:
        def __init__(self,**kwargs):
            assert kwargs['max_retries']==0
            self.responses=Responses()

    report=tmp_path/'real-harness.json'
    monkeypatch.setattr(coach_chat,'OpenAI',Client)
    monkeypatch.setattr(config,'load_settings',settings)
    monkeypatch.setattr(sys,'argv',['validate_calendar_3c.py','--real','--output',str(report)])
    harness.main()
    result=json.loads(report.read_text(encoding='utf-8'))
    assert result['api_attempts']==5 and result['total_tokens']==750 and result['real_garmin_calls']==0
    assert all(item['technical_success'] and item['functional_success'] for item in result['profiles'])
