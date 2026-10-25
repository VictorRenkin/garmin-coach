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
