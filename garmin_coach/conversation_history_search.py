"""Bounded, local historical retrieval; no network, storage or new formulas."""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date, timedelta
from typing import Any

from .config import user_context
from .conversation_sport_context import _measurement, _project_activity, _size
from .history_import import load_history_import
from .local_activity_history import iter_local_activity_history, local_history_scope, load_local_activity_with_details
from .training_history_summary import _window, normalize_sport

MAX_SEARCH_BYTES = 24_000
MAX_PERIODS = 2
MAX_PERIOD_DAYS = 366
MONTHS = ('janvier', 'fevrier', 'mars', 'avril', 'mai', 'juin', 'juillet', 'aout', 'septembre', 'octobre', 'novembre', 'decembre')
SPORT_PATTERNS = (
    (r'\b(velo|cyclisme|cycliste|cycling|biking|indoor_cycling)\b', 'cycling'),
    (r'\b(course|couru|running|trail)\b', 'running'),
    (r'\b(natation|nage|swimming)\b', 'swimming'),
    (r'\b(football|soccer|foot)\b', 'football'),
    (r'\b(marche|walking)\b', 'walking'),
    (r'\b(randonnee|hiking)\b', 'hiking'),
    (r'\b(musculation|strength)\b', 'strength'),
    (r'\bpadel\b', 'padel'), (r'\btennis\b', 'tennis'),
)


def _detail_projection(activity: dict[str, Any]) -> dict[str, Any]:
    """Open at most one selected local detail; never forward arbitrary JSON."""
    try:
        loaded = load_local_activity_with_details(activity.get('activityId'))
        state = loaded.get('details') if isinstance(loaded, dict) else None
        endpoints = state.get('endpoints', {}) if isinstance(state, dict) else {}
        projected = {}
        for endpoint, keys in (
            ('get_activity_hr_in_timezones', ('zoneNumber', 'secsInZone', 'zoneLowBoundary')),
            ('get_activity_split_summaries', ('duration', 'distance', 'averageHR', 'averagePower')),
        ):
            value = endpoints.get(endpoint, {})
            data = value.get('data') if isinstance(value, dict) else None
            if isinstance(data, dict):
                data = data.get('splitSummaries', [])
            if isinstance(data, list):
                rows = [{key: number for key in keys if (number := _measurement(row, key)) is not None} for row in data[:10] if isinstance(row, dict)]
                projected[endpoint] = {'rows': [row for row in rows if row], 'rows_omitted': max(0, len(data)-10)}
        return {'status': 'available' if any(v['rows'] for v in projected.values()) else 'no_projectable_local_details', 'endpoints': projected}
    except Exception:
        return {'status': 'unavailable', 'error_code': 'local_details_read_failed'}


def parse_history_request(content: str, reference_date: str) -> dict[str, Any]:
    """Recognize explicit calendar requests. Bare months never imply an year."""
    plain = ''.join(c for c in unicodedata.normalize('NFKD', content.lower()) if not unicodedata.combining(c))
    reference = date.fromisoformat(reference_date)
    month_matches = list(re.finditer(r'\b(' + '|'.join(MONTHS) + r')\b', plain))
    iso_matches = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', plain)
    previous_month = bool(re.search(r'\bmois (?:dernier|precedent)\b', plain))
    if not month_matches and not iso_matches and not previous_month:
        if re.search(r'\b(volume|historique|bilan)\b', plain) and (any(re.search(pattern, plain) for pattern, _ in SPORT_PATTERNS) or re.search(r'\b(entrainement\w*|activite\w*|evolu\w*)\b', plain)):
            return {'status': 'needs_clarification', 'message': 'Quelle période souhaites-tu analyser ? Précise les mois et l’année, ou les dates.'}
        return {'status': 'not_requested'}
    # Do not redirect scheduling and future planning questions into historical retrieval.
    historical = bool(re.search(r'\b(historique|compare\w*|volume|bilan|activite\w*|entrainement\w*|sortie\w*|couru|roule|nage|fait|analyse\w*)\b', plain))
    if not historical or re.search(r'\b(planifie\w*|programme\w*|deplace\w*|reporte\w*)\b', plain):
        return {'status': 'not_requested'}
    sports = [sport for pattern, sport in SPORT_PATTERNS if re.search(pattern, plain)]
    if re.search(r'\b(rugby|basketball|basket|ski|squash|aviron|rowing|escalade|yoga|pilates|badminton|volleyball)\b', plain):
        return {'status': 'needs_clarification', 'message': 'Ce sport ne peut pas être sélectionné avec certitude. Précise une discipline prise en charge ou demande explicitement tous les sports.'}
    if len(sports) > 1:
        return {'status': 'needs_clarification', 'message': 'Quel sport souhaites-tu analyser ?'}
    if not sports and re.search(r'\bsortie\w* longue\w*\b', plain):
        return {'status': 'needs_clarification', 'message': 'Pour quel sport souhaites-tu retrouver cette sortie longue ?'}
    periods = []
    try:
        if iso_matches:
            if month_matches or previous_month or len(iso_matches) != 2:
                raise ValueError
            start, end = map(date.fromisoformat, iso_matches)
            periods.append({'start_date': start.isoformat(), 'end_date': end.isoformat()})
        elif previous_month:
            if month_matches:
                raise ValueError
            end = reference.replace(day=1) - timedelta(days=1)
            periods.append({'start_date': end.replace(day=1).isoformat(), 'end_date': end.isoformat()})
        else:
            years = re.findall(r'\b(?:19|20)\d{2}\b', plain)
            if not years:
                return {'status': 'needs_clarification', 'message': 'De quelle année parles-tu pour cette période historique ?'}
            if len(month_matches) > MAX_PERIODS or len(years) not in (1, len(month_matches)):
                raise ValueError
            for index, match in enumerate(month_matches):
                year = int(years[0 if len(years) == 1 else index])
                month = MONTHS.index(match.group()) + 1
                periods.append({'start_date': date(year, month, 1).isoformat(), 'end_date': date(year, month, calendar.monthrange(year, month)[1]).isoformat()})
            if len(periods) == 2 and re.search(r'\b(?:de|du)\s+(?:' + '|'.join(MONTHS) + r')\b.*?\ba\s+(?:' + '|'.join(MONTHS) + r')\b', plain):
                periods = [{'start_date': periods[0]['start_date'], 'end_date': periods[1]['end_date']}]
        for period in periods:
            start, end = date.fromisoformat(period['start_date']), date.fromisoformat(period['end_date'])
            if start > end or (end-start).days + 1 > MAX_PERIOD_DAYS or start > reference:
                raise ValueError
    except ValueError:
        return {'status': 'needs_clarification', 'message': 'Précise une ou deux périodes historiques avec leurs dates et années (au maximum un an par période).'}
    return {'status': 'requested', 'periods': periods, 'sport': sports[0] if sports else None,
            'longest_activity': bool(re.search(r'\bsortie\w* longue\w*\b', plain)),
            'evolution': bool(re.search(r'\b(evolu\w*|progress\w*)\b', plain)), 'reference_date': reference_date}


def search_conversation_history(user_id: str, query: dict[str, Any]) -> dict[str, Any]:
    """Retrieve only active user's imported metadata and canonical window totals."""
    if query.get('status') != 'requested':
        return query
    periods = query.get('periods', [])
    sport = query.get('sport')
    if not 1 <= len(periods) <= MAX_PERIODS or sport not in (None, *(s for _, s in SPORT_PATTERNS)):
        raise ValueError('Invalid historical request')
    parsed = [(date.fromisoformat(p['start_date']), date.fromisoformat(p['end_date'])) for p in periods]
    if any(a > b or (b-a).days + 1 > MAX_PERIOD_DAYS for a,b in parsed):
        raise ValueError('Invalid historical period')
    try:
        with user_context(user_id):
            scope = local_history_scope()
            state = load_history_import(detect_stale=False)
            results = []
            detail_loaded = False
            for start, end in parsed:
                accepted = {'football', 'soccer'} if sport == 'football' else {sport}
                selected = [a for a in iter_local_activity_history(start_date=start, end_date=end) if sport is None or normalize_sport(a) in accepted]
                outside = bool((scope.get('cutoff_date') and start.isoformat() < scope['cutoff_date']) or (scope.get('reference_date') and end.isoformat() > scope['reference_date']))
                incomplete = outside or (scope['source'] == 'multisport' and state.get('status') != 'complete')
                shown = selected
                if query.get('longest_activity'):
                    # No inferred workout label: identify only the longest observed distance.
                    measured = [a for a in selected if _project_activity(a).get('distance_m') is not None]
                    shown = sorted(measured, key=lambda a: _project_activity(a)['distance_m'], reverse=True)[:1]
                detail = None
                if shown and query.get('longest_activity'):
                    detail = _detail_projection(shown[0]) if not detail_loaded else {'status': 'not_loaded', 'reason': 'single_detail_lookup_budget'}
                    detail_loaded = True
                monthly = []
                if query.get('evolution'):
                    month_start = start.replace(day=1)
                    while month_start <= end:
                        month_end = month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])
                        facts = _window(selected, max(start, month_start), min(end, month_end)).model_dump(mode='json')
                        monthly.append({key: facts[key] for key in ('start_date', 'end_date', 'status', 'activity_count', 'active_days', 'observed_duration_seconds', 'observed_distance_by_sport_km', 'average_weekly_frequency', 'training_load', 'metric_coverage')})
                        month_start = month_end + timedelta(days=1)
                results.append({'requested_period': {'start_date': start.isoformat(), 'end_date': end.isoformat()},
                                'summary': _window(selected, start, end).model_dump(mode='json'),
                                'coverage_status': 'incomplete_import' if incomplete else ('observed_local' if selected else 'no_local_activities'),
                                'activities': [_project_activity(a) for a in shown], 'activities_omitted': len(selected)-len(shown),
                                'activities_excluded_by_selection': len(selected)-len(shown), 'activities_truncated_by_budget': 0,
                                'selected_activity_details': detail,
                                'candidate_status': ('selected_by_observed_distance' if shown else 'no_distance_candidate') if query.get('longest_activity') else 'not_requested',
                                'monthly_evolution': monthly,
                                'selection': 'longest_observed_distance_not_workout_classification' if query.get('longest_activity') else 'chronological'})
        payload = {'status': 'available', 'sport': sport, 'periods': results,
                   'provenance': scope, 'import_status': state.get('status') if scope['source'] == 'multisport' else None,
                   'budget': {'max_bytes': MAX_SEARCH_BYTES, 'activities_truncated': False, 'summary_preserves_all_activities': True}}
        while _size(payload) > MAX_SEARCH_BYTES:
            candidates = [r for r in results if r['activities']]
            if not candidates:
                raise ValueError('Historical summaries exceed context budget')
            item = max(candidates, key=lambda r: len(r['activities']))
            item['activities'].pop(0)
            item['activities_omitted'] += 1
            item['activities_truncated_by_budget'] += 1
            item['selected_activity_details'] = None
            payload['budget']['activities_truncated'] = True
        return payload
    except Exception:
        # Exception strings can contain file paths or user identifiers.
        return {'status': 'unavailable', 'error_code': 'local_history_read_failed',
                'message': "L'historique local ne peut pas être lu ; aucun résultat n'est inventé."}
