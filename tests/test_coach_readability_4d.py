"""Response-boundary validation, without rewriting useful text or numbers."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from garmin_coach import coach_chat as coach, config, dashboard
from garmin_coach.ai_provider import _json_from_text


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'GLOBAL_DATA_DIR',tmp_path)
    monkeypatch.setattr(config,'USERS_DATA_DIR',tmp_path/'users')
    monkeypatch.setattr(config,'SECRETS_DIR',tmp_path/'secrets')


def settings():
    return config.Settings(None,None,300,'synthetic-key','synthetic-model')


@pytest.mark.parametrize('char',['\x00','\x01','\x0b','\x0c','\x0e','\x19','\x1f','\x7f'])
@pytest.mark.parametrize('provider',['native','compatible'])
def test_invalid_provider_text_is_rejected_without_guessing_digits(char,provider,monkeypatch):
    # model_construct represents an SDK/mock object that bypassed validators.
    response=coach.CoachResponse.model_construct(message=f'5{char}30 secondes.',source='ai')
    calls=[]
    def parse(**kwargs):
        calls.append(1)
        return SimpleNamespace(output_parsed=response)
    def completion(*args,**kwargs):
        calls.append(1)
        return response
    monkeypatch.setattr(coach,'OpenAI',lambda **kwargs:SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    monkeypatch.setattr(coach,'structured_completion',completion)
    monkeypatch.setattr(coach,'record_usage',lambda *args,**kwargs:None)
    resolved=settings() if provider=='native' else replace(settings(),ai_base_url='https://synthetic.invalid')
    with pytest.raises(RuntimeError,match='temporairement indisponible') as error:
        coach.generate_response({'content':'Explique le volume observé.'},resolved,{})
    assert isinstance(error.value.__cause__,ValueError)
    assert len(calls)==1  # No automatic retry or corrective model call.
    assert response.message==f'5{char}30 secondes.'


@pytest.mark.parametrize('method',['model_copy','model_construct'])
def test_storage_guard_rejects_bypass_before_request_or_action_is_changed(method):
    with config.user_context('alice'):
        request=coach.submit_message('Je confirme le déplacement de la séance 1.')
        before=coach.MESSAGES_FILE.read_bytes()
        normal=coach.CoachResponse(message='Une proposition de déplacement.',action='reschedule_session',
            session_index=0,new_date='2099-10-14')
        invalid=(normal.model_copy(update={'message':'5\x1930 secondes.'}) if method=='model_copy'
                 else coach.CoachResponse.model_construct(**{**normal.model_dump(),'message':'5\x1930 secondes.'}))
        with pytest.raises(ValueError,match='contrôle illisibles'):
            coach.store_response(request['id'],invalid)
        assert coach.MESSAGES_FILE.read_bytes()==before
        assert coach.get_message(request['id'])['status']=='pending'
        assert not list(coach.COACH_DRAFTS_DIR.glob('*.json'))


def test_unreadable_compatible_action_never_enters_existing_ai_repair(monkeypatch):
    calls=[]
    response=coach.CoachResponse(message='5\x1930 secondes.',action='reschedule_session',
        session_index=0,new_date='2099-10-14')
    def completion(*args,**kwargs):
        calls.append(1)
        return response
    monkeypatch.setattr(coach,'structured_completion',completion)
    with pytest.raises(RuntimeError):
        coach.generate_response({'content':'Je confirme le contenu proposé.'},
            replace(settings(),ai_base_url='https://synthetic.invalid'),{})
    assert len(calls)==1


def test_json_extraction_and_storage_preserve_source_instead_of_inventing_corruption():
    raw='Volume : 5\x1930 secondes.'
    encoded=json.dumps({'message':raw})
    assert '\\u0019' in encoded and _json_from_text(encoded)['message']==raw
    assert json.loads(json.dumps(raw))==raw
    with config.user_context('alice'):
        # Existing archived facts are not migrated or presented as never applied.
        config.ensure_directories()
        legacy=[{'id':'old','role':'assistant','content':raw,'action':'reschedule_session','action_status':'applied'}]
        coach.MESSAGES_FILE.write_text(json.dumps(legacy),encoding='utf-8')
        before=coach.MESSAGES_FILE.read_bytes()
        assert coach.list_messages()==legacy
        assert coach.MESSAGES_FILE.read_bytes()==before


def test_unicode_numbers_line_breaks_and_action_structure_survive_two_turns():
    text='37\u202f800 secondes = 10 h 30. Échauffement : 30 / 45 / 90 s.\r\n\nVélo 🚴🏽\u200d♀️, e\u0301, 中文.\t2 h 37 min 30 s.\u0085Suite\u2028Suite\u2029Fin'
    response=coach.CoachResponse(message=text,action='modify_session',session_index=0,
        session_patch=coach.SessionPatch(recuperation_minutes=.75),source='openai')
    original=response.model_dump()
    with config.user_context('alice'):
        request=coach.submit_message('Modifie précisément la récupération de la séance 1 à45 secondes.')
        checked=coach._validate_conversation_output(response,request,{})
        stored=coach.store_response(request['id'],checked)
        assert stored['content']==text and stored['session_patch']['recuperation_minutes']==.75
        assert checked.model_dump()==original
        coach.submit_message('Et que disais-tu sur les durées ?')
        recent=dashboard.coach_context()['conversation_recente']
        assert any(item.get('content')==text for item in recent)
    with config.user_context('bob'):
        assert coach.list_messages()==[]


def test_observed_literal_paragraph_artifact_is_rejected_not_decoded_or_persisted():
    text=r'Aucun plan n’est créé.\n\nÀ titre de repère, 275 km sont enregistrés.'
    request={'content':'Peux-tu définir mon objectif vélo ?'}
    response=coach.CoachResponse(message=text)
    with pytest.raises(ValueError,match='paragraphes illisibles'):
        coach._validate_conversation_output(response,request,{})
    assert response.message==text
    with config.user_context('alice'):
        pending=coach.submit_message(request['content'])
        before=coach.MESSAGES_FILE.read_bytes()
        with pytest.raises(ValueError):coach.store_response(pending['id'],response)
        assert coach.MESSAGES_FILE.read_bytes()==before


@pytest.mark.parametrize('text,prompt',[
    ('Premier paragraphe.\n\nÀ la suite, 10 h30.','Explique le volume.'),
    (r'Exemple : `a.\n\nB` reste littéral.','Explique ce texte.'),
    ('```json\n{"message":"a.\\n\\nB"}\n```','Explique ce texte.'),
    (r'Le chemin est C:\n\nArchive et doit être conservé.','Explique ce texte.'),
    (r'Le texte est "a.\n\nB" et doit être conservé.','Explique ce texte.'),
    (r'Avant.\n\nAprès.','Explique la séquence littérale de retours à la ligne.'),
    (r'Avant.\n\nAprès.',r'Explique \n\n précisément.'),
])
def test_legitimate_newlines_verbatim_and_useful_escapes_are_unchanged(text,prompt):
    response=coach.CoachResponse(message=text)
    assert coach._validate_conversation_output(response,{'content':prompt},{}).message==text
