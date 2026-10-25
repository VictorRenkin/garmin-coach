"""Authenticated Web paths for synthetic V2 drafts; all Garmin access forbidden."""
import json
from pathlib import Path

import pytest

from garmin_coach import coach_chat, config, dashboard, user_accounts, plan_proposal, plan_acceptance
from garmin_coach import coach_session_actions as actions
from tests.test_coach_session_actions import setup, snapshot, shorten
from tests.test_onboarding_v2 import _serve
from tests.test_planning_decision import _request
from tests.test_calendar_3c import accept


def _post(server, cookie, path, payload):
    return _request(server, 'POST', path, cookie, payload)


@pytest.fixture
def web(setup, tmp_path, monkeypatch):
    user, source, state = setup
    monkeypatch.setattr(user_accounts, 'SESSION_KEY_FILE', tmp_path/'session.key')
    monkeypatch.setattr(user_accounts, 'USERS_FILE', tmp_path/'accounts.json')
    monkeypatch.setattr(dashboard, 'load_onboarding', lambda:{'onboarding_version':2, 'status':'complete'})
    def forbidden(*a, **kw): raise AssertionError('Garmin/network forbidden')
    for name in ('connect','replace_future_plan','remove_future_sessions','publish_plan'):
        monkeypatch.setattr(dashboard,name,forbidden)
    server, thread = _serve()
    cookie=f'motionest_session={user_accounts.create_session(user)}'
    try:
        yield user, state, server, cookie
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def answer(user):
    with config.user_context(user):
        request=coach_chat.submit_message('Raccourcis ma séance du matin à 25 minutes.')
        return coach_chat.store_response(request['id'], coach_chat.CoachResponse(message='Proposition sans effet.',
            session_action_v2=actions.SessionActionIntent(action='modify',session_id='morning',target_duration_minutes=25)))


def test_response_prepares_only_then_authenticated_confirmation_updates_exact_session(web):
    user,state,server,cookie=web
    before=snapshot(user)
    response=answer(user)
    assert response['action']=='none' and snapshot(user)==before
    draft=response['session_action_preview']
    status,payload=_post(server,cookie,'/api/coach/session-action/confirm',{'draftId':draft['draft_id']})
    assert status==200 and payload['preview']['status']=='confirmed'
    assert state().proposal.sessions[0].duration_minutes==25
    assert state().proposal.sessions[1].duration_minutes==30
    with config.user_context(user):
        assert coach_chat.list_messages()[-1]['session_action_preview']['status']=='confirmed'


def test_cancel_and_confirm_cancelled_never_change_plan_or_claim_applied(web):
    user,state,server,cookie=web
    draft=answer(user)['session_action_preview'];before=snapshot(user)
    status,payload=_post(server,cookie,'/api/coach/session-action/cancel',{'draftId':draft['draft_id']})
    assert status==200 and payload['preview']['status']=='cancelled'
    status,payload=_post(server,cookie,'/api/coach/session-action/confirm',{'draftId':draft['draft_id']})
    assert status==200 and 'annulé' in payload['message'] and 'confirmée' not in payload['message']
    assert snapshot(user)==before


def test_cancel_after_confirm_does_not_claim_rollback(web):
    user,state,server,cookie=web
    draft=answer(user)['session_action_preview']
    _post(server,cookie,'/api/coach/session-action/confirm',{'draftId':draft['draft_id']})
    before=snapshot(user)
    status,payload=_post(server,cookie,'/api/coach/session-action/cancel',{'draftId':draft['draft_id']})
    assert status==200 and payload['preview']['status']=='confirmed'
    assert 'confirmée' in payload['message'] and 'annulé' not in payload['message']
    assert snapshot(user)==before


@pytest.mark.parametrize('endpoint',['confirm','cancel'])
def test_neighbour_cannot_read_or_decide_draft(web,endpoint):
    user,state,server,cookie=web
    draft=answer(user)['session_action_preview'];before=snapshot(user)
    neighbour=f'motionest_session={user_accounts.create_session("neighbour")}'
    status,payload=_post(server,neighbour,f'/api/coach/session-action/{endpoint}',{'draftId':draft['draft_id']})
    assert status==409 and payload['code']=='draft_not_found' and snapshot(user)==before


def test_anonymous_and_unconfirmed_direct_route_have_no_effect(web):
    user,state,server,cookie=web
    draft=answer(user)['session_action_preview'];before=snapshot(user)
    assert _post(server,'','/api/coach/session-action/confirm',{'draftId':draft['draft_id']})[0]==401
    status,payload=_post(server,cookie,'/api/plan-proposal/session',{'session_id':'morning',
        'proposal_fingerprint':plan_proposal.proposal_fingerprint(state().proposal),'patch':{'objective':'Ne pas appliquer.'}})
    assert status==409 and payload['code']=='confirmation_required' and snapshot(user)==before


def test_old_chat_action_is_blocked_for_v2_without_garmin_or_plan_write(web,monkeypatch):
    user,state,server,cookie=web
    monkeypatch.setattr(dashboard,'get_message',lambda _:{'id':'old','role':'assistant','action':'modify_session',
        'session_index':0,'session_patch':{'titre':'Mauvaise cible historique'}})
    before=snapshot(user)
    status,payload=_post(server,cookie,'/api/coach/action',{'answerId':'old'})
    assert status==409 and payload['code']=='v2_calendar_action_required' and snapshot(user)==before


def test_acceptance_changed_after_preview_blocks_confirmation(web):
    user,state,server,cookie=web
    draft=answer(user)['session_action_preview']
    plan_acceptance.record_plan_acceptance_decision(user,decision='refused',input_fingerprint=state().input_fingerprint,
        expected_proposal_fingerprint=plan_proposal.proposal_fingerprint(state().proposal),proposal_state=state(),session_id='morning')
    before=snapshot(user)
    status,payload=_post(server,cookie,'/api/coach/session-action/confirm',{'draftId':draft['draft_id']})
    assert status==409 and snapshot(user)==before


def test_natural_cancellation_closes_pending_draft_without_ai_or_plan_change(web):
    user,state,server,cookie=web
    response=answer(user);before=snapshot(user)
    with config.user_context(user):
        result=coach_chat.generate_response({'content':'Annule, finalement.'},config.Settings(None,None,300,None),{})
        assert result.action=='none'
        assert actions.get_session_action(user,response['session_action_preview']['draft_id'])['status']=='cancelled'
    assert snapshot(user)==before


def test_model_cannot_map_available_v2_to_legacy_action():
    result=coach_chat._validate_conversation_output(coach_chat.CoachResponse(message='Modifier ancien indice.',
        action='modify_session',session_index=0,session_patch={'titre':'Ancienne cible'}),{'content':'Raccourcis mardi'},
        {'calendrier_plan_v2':{'status':'available'}})
    assert result.action=='none' and result.session_action_v2 is None


def test_web_preview_has_before_after_and_explicit_local_buttons():
    html=dashboard.TEMPLATE_FILE.read_text(encoding='utf-8')
    for text in ('sessionActionPreviewMarkup','Actuellement','Après confirmation','data-session-action="confirm"',
                 'data-session-action="cancel"','>Confirmer</button>','>Annuler</button>'):
        assert text in html
