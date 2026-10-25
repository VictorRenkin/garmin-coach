"""Independent safety boundaries for V2 local session actions, synthetic only."""
import pytest
from pydantic import ValidationError
from garmin_coach import config, plan_proposal
from tests.test_calendar_3c import source_for, double_proposal


@pytest.fixture(autouse=True)
def isolated_quality(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'ROOT_DIR',tmp_path)
    monkeypatch.setattr(config,'GLOBAL_DATA_DIR',tmp_path/'data')
    monkeypatch.setattr(config,'USERS_DATA_DIR',tmp_path/'data/users')
    monkeypatch.setattr(config,'SECRETS_DIR',tmp_path/'.secrets')


def test_removing_a_session_cannot_preserve_existing_confirmed_frequency_contract():
    source=source_for('quality-removal')
    original=double_proposal(source)
    raw=original.model_dump(mode='json')
    raw['sessions']=raw['sessions'][1:]
    candidate=plan_proposal.PlanProposalV2.model_validate(raw)
    validation=plan_proposal.validate_plan_proposal(candidate,source)
    assert not validation.valid
    assert 'session_count_mismatch' in {item.code for item in validation.errors}
    assert source.plan_constraints.confirmed_sessions_per_week==2
    assert [item.session_id for item in original.sessions]==['morning','evening']
    assert candidate.sessions[0].session_id=='evening'


def test_removing_last_session_is_not_an_existing_empty_plan_contract():
    raw=double_proposal(source_for('quality-last-removal')).model_dump(mode='json')
    raw['sessions']=[]
    with pytest.raises(ValidationError):
        plan_proposal.PlanProposalV2.model_validate(raw)


def test_current_session_contract_does_not_have_an_implicit_removal_tombstone():
    original=double_proposal(source_for('quality-tombstone'))
    raw=original.model_dump(mode='json')
    raw['sessions'][0]['removed']=True
    with pytest.raises(ValidationError):
        plan_proposal.PlanProposalV2.model_validate(raw)

from concurrent.futures import ThreadPoolExecutor
from garmin_coach import coach_session_actions as actions, plan_acceptance, garmin_delivery
from tests.test_plan_proposal import settings
from tests.test_calendar_3c import accept


@pytest.fixture
def scenario(monkeypatch):
    user='independent-action-quality'
    source=source_for(user)
    raw=double_proposal(source).model_dump(mode='json')
    raw['sessions'][0].update(duration_minutes=35)
    raw['sessions'][0]['structure'].update(effort_minutes=25,main_set='Vingt-cinq minutes faciles.')
    proposal=plan_proposal.PlanProposalV2.model_validate(raw)
    monkeypatch.setattr(plan_proposal,'build_plan_generation_input_v2',lambda *a,**kw:source)
    monkeypatch.setattr(plan_proposal.ai_provider,'structured_completion',lambda *a,**kw:proposal)
    monkeypatch.setattr(actions,'_source',lambda uid:source)
    plan_proposal.generate_plan_proposal(user,settings=settings())
    def state():
        with config.user_context(user):return plan_proposal._state(source)
    return user,source,state


def protected_files(user):
    with config.user_context(user):
        return {str(p):p.read_bytes() if p.exists() else None for p in
            (config.PLAN_PROPOSAL_FILE,config.PLAN_ACCEPTANCE_FILE,config.GARMIN_DELIVERY_FILE,config.PLANS_DIR/'2099-10-12.json')}


def draft_for(user,minutes=25,identity='morning'):
    return actions.prepare_session_action(user,dict(action='modify',session_id=identity,target_duration_minutes=minutes))


def test_permutation_after_preview_never_redirects_action_to_other_index(scenario):
    user,_,state=scenario
    draft=draft_for(user)
    with config.user_context(user):
        store,_=plan_proposal._read_store()
        changed=store.proposal.model_copy(update={'sessions':list(reversed(store.proposal.sessions))})
        plan_proposal._write_store(store.model_copy(update={'proposal':changed}))
    before=protected_files(user)
    with pytest.raises(actions.SessionActionError):actions.confirm_session_action(user,draft['draft_id'])
    assert protected_files(user)==before


def test_evening_choice_preserves_morning_and_legacy_plan_bytes(scenario):
    user,_,state=scenario
    with config.user_context(user):
        config.PLANS_DIR.mkdir(exist_ok=True,parents=True)
        (config.PLANS_DIR/'2099-10-12.json').write_text('{"seances":[{"titre":"legacy untouched"}]}')
    before=protected_files(user)
    morning=state().proposal.sessions[0].model_dump(mode='json')
    draft=draft_for(user,20,'evening')
    assert protected_files(user)==before
    actions.confirm_session_action(user,draft['draft_id'])
    assert state().proposal.sessions[0].model_dump(mode='json')==morning
    assert state().proposal.sessions[1].duration_minutes==20
    assert protected_files(user)[next(p for p in before if p.endswith('2099-10-12.json'))]==before[next(p for p in before if p.endswith('2099-10-12.json'))]


@pytest.mark.parametrize('decision',['refused','accepted'])
def test_new_consent_after_preview_cannot_be_silently_overwritten(scenario,decision):
    user,_,state=scenario
    draft=draft_for(user)
    current=state()
    plan_acceptance.record_plan_acceptance_decision(user,decision=decision,session_id='morning',proposal_state=current,
        input_fingerprint=current.input_fingerprint,expected_proposal_fingerprint=plan_proposal.proposal_fingerprint(current.proposal))
    before=protected_files(user)
    with pytest.raises(actions.SessionActionError):actions.confirm_session_action(user,draft['draft_id'])
    assert protected_files(user)==before


def test_distinct_concurrent_previews_apply_only_one_current_version(scenario):
    user,_,state=scenario
    drafts=[draft_for(user,m) for m in (25,27)]
    def confirm(draft):
        try:return actions.confirm_session_action(user,draft['draft_id'])['status']
        except actions.SessionActionError as error:return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(confirm,drafts))
    assert sorted(results)==['confirmed','draft_stale']
    assert state().proposal.sessions[0].duration_minutes in (25,27)
    assert state().proposal.sessions[1].duration_minutes==30


@pytest.mark.parametrize('record',[{'upload_requested_at':'2099-10-13T08:00:00+02:00'}, {'workout_id':701}])
def test_legacy_unidentified_remote_record_blocks_new_draft_without_erasing_it(scenario,record):
    user,_,_=scenario
    with config.user_context(user):garmin_delivery._write_store({'schema_version':1,'deliveries':{'old-unidentified':record}})
    before=protected_files(user)
    with pytest.raises(actions.SessionActionError):draft_for(user)
    assert protected_files(user)==before


def test_cancel_does_not_mutate_accepted_session_or_neighbor_workspace(scenario):
    user,_,state=scenario
    accept(user,state(),'morning');accept(user,state(),'evening')
    with config.user_context('neighbor-quality'):
        config.ensure_directories();config.PLAN_PROPOSAL_FILE.write_text('{"sentinel":"neighbor"}')
    before=protected_files(user);neighbor=protected_files('neighbor-quality')
    draft=draft_for(user);actions.cancel_session_action(user,draft['draft_id'])
    assert actions.confirm_session_action(user,draft['draft_id'])['status']=='cancelled'
    assert protected_files(user)==before and protected_files('neighbor-quality')==neighbor
    assert plan_acceptance.get_plan_acceptance_state(user,proposal_state=state(),session_id='morning').status=='accepted'


def test_candidate_blocks_must_match_exact_requested_duration_and_no_partial_plan_write(scenario):
    user,_,state=scenario
    structure=dict(warmup_minutes=5,cooldown_minutes=5,main_set='Deux efforts synthétiques.',
        blocks=[dict(repetitions=2,effort_minutes=3,recovery_minutes=1,intensity_instruction='Facile')])
    before=protected_files(user)
    with pytest.raises(actions.SessionActionError):
        actions.prepare_session_action(user,dict(action='modify',session_id='morning',target_duration_minutes=25,patch={'structure':structure}))
    assert protected_files(user)==before
    draft=actions.prepare_session_action(user,dict(action='modify',session_id='morning',target_duration_minutes=18,patch={'structure':structure}))
    actions.confirm_session_action(user,draft['draft_id'])
    executable=garmin_delivery.build_plan_session_workout(state().proposal.sessions[0])
    assert executable.estimatedDurationInSecs==1080


@pytest.mark.parametrize('bad',[{'session_index':0},{'session_id':'0'},{'session_id':'evening','target_duration_minutes':True}])
def test_index_or_maltyped_target_never_selects_a_session(scenario,bad):
    user,_,_=scenario
    before=protected_files(user)
    intent=dict(action='modify',session_id='morning',target_duration_minutes=25);intent.update(bad)
    with pytest.raises(actions.SessionActionError):actions.prepare_session_action(user,intent)
    assert protected_files(user)==before

@pytest.mark.parametrize('operation',['get_session_action','confirm_session_action','cancel_session_action'])
def test_foreign_draft_id_does_not_grant_access_to_another_account(scenario,operation):
    user,_,_=scenario
    draft=draft_for(user)
    before=protected_files(user)
    with pytest.raises(actions.SessionActionError) as error:
        getattr(actions,operation)('neighbor-quality',draft['draft_id'])
    assert error.value.code=='draft_not_found'
    assert protected_files(user)==before


def test_activity_imported_after_preview_blocks_confirmation_without_erasing_activity(scenario,monkeypatch):
    user,_,_=scenario
    draft=draft_for(user)
    activity={'activityId':'synthetic-completed','duration':1800,'sport':'running'}
    monkeypatch.setattr(actions,'iter_local_activity_history',lambda **kw:iter([activity]))
    before=protected_files(user)
    with pytest.raises(actions.SessionActionError) as error:actions.confirm_session_action(user,draft['draft_id'])
    assert error.value.code=='session_completed_or_ambiguous'
    assert protected_files(user)==before
    assert activity=={'activityId':'synthetic-completed','duration':1800,'sport':'running'}


def unidentified_state(scenario):
    user,source,state=scenario
    with config.user_context(user):
        store,_=plan_proposal._read_store()
        raw=store.proposal.model_dump(mode='json')
        for item in raw['sessions']:item.update(session_id=None,starts_at=None)
        raw['sessions'][1]['date']='2099-10-17'
        proposal=plan_proposal.PlanProposalV2.model_validate(raw)
        assert plan_proposal.validate_plan_proposal(proposal,source).valid
        plan_proposal._write_store(store.model_copy(update={'proposal':proposal}))
    return user,source,state


def test_unidentified_v2_sessions_remain_consultable_without_legacy_fallback(scenario,monkeypatch):
    from garmin_coach import coach_calendar_context,coach_chat
    user,_,state=unidentified_state(scenario)
    monkeypatch.setattr(coach_calendar_context,'get_plan_proposal_state',lambda uid:state())
    before=protected_files(user)
    calendar=coach_calendar_context.build_coach_calendar_context(user)
    assert calendar['status']=='available' and len(calendar['sessions'])==2
    assert all(item.get('session_id') is None for item in calendar['sessions'])
    response=coach_chat._grounded_current_plan_response('Quelles sont mes séances cette semaine ?',
        {'calendrier_plan_v2':calendar,'plan_actif':{'seances':[{'titre':'LEGACY_SENTINEL','date':'2099-10-13'}]}})
    assert response is not None and '35 min' in response.message and '30 min' in response.message
    assert 'LEGACY_SENTINEL' not in response.message
    assert protected_files(user)==before


def test_unidentified_v2_cannot_be_mutated_with_missing_or_invented_identity(scenario):
    user,_,_=unidentified_state(scenario)
    before=protected_files(user)
    with config.user_context(user):
        path=config.DATA_DIR/'coach_session_actions_v1.json'
        assert not path.exists()
    for identity in (None,'morning'):
        with pytest.raises(actions.SessionActionError):
            actions.prepare_session_action(user,dict(action='modify',session_id=identity,target_duration_minutes=25))
    assert protected_files(user)==before
    with config.user_context(user):assert not (config.DATA_DIR/'coach_session_actions_v1.json').exists()
