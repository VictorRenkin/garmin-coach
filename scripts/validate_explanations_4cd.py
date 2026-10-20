"""Four bounded synthetic provenance/readability hypotheses, no Garmin calls."""
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
from garmin_coach import coach_chat, config, dashboard, history_import
from garmin_coach.coach_numeric_presentation import build_numeric_presentation
from tests.test_conversation_sport_context import REFERENCE, seed

CASES = {
    'interrupted': "La liste locale des activités est complète. Cela prouve-t-il que toutes les activités de mon compte Garmin sont importées et que mon entraînement réel est entièrement connu ? Explique les limites, sans aucune action.",
    'bounded': "Mon import terminé sur trois mois et sa pagination complète prouvent-ils que tu connais tout mon historique Garmin depuis toujours ? Distingue le périmètre importé et ce qui reste inconnu. Ne change rien.",
    'empty': "Aucune activité n'est enregistrée localement. Cela prouve-t-il que je ne me suis pas entraîné ? Précise les données manquantes sans inventer de mesure. Ne change rien.",
    'followup': "Reprends notre échange précédent sur mon import interrompu. Résume en deux paragraphes lisibles ce qui est connu et inconnu, avec le mot vélo et un emoji. Ne change rien.",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--real', action='store_true')
    parser.add_argument('--env', type=Path)
    parser.add_argument('--output', type=Path, default=Path('reports/explanations-4cd-real.json'))
    args = parser.parse_args()
    if not args.real:
        raise SystemExit('Use --real explicitly. Ceiling four calls, zero retries.')
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
            assert report['api_attempts'] < 4
            payload = json.loads(kwargs['input'])
            text = json.dumps(payload)
            assert all(x not in text for x in ('PRIVATE TITLE', 'private@example.com', 'latitude', 'longitude'))
            item = report['profiles'][-1]
            sent = payload['contexte']
            assert sent['contexte_multisport'] == item['canonical_context']
            assert sent['presentation_numerique'] == item['numeric']
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
        with patch.multiple(config, GLOBAL_DATA_DIR=root, USERS_DATA_DIR=root/'users', SECRETS_DIR=root/'secrets'), patch.object(dashboard, 'build_first_run_journey', lambda user: {}), patch.object(coach_chat, 'OpenAI', Client):
            for name, prompt in CASES.items():
                user = 'synthetic-' + ('interrupted' if name == 'followup' else name)
                item = {'name': name, 'prompt': prompt, 'technical_success': False, 'automated_checks_success': False}
                report['profiles'].append(item)
                with config.user_context(user):
                    usage_path = root/'users'/user/'ai_usage.json'
                    before = json.loads(usage_path.read_text()) if usage_path.exists() else []
                    try:
                        if name != 'followup':
                            seed(user, [] if name == 'empty' else ['running'])
                            if name == 'bounded':
                                history_import.initialize_history_import('3_months', reference_date=REFERENCE)
                            history_import.update_history_import({'status': 'complete' if name == 'bounded' else 'interrupted', 'pagination_complete': name == 'bounded'})
                        request = coach_chat.submit_message(prompt)
                        context = dashboard.coach_context()
                        numeric = build_numeric_presentation(context)
                        assert numeric and len(json.dumps(numeric).encode()) < 4096
                        multi = context['contexte_multisport']
                        assert multi['import_status'] == ('complete' if name == 'bounded' else 'interrupted')
                        assert multi['import_pagination_complete'] == (name == 'bounded')
                        if name == 'followup':
                            assert len(context['conversation_recente']) >= 3
                            assert any(m['role'] == 'assistant' for m in context['conversation_recente'])
                        item.update(canonical_context=multi, numeric=numeric, conversation_messages=len(context['conversation_recente']))
                        attempts_before = report['api_attempts']
                        response = coach_chat.generate_response(request, settings, context)
                        item['response'] = response.model_dump(mode='json')
                        assert report['api_attempts'] == attempts_before + 1 and item['technical_success'], 'Expected one measured SDK call'
                        assert response.action == 'none' and not response.plan_modifie
                        coach_chat.store_response(request['id'], response)
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
