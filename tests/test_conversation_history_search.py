from garmin_coach.conversation_history_search import parse_history_request


def test_bare_month_requires_year():
    assert parse_history_request('Analyse mes entraînements de juin', '2026-10-14')['status'] == 'needs_clarification'


def test_comparison_periods_are_distinct():
    result = parse_history_request('Compare septembre 2025 et juillet 2025', '2026-10-14')
    assert result['periods'] == [{'start_date': '2025-09-01', 'end_date': '2025-09-30'}, {'start_date': '2025-07-01', 'end_date': '2025-07-31'}]


def test_contiguous_interval_and_cycling():
    result = parse_history_request('Mon volume vélo de juillet à septembre 2025', '2026-10-14')
    assert result['sport'] == 'cycling'
    assert result['periods'] == [{'start_date': '2025-07-01', 'end_date': '2025-09-30'}]


def test_previous_month_crosses_year():
    result = parse_history_request('Retrouve ma sortie longue de course du mois dernier', '2026-01-10')
    assert result['periods'] == [{'start_date': '2025-12-01', 'end_date': '2025-12-31'}]


def test_longest_requires_sport():
    assert parse_history_request('Retrouve ma sortie longue du mois dernier', '2026-01-10')['status'] == 'needs_clarification'


def test_future_and_invalid_dates_require_clarification():
    assert parse_history_request('Analyse juin 2027', '2026-10-14')['status'] == 'needs_clarification'
    assert parse_history_request('Analyse entre 2025-02-30 et 2025-03-01', '2026-10-14')['status'] == 'needs_clarification'


def test_calendar_request_is_not_historical():
    assert parse_history_request('Déplace ma sortie de course au 2026-10-20', '2026-10-14')['status'] == 'not_requested'


def test_other_sports_are_not_silently_all_sports():
    assert parse_history_request('Analyse mes activités football en juin 2025', '2026-10-14')['sport'] == 'football'
    assert parse_history_request('Analyse mes activités padel en juin 2025', '2026-10-14')['sport'] == 'padel'
    assert parse_history_request('Analyse mes activités rugby en juin 2025', '2026-10-14')['status'] == 'needs_clarification'
