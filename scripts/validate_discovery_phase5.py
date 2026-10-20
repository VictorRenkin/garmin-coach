"""Five bounded synthetic discovery hypotheses, no Garmin calls."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from garmin_coach import coach_chat, config, dashboard, history_import, user_accounts
from garmin_coach.goal import build_goal_v1
from garmin_coach.availability import build_availability_v1
from garmin_coach.subjective_context import build_subjective_context_v1
from garmin_coach.runtime_context import update_motionest_inputs_v1, load_motionest_inputs_v1
from tests.test_first_run_journey import _tokens
from garmin_coach.coach_numeric_presentation import build_numeric_presentation
from tests.test_conversation_sport_context import REFERENCE, seed

CASES = {
    'empty': "Je n'ai connecté aucune source sportive. Que sais-tu réellement de mon entraînement ? Distingue mes déclarations des observations. Ne crée ni ne modifie rien.",
    'declared': "Mes disponibilités et ma pratique complémentaire sont déclarées, sans source sportive connectée. Résume-les en distinguant les mesures manquantes, sans déduire ma fatigue ni inventer de chiffres. Ne change rien.",
    'goal': "J'ai déclaré un objectif 10 km sans importer d'activité. Que peux-tu en dire et quelles limites dois-tu conserver ? Ne crée ni ne publie de plan.",
    'late': "Je viens de connecter une source et d'importer une activité. Décris uniquement les observations disponibles et distingue-les de mon objectif déclaré. Aucun changement ni publication.",
    'followup': "Reprends notre échange précédent : que sais-tu maintenant, et qu'est-ce qui reste inconnu malgré ma course importée ? Réponds en deux paragraphes lisibles, sans aucune action.",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--real', action='store_true')
    parser.add_argument('--env', type=Path)
    parser.add_argument('--output', type=Path, default=Path('reports/discovery-phase5-real.json'))
    args = parser.parse_args()
    if not args.real:
        raise SystemExit('Use --real explicitly. Ceiling five calls, zero retries.')
    if args.env:
        load_dotenv(args.env, override=True)
    settings = replace(config.load_settings(), email=None, password=None)
    if not settings.ai_api_key or settings.ai_provider != 'openai' or settings.ai_base_url:
        raise SystemExit('Native configured OpenAI required; no requests made.')
    report = {'synthetic_only': True, 'real_garmin_calls': 0, 'api_attempts': 0,
              'profiles': [], 'manual_review_required': True}
    original = coach_chat.OpenAI

    class Responses:
        def __init__(self, inner): self.inner = inner
        def parse(self, **kwargs):
            assert report['api_attempts'] < 5
            payload = json.loads(kwargs['input'])
            text = json.dumps(payload)
            assert all(x not in text for x in ('PRIVATE TITLE', 'private@example.com', 'synthetic.invalid', 'latitude', 'longitude', 'oauth_token', 'access_token', 'refresh_token'))
            item = report['profiles'][-1]
            sent = payload['contexte']
            assert sent['contexte_multisport'] == item['canonical_context']
            assert sent['presentation_numerique'] == item['numeric']
            assert sent['connexion_sportive'] == item['connection']
            item['payload_bytes'] = len(json.dumps(payload, ensure_ascii=False).encode())
            report['api_attempts'] += 1
            result = self.inner.parse(**kwargs)
            item['technical_success'] = True
            return result

    class Client:
        def __init__(self, **kwargs):
            kwargs['max_retries'] = 0
            self.responses = Responses(original(**kwargs).responses)

    def save():
        usage = [u for p in report['profiles'] for u in p.get('usage_new', [])]
        for key in ('input_tokens', 'output_tokens', 'total_tokens'):
            report[key] = sum(u.get(key, 0) for u in usage)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    with tempfile.TemporaryDirectory(prefix='motionest-explanations-') as temp:
        root = Path(temp)
        with patch.multiple(config, GLOBAL_DATA_DIR=root, USERS_DATA_DIR=root/'users', SECRETS_DIR=root/'.secrets'), patch.object(user_accounts, 'USERS_FILE', root/'accounts.json'), patch.object(user_accounts, 'SESSION_KEY_FILE', root/'.secrets'/'session.key'), patch.object(coach_chat, 'OpenAI', Client):
            for name, prompt in CASES.items():
                user = 'synthetic-' + ('goal' if name in ('late','followup') else name)
                item = {'name': name, 'prompt': prompt, 'technical_success': False, 'automated_checks_success': False}
                report['profiles'].append(item)
                with config.user_context(user):
                    usage_path = root/'users'/user/'ai_usage.json'
                    before = json.loads(usage_path.read_text()) if usage_path.exists() else []
                    try:
                        if name not in ('late','followup'):
                            records = json.loads(user_accounts.USERS_FILE.read_text()) if user_accounts.USERS_FILE.exists() else []
                            records.append({'id': user, 'email': user+'@synthetic.invalid', 'email_verified': True})
                            user_accounts.USERS_FILE.write_text(json.dumps(records), encoding='utf-8')
                            config.ensure_directories()
                            config.ONBOARDING_FILE.write_text(json.dumps({'onboarding_version':2, 'status':'ready_for_garmin', 'discovery_without_garmin':True}), encoding='utf-8')
                            update_motionest_inputs_v1(user, goal=build_goal_v1('race',event_type='10k') if name in ('goal','late') else build_goal_v1('beginner'))
                            if name == 'declared':
                                update_motionest_inputs_v1(user, availability=build_availability_v1(['tue','thu','sat'],preferred_sessions_per_week=3), subjective_context=build_subjective_context_v1(affecting_training=False,unrecorded_activities=[{'sport':'musculation','sessions_per_week':2,'typical_duration_minutes':30}]))
                        if name == 'late':
                            prior_profile = load_motionest_inputs_v1(user).model_dump(mode='json')
                            _tokens(root,user)
                            seed(user,['running'])
                            history_import.update_history_import({'status':'complete','pagination_complete':True})
                            assert load_motionest_inputs_v1(user).model_dump(mode='json') == prior_profile
                        profile_before = load_motionest_inputs_v1(user).model_dump(mode='json')
                        request = coach_chat.submit_message(prompt)
                        context = dashboard.coach_context()
                        numeric = build_numeric_presentation(context)
                        assert numeric and len(json.dumps(numeric).encode()) < 4096
                        multi = context['contexte_multisport']
                        observed = name in ('late', 'followup')
                        assert multi['history']['coverage']['activity_count'] == (1 if observed else 0)
                        assert (multi['history']['source'] == 'multisport') == observed
                        item['historical_activity_count'] = multi['history']['coverage']['activity_count']
                        item['journey_stage'] = context.get('etape_parcours')
                        connection = context['connexion_sportive']
                        assert connection == {'garmin_connected': observed, 'discovery_without_garmin': not observed}
                        item['connection'] = connection
                        if name == 'followup':
                            assert len(context['conversation_recente']) >= 3
                            assert any(m['role'] == 'assistant' for m in context['conversation_recente'])
                        item.update(canonical_context=multi, numeric=numeric, declarations=profile_before, conversation_messages=len(context['conversation_recente']))
                        attempts_before = report['api_attempts']
                        response = coach_chat.generate_response(request, settings, context)
                        item['response'] = response.model_dump(mode='json')
                        assert report['api_attempts'] == attempts_before + 1 and item['technical_success'], 'Expected one measured SDK call'
                        assert response.action == 'none' and not response.plan_modifie
                        coach_chat.store_response(request['id'], response)
                        assert load_motionest_inputs_v1(user).model_dump(mode='json') == profile_before
                        item['automated_checks_success'] = True
                    except Exception as error:
                        item['error_type'] = type(error).__name__
                        if isinstance(error, AssertionError): item['error_detail'] = str(error)[:300]
                    usage = json.loads(usage_path.read_text()) if usage_path.exists() else []
                    item['usage_new'] = usage[len(before):]
                    save()
                    print(name, item['technical_success'], item['automated_checks_success'], flush=True)
    save()
    print(json.dumps({k: v for k, v in report.items() if k != 'profiles'}))
    if not all(p['automated_checks_success'] for p in report['profiles']): raise SystemExit(1)


if __name__ == '__main__': main()
