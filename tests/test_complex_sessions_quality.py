"""Independent adversarial checks; only synthetic accounts and local SDK DTOs."""
import json
from datetime import date

import pytest
from pydantic import ValidationError

from garmin_coach import config, dashboard, garmin_delivery, plan_acceptance
from garmin_coach.coach_chat import SessionPatch
from garmin_coach.garmin_workouts import build_running_workout
from garmin_coach.plan_proposal import PlanSessionV2, proposal_fingerprint
from garmin_coach.session_blocks import SessionBlock
from garmin_coach.training_cycle import PlannedSession
from tests.test_garmin_delivery import VerifiedClient, proposal_state
from tests.test_garmin_workouts import make_session


def block(**changes):
    return {**dict(effort_minutes=2, recovery_minutes=.75, repetitions=2,
                   intensity_instruction='Soutenu contrôlé.'), **changes}


def raw_session(blocks=None):
    blocks = blocks if blocks is not None else [block(), dict(effort_minutes=3, recovery_minutes=.5,
        repetitions=1, intensity_instruction='Facile.', effort_hr_zone=2, recovery_hr_zone=1)]
    seconds = 900 + sum(round(b['effort_minutes']*60+b.get('recovery_minutes',0)*60)*b.get('repetitions',1) for b in blocks)
    return dict(date=date(2099,10,13), sport='running', session_type='intervalles',
        objective='Une séance synthétique contrôlée.', duration_minutes=round(seconds/60),
        intensity_instruction='Suivre les blocs.', explanation='Deux blocs synthétiques.',
        structure=dict(warmup_minutes=10, cooldown_minutes=5,
                       main_set='Ancien texte descriptif sans autorité numérique.', blocks=blocks))


def legacy(raw):
    return PlannedSession.model_validate({**make_session().model_dump(),
        'date': raw['date'].isoformat(), 'blocks':raw['structure']['blocks'],
        'duree_estimee_minutes':raw['duration_minutes'],
        'repetitions_bloc':None, 'effort_minutes':None, 'recuperation_minutes':None})


def duration(steps):
    return sum(s.numberOfIterations*duration(s.workoutSteps) if hasattr(s,'numberOfIterations')
               else s.endConditionValue for s in steps)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config,'GLOBAL_DATA_DIR',tmp_path)
    monkeypatch.setattr(config,'USERS_DATA_DIR',tmp_path/'users')
    monkeypatch.setattr(config,'SECRETS_DIR',tmp_path/'secrets')


@pytest.mark.parametrize('recovery',[.5,.75,1.5])
def test_distinct_blocks_retain_seconds_order_and_real_executable_total(recovery):
    first=block(); first['recovery_minutes']=recovery
    raw=raw_session([first,dict(effort_minutes=3,recovery_minutes=.75,repetitions=1,
        intensity_instruction='Facile.',effort_hr_zone=2,recovery_hr_zone=1)])
    expected=900+2*(120+recovery*60)+180+45
    for dto in (garmin_delivery.build_plan_session_workout(PlanSessionV2.model_validate(raw)),
                build_running_workout(legacy(raw))):
        steps=dto.workoutSegments[0].workoutSteps
        assert duration(steps)==expected==dto.estimatedDurationInSecs
        assert [s.stepOrder for s in steps]==list(range(1,len(steps)+1))
        assert steps[1].workoutSteps[1].endConditionValue==recovery*60
        assert steps[2].endConditionValue==180 and steps[2].zoneNumber==2
        assert steps[3].endConditionValue==45 and steps[3].zoneNumber==1
        assert getattr(steps[1].workoutSteps[0],'zoneNumber',None) is None
        assert steps[1].workoutSteps[0].targetType['workoutTargetTypeKey']=='no.target'


@pytest.mark.parametrize('change',[
    {'effort_minutes':True},{'repetitions':True},{'recovery_hr_zone':True},
    {'effort_minutes':-1},{'effort_minutes':float('nan')},{'recovery_minutes':float('inf')},
    {'effort_minutes':.001},{'repetitions':101},{'repetitions':0},
    {'effort_hr_zone':6},{'recovery_minutes':0,'recovery_hr_zone':1},
    {'pace_seconds_per_km':240},{'blocks':[]},{'intensity_instruction':' '},
])
def test_bad_or_unrepresentable_block_is_rejected(change):
    with pytest.raises(ValidationError):
        SessionBlock.model_validate({**block(),**change})


def test_cumulative_repetitions_and_total_duration_cannot_bypass_limits():
    blocks=[dict(effort_minutes=1/60,repetitions=50,recovery_minutes=0,intensity_instruction='Facile.')]*2
    assert len(PlanSessionV2.model_validate(raw_session(blocks)).structure.blocks)==2
    blocks[1]={**blocks[1],'repetitions':51}
    with pytest.raises(ValidationError,match='cumul'):
        PlanSessionV2.model_validate(raw_session(blocks))
    with pytest.raises(ValidationError):
        PlanSessionV2.model_validate(raw_session([dict(effort_minutes=240,repetitions=2,
            recovery_minutes=0,intensity_instruction='Facile.')]))


@pytest.mark.parametrize('blocks',[
    [dict(effort_minutes=232.5,repetitions=2,recovery_minutes=0,intensity_instruction='Facile.')],
    [dict(effort_minutes=1/60,repetitions=1,recovery_minutes=0,intensity_instruction='Facile.')]*100,
])
def test_exact_existing_ceilings_are_serialized_without_silent_truncation(blocks):
    raw=raw_session(blocks)
    expected=900+sum(round(b['effort_minutes']*60)*b['repetitions'] for b in blocks)
    for dto in (garmin_delivery.build_plan_session_workout(PlanSessionV2.model_validate(raw)),
                build_running_workout(legacy(raw))):
        assert duration(dto.workoutSegments[0].workoutSteps)==expected==dto.estimatedDurationInSecs
    assert len(PlanSessionV2.model_validate(raw).structure.blocks)==len(blocks)


@pytest.mark.parametrize('update',[{'blocks':[]},{'blocks':[block()]*101},
    {'repetitions':2},{'effort_minutes':3},{'recovery_minutes':.5}])
def test_empty_excessive_or_conflicting_structure_is_rejected(update):
    raw=raw_session(); raw['structure'].update(update)
    with pytest.raises(ValidationError):
        PlanSessionV2.model_validate(raw)


def test_unvalidated_legacy_copy_is_rejected_before_sdk_conversion():
    session=legacy(raw_session())
    corrupt=session.blocks[0].model_copy(update={'effort_minutes':-2})
    copied=session.model_copy(update={'blocks':[corrupt,*session.blocks[1:]]})
    with pytest.raises(ValidationError):
        build_running_workout(copied)


@pytest.mark.parametrize('difference',[1,-1])
def test_announced_duration_mismatch_rejected_on_both_contracts(difference):
    raw=raw_session(); raw['duration_minutes']+=difference
    with pytest.raises(ValidationError,match='durée annoncée'):
        PlanSessionV2.model_validate(raw)
    with pytest.raises(ValidationError,match='durée annoncée'):
        legacy(raw)


def test_sdk_boundary_uses_revalidated_structure_not_old_model_copy():
    original=PlanSessionV2.model_validate(raw_session())
    raw=raw_session()['structure']['blocks']
    raw[0]['effort_minutes']='2'
    copied=original.model_copy(update={'structure':original.structure.model_copy(update={'blocks':raw})})
    with pytest.warns(UserWarning,match='Pydantic serializer warnings'):
        dto=garmin_delivery.build_plan_session_workout(copied)
    assert duration(dto.workoutSegments[0].workoutSteps)==1440
    raw[0]['effort_minutes']=-2
    with pytest.warns(UserWarning,match='Pydantic serializer warnings'):
        with pytest.raises(ValidationError):
            garmin_delivery.build_plan_session_workout(copied)


def test_patch_roundtrip_keeps_date_ids_and_refuses_partial_or_conflicting_change(tmp_path):
    path=tmp_path/'plan.json'
    original=make_session().model_dump(mode='json'); original['garmin']={'workout_id':432}
    path.write_text(json.dumps({'seances':[original]}),encoding='utf-8')
    raw=raw_session()
    patch=SessionPatch(blocks=raw['structure']['blocks'],duree_estimee_minutes=raw['duration_minutes'])
    saved=dashboard.modify_planned_session(path,0,patch.model_dump(exclude_none=True))
    assert saved['date']==original['date'] and saved['garmin']=={'workout_id':432}
    assert all(saved[k] is None for k in ('effort_minutes','repetitions_bloc','recuperation_minutes'))
    before=path.read_bytes()
    for invalid in ({'effort_minutes':3},{'blocks':raw['structure']['blocks'],'repetitions_bloc':1},
                    {'blocks':[]},{'blocks':None},{'date':'2099-10-14'}):
        with pytest.raises(ValueError):
            dashboard.modify_planned_session(path,0,invalid)
        assert path.read_bytes()==before
    result=dashboard.modify_planned_session(path,0,{'effort_minutes':3,'repetitions_bloc':4,
        'recuperation_minutes':1,'duree_estimee_minutes':31})
    assert result['blocks'] is None
    assert build_running_workout(PlannedSession.model_validate(result)).estimatedDurationInSecs==1860


def test_real_acceptance_isolated_deduplicated_and_invalidated_by_changed_block(monkeypatch):
    current=proposal_state(PlanSessionV2.model_validate(raw_session()))
    monkeypatch.setattr(garmin_delivery,'get_plan_proposal_state',lambda user:current)
    client=VerifiedClient()
    def publish(user):
        return garmin_delivery.publish_accepted_session(user,client,session_index=0)
    with pytest.raises(garmin_delivery.GarminDeliveryError,match='Valide'):
        publish('alice')
    plan_acceptance.record_plan_acceptance_decision('alice',decision='accepted',
        input_fingerprint=current.input_fingerprint,
        expected_proposal_fingerprint=proposal_fingerprint(current.proposal),proposal_state=current)
    with pytest.raises(garmin_delivery.GarminDeliveryError):
        publish('bob')
    first=publish('alice'); assert publish('alice')==first
    assert client.uploads==1 and len(client.schedules)==1
    current.proposal.sessions[0].structure.blocks[0].intensity_instruction='Nouvelle consigne.'
    with pytest.raises(garmin_delivery.GarminDeliveryError,match='Valide'):
        publish('alice')
    assert client.uploads==1 and len(client.schedules)==1


@pytest.mark.parametrize('sport',['cycling','swimming','other'])
def test_complex_structure_does_not_enable_non_running_publication(sport,monkeypatch):
    raw=raw_session(); raw['sport']=sport
    with pytest.raises(ValidationError,match='réservés à la course'):
        PlanSessionV2.model_validate(raw)
    # Even an unvalidated copied object cannot bypass the publication guard.
    copied=PlanSessionV2.model_validate(raw_session()).model_copy(update={'sport':sport})
    current=proposal_state(PlanSessionV2.model_validate(raw_session()))
    current.proposal.sessions[0]=copied
    monkeypatch.setattr(garmin_delivery,'_accepted_proposal',lambda user:(current,'a'*64))
    client=VerifiedClient()
    with pytest.raises(garmin_delivery.GarminDeliveryError) as error:
        garmin_delivery.publish_accepted_session('alice',client,session_index=0)
    assert error.value.code=='sport_not_supported'
    assert client.uploads==0 and client.schedules==[]
