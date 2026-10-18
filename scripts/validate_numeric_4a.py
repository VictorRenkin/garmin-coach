"""Four bounded numeric hypotheses on the synthetic phase-2A histories."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from garmin_coach import coach_chat, config, dashboard, history_import, sync
from garmin_coach.coach_numeric_presentation import build_numeric_presentation
from validate_conversations_2a import REFERENCE, seed

SCENARIOS = {
    'confirmed': (['running'] * 5,
        'Compare ma fréquence et mon volume enregistrés sur 28 et 84 jours, y compris la durée moyenne par semaine, puis les deux semaines récentes. Ne crée aucune action.'),
    'triathlon': (['running', 'cycling', 'lap_swimming'] * 2,
        'Compare mes deux semaines récentes par discipline et cite les volumes enregistrés de course, vélo et natation. Ne propose aucune modification.'),
    'incomplete': (['running', 'cycling', 'lap_swimming'],
        'Décris les durées et distances de mes deux semaines enregistrées. Les mesures manquantes et mon import incomplet permettent-ils de conclure à mon volume réel complet ? Ne change rien.'),
    'no_hr': (['running'] * 3,
        'Donne mes durées observées sur 28 et 84 jours et leurs moyennes hebdomadaires. Peux-tu en déduire ma charge physiologique ou ma fatigue si les mesures correspondantes manquent ? Ne change rien.'),
}


class ScenarioDate(date):
    @classmethod
    def today(cls):
        return REFERENCE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--real', action='store_true')
    parser.add_argument('--env', type=Path)
    parser.add_argument('--scenario', choices=SCENARIOS, nargs='+')
    parser.add_argument('--output', type=Path, default=Path('reports/phase-4a-real.json'))
    args = parser.parse_args()
    if not args.real:
        raise SystemExit('Use --real explicitly; no requests made.')
    if args.env:
        load_dotenv(args.env, override=True)
    settings = replace(config.load_settings(), email=None, password=None)
    if not settings.ai_api_key or settings.ai_provider != 'openai' or settings.ai_base_url:
        raise SystemExit('This bounded measurement requires the configured native OpenAI provider; no requests made.')
    report = {'reference_date': str(REFERENCE), 'synthetic_only': True, 'real_garmin_calls': 0,
              'api_attempts': 0, 'profiles': []}
    captured = []
    original = coach_chat.OpenAI

    class Responses:
        def __init__(self, inner):
            self.inner = inner
        def parse(self, **kwargs):
            payload = json.loads(kwargs['input'])
            encoded = json.dumps(payload)
            assert all(s not in encoded for s in ('PRIVATE-ID-', 'PRIVATE TITLE', 'private-synthetic@example.com', 'latitude'))
            captured.append(payload)
            report['api_attempts'] += 1
            return self.inner.parse(**kwargs)

    class Client:
        def __init__(self, **kwargs):
            self.responses = Responses(original(**kwargs).responses)

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    with tempfile.TemporaryDirectory(prefix='motionest-numeric-4a-') as temp:
        root = Path(temp)
        with patch.multiple(config, GLOBAL_DATA_DIR=root, USERS_DATA_DIR=root/'users', SECRETS_DIR=root/'secrets'), patch.object(coach_chat, 'OpenAI', Client):
            for name, (sports, prompt) in SCENARIOS.items():
                if args.scenario and name not in args.scenario:
                    continue
                with config.user_context(name):
                    # Reuse the original FakeGarmin seed; pin only its test clock to replay phase 2A.
                    with patch.object(sync, 'date', ScenarioDate):
                        seed(name, sports)
                    if name == 'incomplete':
                        history_import.update_history_import({'status': 'interrupted', 'pagination_complete': False})
                    context = dashboard.coach_context()
                    numeric = build_numeric_presentation(context)
                    windows = context['contexte_multisport']['history']['windows']
                    if name == 'confirmed':
                        assert windows['last_28_days']['observed_duration_seconds'] == 37800
                        assert windows['last_84_days']['observed_duration_seconds'] == 109800
                    item = {'name': name, 'prompt': prompt, 'numeric': numeric, 'canonical_windows': windows,
                            'import_status': history_import.load_history_import()['status'],
                            'canonical_weeks': {k:v['summary'] for k,v in context['contexte_multisport']['weeks'].items()}}
                    report['profiles'].append(item)
                    calls_before = len(captured)
                    try:
                        response = coach_chat.generate_response({'content': prompt, 'coach_mode': 'balanced'}, settings, context)
                        item.update(message=response.message, action=response.action, source=response.source,
                                    real_call=len(captured) > calls_before,
                                    payload_bytes=len(json.dumps(captured[-1], ensure_ascii=False, separators=(',', ':')).encode('utf-8')) if len(captured) > calls_before else 0)
                    except Exception as exc:
                        item['error_type'] = type(exc).__name__
                    usage = root/'users'/name/'ai_usage.json'
                    item['usage'] = json.loads(usage.read_text()) if usage.exists() else []
                    save()
                    print(name, item.get('error_type', 'ok'), flush=True)
    report['input_tokens'] = sum(e['input_tokens'] for p in report['profiles'] for e in p['usage'])
    report['output_tokens'] = sum(e['output_tokens'] for p in report['profiles'] for e in p['usage'])
    report['total_tokens'] = report['input_tokens'] + report['output_tokens']
    save()
    print(json.dumps({k:v for k,v in report.items() if k != 'profiles'}))
    if any('error_type' in p for p in report['profiles']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
