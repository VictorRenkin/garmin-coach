import json
from types import SimpleNamespace

import pytest

from garmin_coach import coach_chat
from garmin_coach.config import Settings


@pytest.mark.parametrize('provider', ['openai', 'compatible'])
@pytest.mark.parametrize('sport,prompt', [
    ('running', 'Je prépare un 10 km, que peux-tu publier ?'),
    ('cycling', 'Crée mon plan vélo de 150 km.'),
    ('soccer', 'Crée mes séances de football.'),
    ('triathlon', 'Publie mon plan triathlon complet.'),
    ('strength_training', 'Analyse ma musculation et ma course.'),
])
def test_capability_instructions_reach_each_provider(monkeypatch, provider, sport, prompt):
    captured = {}
    expected = coach_chat.CoachResponse(message='Analyse disponible ; aucune publication effectuée.')

    def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_parsed=expected)

    def completion(settings, **kwargs):
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(coach_chat, 'OpenAI', lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    monkeypatch.setattr(coach_chat, 'structured_completion', completion)
    monkeypatch.setattr(coach_chat, 'record_usage', lambda *args, **kwargs: None)
    settings = Settings(None, None, 300, 'synthetic-key', 'test-model')
    if provider == 'compatible':
        from dataclasses import replace
        settings = replace(settings, ai_base_url='https://synthetic.invalid/v1')
    context = {'objectif': {}, 'conversation_recente': [], 'sport_observe': sport}
    result = coach_chat.generate_response({'content': prompt}, settings, context)
    instructions = captured['instructions']
    for distinction in ('Sport observé', 'Sport analysable', 'Objectif reconnu', 'Séance générable', 'Publication Garmin possible'):
        assert distinction in instructions
    assert 'action=none pour sa' in instructions
    assert 'le formulaire actuel est consacré à la course à pied' in instructions
    assert 'déclaration conversationnelle' in instructions
    assert 'Ces limites priment sur les consignes' in instructions
    assert 'sans générateur trail dédié' in instructions
    payload = json.loads(captured['input']) if provider == 'openai' else captured['payload']
    assert payload['contexte']['sport_observe'] == sport
    assert result.action == 'none'
