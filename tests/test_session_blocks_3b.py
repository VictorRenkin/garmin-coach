from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from garmin_coach import config, dashboard, garmin_delivery, plan_proposal
from garmin_coach.coach_chat import SessionPatch
from garmin_coach.garmin_workouts import build_running_workout
from garmin_coach.plan_proposal import PlanSessionV2
from garmin_coach.session_blocks import SessionBlock
from garmin_coach.training_cycle import PlannedSession
from tests.test_garmin_workouts import make_session
from tests.test_garmin_delivery import VerifiedClient, proposal_state
from tests.test_plan_proposal import runtime_for, states, settings


def blocks(recovery=0.75):
    return [dict(effort_minutes=3, recovery_minutes=recovery, repetitions=4,
                 intensity_instruction="Soutenu contrôlé.", effort_hr_zone=3, recovery_hr_zone=1),
            dict(effort_minutes=2, recovery_minutes=0.5, repetitions=2,
                 intensity_instruction="Rester fluide.", effort_hr_zone=4, recovery_hr_zone=1)]


def session(recovery=0.75):
    duration = round(10 + 4 * (3 + recovery) + 2 * 2.5 + 5)
    return PlanSessionV2(date=date(2099, 10, 13), sport="running", session_type="intervalles",
                         objective="Deux séries distinctes.", duration_minutes=duration,
                         intensity_instruction="Respecter les deux blocs.", explanation="Séance synthétique.",
                         structure=dict(warmup_minutes=10, cooldown_minutes=5,
                                        main_set="Deux blocs distincts.", blocks=blocks(recovery)))


def legacy(recovery=0.75):
    return PlannedSession.model_validate({**make_session().model_dump(), "blocks": blocks(recovery),
        "repetitions_bloc":None, "effort_minutes":None, "recuperation_minutes":None,
        "duree_estimee_minutes": session(recovery).duration_minutes})


def effective_seconds(steps):
    return sum(step.numberOfIterations * effective_seconds(step.workoutSteps)
               if hasattr(step, "numberOfIterations") else step.endConditionValue for step in steps)


@pytest.mark.parametrize("recovery", [0.5, 0.75, 1.5])
def test_two_blocks_are_identical_in_both_existing_adapters(recovery):
    v2 = garmin_delivery.build_plan_session_workout(session(recovery))
    old = build_running_workout(legacy(recovery))
    expected = (10 + 4 * (3 + recovery) + 2 * 2.5 + 5) * 60
    for workout in (v2, old):
        steps = workout.workoutSegments[0].workoutSteps
        assert effective_seconds(steps) == expected == workout.estimatedDurationInSecs
        groups = [step for step in steps if hasattr(step, 'numberOfIterations')]
        assert [group.numberOfIterations for group in groups] == [4,2]
        assert [group.workoutSteps[0].zoneNumber for group in groups] == [3,4]
        assert groups[0].workoutSteps[1].endConditionValue == recovery*60
        assert [step.stepOrder for step in steps] == list(range(1,len(steps)+1))


@pytest.mark.parametrize('bad', [dict(effort_minutes=-1), dict(recovery_minutes=-1),
    dict(effort_minutes=float('nan')),dict(recovery_minutes=float('inf')),
    dict(effort_minutes=0.001),dict(effort_minutes=1e-10),dict(recovery_minutes=1e-10),
    dict(blocks=[]),dict(pace_seconds_per_km=240)])
def test_unrepresentable_blocks_are_refused(bad):
    with pytest.raises(ValidationError):
        SessionBlock.model_validate({**blocks()[0], **bad})


def test_complex_total_and_cumulative_repetition_guards():
    raw = session().model_dump()
    raw['duration_minutes'] += 1
    with pytest.raises(ValidationError, match='durée annoncée'):
        PlanSessionV2.model_validate(raw)
    raw = session().model_dump()
    for block in raw['structure']['blocks']:
        block.update(repetitions=51,effort_minutes=1/60,recovery_minutes=0,recovery_hr_zone=None)
    with pytest.raises(ValidationError, match='cumul'):
        PlanSessionV2.model_validate(raw)


def test_patch_switches_representation_explicitly_without_obsolete_fields(tmp_path):
    target=tmp_path/'plan.json'
    target.write_text(json.dumps({'seances':[make_session().model_dump()]}),encoding='utf-8')
    patch = SessionPatch(blocks=blocks(), duree_estimee_minutes=35)
    changed=dashboard.modify_planned_session(target,0,patch.model_dump(exclude_none=True))
    assert changed['blocks'] is not None and changed['repetitions_bloc'] is None
    before=target.read_bytes()
    with pytest.raises(ValueError,match='trois champs'):
        dashboard.modify_planned_session(target,0,{'effort_minutes':3})
    assert target.read_bytes()==before
    changed=dashboard.modify_planned_session(target,0,{'repetitions_bloc':4,'effort_minutes':3,'recuperation_minutes':1})
    assert changed['blocks'] is None and changed['effort_minutes']==3


def test_invalid_conflicting_patch_never_writes(tmp_path):
    target=tmp_path/'plan.json'
    target.write_text(json.dumps({'seances':[make_session().model_dump()]}),encoding='utf-8')
    before=target.read_bytes()
    with pytest.raises(ValueError, match='exclusifs'):
        dashboard.modify_planned_session(target,0,{'blocks':blocks(),'effort_minutes':3})
    assert target.read_bytes()==before


@pytest.mark.parametrize('adjustment',['lighter','progressive','recovery'])
def test_global_adaptation_cannot_silently_corrupt_complex_blocks(tmp_path,adjustment):
    target=tmp_path/'plan.json'
    future=legacy().model_copy(update={'date':'2099-10-13'})
    target.write_text(json.dumps({'seances':[future.model_dump(mode='json')]}),encoding='utf-8')
    before=target.read_bytes()
    with pytest.raises(ValueError,match='bloc par bloc'):
        dashboard.adapt_plan(target,adjustment)
    assert target.read_bytes()==before


def test_single_iteration_recovery_is_executable_in_both_adapters():
    for original, builder in ((session(),garmin_delivery.build_plan_session_workout),
                              (legacy(),build_running_workout)):
        raw=original.model_dump()
        # Keep declared duration at nearest minute to the actual seconds.
        if 'structure' in raw:
            raw['structure']['blocks']=[dict(effort_minutes=3,recovery_minutes=0.75,repetitions=1,
                                           intensity_instruction='Effort contrôlé.')]
            raw['duration_minutes']=19
            model=PlanSessionV2
        else:
            raw['blocks']=[dict(effort_minutes=3,recovery_minutes=0.75,repetitions=1,
                                intensity_instruction='Effort contrôlé.')]
            raw['duree_estimee_minutes']=19
            model=PlannedSession
        workout=builder(model.model_validate(raw))
        assert effective_seconds(workout.workoutSegments[0].workoutSteps)==1125
        assert workout.estimatedDurationInSecs==1125


def test_generation_validation_acceptance_and_local_conversion(monkeypatch,tmp_path):
    monkeypatch.setattr(config,'GLOBAL_DATA_DIR',tmp_path)
    monkeypatch.setattr(config,'USERS_DATA_DIR',tmp_path/'users')
    monkeypatch.setattr(config,'SECRETS_DIR',tmp_path/'secrets')
    user='synthetic-blocks'
    runtime=runtime_for(user)
    recommendation,decision=states(runtime,confirmed=2)
    source=plan_proposal.build_plan_generation_input_v2(user,runtime,
        recommendation_state=recommendation,decision_state=decision)
    first=session().model_dump()
    first['date']=source.allowed_dates[0]
    second={**first,'date':source.allowed_dates[1]}
    proposal=plan_proposal.PlanProposalV2(period=source.period,summary='Une semaine synthétique.',
        strategy='Deux séances distinctes.',sessions=[first,second])
    monkeypatch.setattr(plan_proposal.ai_provider,'structured_completion',lambda *a,**kw: proposal)
    generated=plan_proposal.generate_plan_proposal(user,runtime=runtime,
        recommendation_state=recommendation,decision_state=decision,settings=settings())
    assert generated.status=='available'
    from garmin_coach import plan_acceptance
    monkeypatch.setattr(garmin_delivery,'get_plan_proposal_state',lambda user:generated)
    client=VerifiedClient()
    with pytest.raises(garmin_delivery.GarminDeliveryError,match='Valide'):
        garmin_delivery.publish_accepted_session(user,client,session_index=0)
    plan_acceptance.record_plan_acceptance_decision(user,decision='accepted',
        input_fingerprint=generated.input_fingerprint,
        expected_proposal_fingerprint=plan_proposal.proposal_fingerprint(proposal),proposal_state=generated)
    first=garmin_delivery.publish_accepted_session(user,client,session_index=0,scheduled_date=date(2099,10,13))
    assert garmin_delivery.publish_accepted_session(user,client,session_index=0,scheduled_date=date(2099,10,13))==first
    assert client.uploads==1 and len(client.schedules)==1


def test_real_ai_validation_harness_runs_five_cases_offline(monkeypatch,tmp_path):
    """Validate the paid-call harness itself with an entirely fake transport."""
    import importlib.util
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from garmin_coach import coach_chat
    spec=importlib.util.spec_from_file_location('validate_complex_sessions_3b',
        Path(__file__).resolve().parents[1]/'scripts'/'validate_complex_sessions_3b.py')
    harness=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)

    class Responses:
        def parse(self,**kwargs):
            name=config.current_user_id().removeprefix('synthetic-')
            expected=harness.CASES[name]
            if name=='v2_two_blocks':
                payload=json.loads(kwargs['input'])
                generated=session().model_dump()
                generated.update(date=payload['allowed_dates'][0],duration_minutes=29)
                generated['structure']['blocks']=expected
                output=plan_proposal.PlanProposalV2(period=payload['period'],summary='Semaine synthétique.',
                    strategy='Deux blocs distincts.',sessions=[generated])
            elif expected is None:
                output=coach_chat.CoachResponse(message='Les blocs imbriqués ne sont pas représentables.',action='none')
            else:
                _,seconds=harness.requested_session(expected)
                output=coach_chat.CoachResponse(message='Voici les blocs proposés.',action='modify_session',session_index=0,
                    session_patch=SessionPatch(blocks=expected,duree_estimee_minutes=round(seconds/60),
                                               echauffement_minutes=10,retour_au_calme_minutes=5))
            return SimpleNamespace(output_parsed=output,usage=SimpleNamespace(input_tokens=100,output_tokens=50,total_tokens=150))

    class Client:
        def __init__(self,**kwargs):
            self.responses=Responses()

    report=tmp_path/'validation.json'
    monkeypatch.setattr(coach_chat,'OpenAI',Client)
    monkeypatch.setattr(config,'load_settings',settings)
    monkeypatch.setattr(sys,'argv',['validate_complex_sessions_3b.py','--real','--output',str(report)])
    harness.main()
    result=json.loads(report.read_text(encoding='utf-8'))
    assert result['api_attempts']==5
    assert all(item['technical_success'] and item['functional_success'] for item in result['profiles'])
    assert result['total_tokens']==750 and result['real_garmin_calls']==0
