"""End-to-end representation limits; all Garmin clients are existing simulators."""
import json
from datetime import date

import pytest
from pydantic import ValidationError

from garmin_coach import config, dashboard, garmin_delivery, plan_acceptance
from garmin_coach.coach_chat import SessionPatch
from garmin_coach.garmin_workouts import build_running_workout
from garmin_coach.plan_proposal import PlanSessionStructureV2, PlanSessionV2, PlanProposalV2, PlanPeriodV2, PlanProposalStateV2, proposal_fingerprint
from garmin_coach.training_cycle import InitialPlan, PlannedSession
from tests.test_garmin_delivery import VerifiedClient
from tests.test_garmin_workouts import make_session


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'GLOBAL_DATA_DIR', tmp_path)
    monkeypatch.setattr(config, 'USERS_DATA_DIR', tmp_path/'users')
    monkeypatch.setattr(config, 'SECRETS_DIR', tmp_path/'secrets')


def v2(legacy):
    return PlanSessionV2(date=legacy.date, sport='running', session_type='intervals',
        objective=legacy.objectif, duration_minutes=legacy.duree_estimee_minutes,
        distance_km=legacy.distance_estimee_km, intensity_instruction=legacy.consigne_intensite,
        structure=PlanSessionStructureV2(warmup_minutes=legacy.echauffement_minutes,
            main_set=legacy.bloc_principal, repetitions=legacy.repetitions_bloc,
            effort_minutes=legacy.effort_minutes, recovery_minutes=legacy.recuperation_minutes,
            cooldown_minutes=legacy.retour_au_calme_minutes), explanation=legacy.raison)


@pytest.mark.parametrize('seconds', [30, 45, 90])
def test_requested_recovery_survives_patch_storage_and_both_adapters(seconds, tmp_path):
    legacy = make_session(date='2099-10-13')
    target = tmp_path/'plan.json'
    target.write_text(json.dumps({'seances': [legacy.model_dump(mode='json')]}))
    minutes = seconds/60
    duration = int(10 + 4*(3+minutes) + 5)
    patch = SessionPatch(recuperation_minutes=minutes, duree_estimee_minutes=duration)
    changed = dashboard.modify_planned_session(target, 0, patch.model_dump(exclude_none=True))
    assert changed['recuperation_minutes'] == minutes
    assert json.loads(target.read_text(encoding='utf-8'))['seances'][0] == changed
    session = PlannedSession.model_validate(changed)
    for workout in (build_running_workout(session), garmin_delivery.build_plan_session_workout(v2(session))):
        repeated = workout.workoutSegments[0].workoutSteps[1]
        assert repeated.numberOfIterations == 4
        assert repeated.workoutSteps[1].endConditionValue == seconds
        assert workout.estimatedDurationInSecs == 600 + 4*(180+seconds) + 300
    assert 'garmin' not in changed


def test_existing_simple_session_export_is_unchanged():
    workout = build_running_workout(make_session())
    assert workout.workoutName == 'GC 2807 INT'
    assert workout.estimatedDurationInSecs == 2100
    assert workout.workoutSegments[0].workoutSteps[1].numberOfIterations == 4
    assert workout.workoutSegments[0].workoutSteps[1].workoutSteps[1].endConditionValue == 120


@pytest.mark.parametrize('duration,distance,repetitions,effort,recovery', [
    (150, 25, 1, 135, 0), (180, 30, 1, 165, 0), (75, 15, 15, 3, 1),
    (215, 25, 100, 1, 1),
])
def test_long_or_repeated_session_has_same_structure_on_both_paths(duration, distance, repetitions, effort, recovery):
    patch = SessionPatch(duree_estimee_minutes=duration, distance_estimee_km=distance,
        repetitions_bloc=repetitions, effort_minutes=effort, recuperation_minutes=recovery)
    legacy = make_session(**patch.model_dump(exclude_none=True))
    for workout in (build_running_workout(legacy), garmin_delivery.build_plan_session_workout(v2(legacy))):
        assert workout.estimatedDurationInSecs == duration*60
        main = workout.workoutSegments[0].workoutSteps[1]
        if repetitions > 1:
            assert main.numberOfIterations == repetitions
            assert main.workoutSteps[0].endConditionValue == effort*60
        else:
            assert main.endConditionValue == effort*60


@pytest.mark.parametrize('field,value', [
    ('recuperation_minutes', -1), ('recuperation_minutes', 16), ('recuperation_minutes', None),
    ('recuperation_minutes', float('nan')), ('recuperation_minutes', float('inf')),
    ('repetitions_bloc', 0), ('repetitions_bloc', 101), ('repetitions_bloc', None),
    ('duree_estimee_minutes', 0), ('duree_estimee_minutes', 481), ('duree_estimee_minutes', None),
    ('distance_estimee_km', 0), ('distance_estimee_km', 501), ('distance_estimee_km', None),
    ('effort_minutes', 0), ('effort_minutes', 361), ('effort_minutes', None),
])
def test_invalid_values_still_rejected(field, value):
    with pytest.raises(ValidationError):
        make_session(**{field:value})
    if value is not None:
        with pytest.raises(ValidationError):
            SessionPatch(**{field:value})
    else:
        assert SessionPatch(**{field:None}).model_dump(exclude_none=True) == {}


def test_incomplete_typed_blocks_are_rejected_without_writing(tmp_path):
    with pytest.raises(ValidationError):
        PlanSessionStructureV2(main_set='Deux blocs distincts.', blocks=[{'repetitions': 4}, {'repetitions': 3}])
    target = tmp_path/'plan.json'
    original = json.dumps({'seances': [make_session().model_dump(mode='json')]})
    target.write_text(original)
    with pytest.raises(ValidationError, match='effort_minutes'):
        dashboard.modify_planned_session(target, 0, {'blocks': [{'repetitions':4}, {'repetitions':3}]})
    assert target.read_text(encoding='utf-8') == original


def state(sport='running'):
    session = v2(make_session(date='2099-10-13', recuperation_minutes=0.75, duree_estimee_minutes=30))
    session = session.model_copy(update={'sport':sport})
    proposal = PlanProposalV2(period=PlanPeriodV2(start=date(2099,10,12),end=date(2099,10,18)),
        summary='Proposition synthétique.', strategy='Une séance sur la période.', sessions=[session])
    return PlanProposalStateV2(status='available', input_fingerprint='a'*64, proposal=proposal)


def decide(user, current):
    return plan_acceptance.record_plan_acceptance_decision(user, decision='accepted',
        input_fingerprint=current.input_fingerprint, expected_proposal_fingerprint=proposal_fingerprint(current.proposal),
        proposal_state=current)


def test_acceptance_repeat_and_account_isolation_before_simulated_delivery(monkeypatch):
    current = state()
    monkeypatch.setattr(garmin_delivery, 'get_plan_proposal_state', lambda user: current)
    client = VerifiedClient()
    with pytest.raises(garmin_delivery.GarminDeliveryError, match='Valide'):
        garmin_delivery.publish_accepted_session('alice', client, session_index=0)
    assert client.uploads == 0 and client.schedules == []
    assert not decide('alice', current).idempotent
    assert decide('alice', current).idempotent
    with pytest.raises(garmin_delivery.GarminDeliveryError):
        garmin_delivery.publish_accepted_session('bob', client, session_index=0)
    first = garmin_delivery.publish_accepted_session('alice', client, session_index=0)
    second = garmin_delivery.publish_accepted_session('alice', client, session_index=0)
    assert second == first
    assert client.uploads == 1 and client.schedules == [(701, '2099-10-13')]


def test_non_running_cannot_bypass_guard_with_fractional_recovery(monkeypatch):
    current = state('cycling')
    monkeypatch.setattr(garmin_delivery, 'get_plan_proposal_state', lambda user: current)
    decide('alice', current)
    client = VerifiedClient()
    with pytest.raises(garmin_delivery.GarminDeliveryError) as error:
        garmin_delivery.publish_accepted_session('alice', client, session_index=0)
    assert error.value.code == 'sport_not_supported'
    assert client.uploads == 0 and client.schedules == []


def test_weekly_limit_stays_at_seven():
    sessions = [make_session()] * 8
    with pytest.raises(ValidationError):
        InitialPlan(synthese_historique='Une histoire entièrement synthétique.', logique_existante='Logique de test existante.',
            adaptation_objectif='Une adaptation entièrement synthétique.', seances=sessions,
            avertissement_securite='Un avertissement synthétique.')
    with pytest.raises(ValidationError):
        current = state().proposal.model_dump(mode='json')
        current['sessions'] *= 8
        PlanProposalV2.model_validate(current)


def test_coach_contract_explains_existing_zero_based_session_index():
    from garmin_coach.coach_chat import CoachResponse, COACH_CHAT_INSTRUCTIONS
    assert 'base zéro' in CoachResponse.model_json_schema()['properties']['session_index']['description']
    assert 'séance 1 affichée correspond à l’indice 0' in COACH_CHAT_INSTRUCTIONS


def test_repetition_product_cannot_bypass_total_duration_ceiling():
    with pytest.raises(ValidationError, match='plafond'):
        make_session(repetitions_bloc=100, effort_minutes=360, recuperation_minutes=15,
                     duree_estimee_minutes=480)
