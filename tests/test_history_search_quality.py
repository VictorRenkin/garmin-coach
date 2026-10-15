"""Independent adversarial checks for bounded local coach history retrieval."""
import json
from datetime import date

import pytest

from garmin_coach import config, history_import, history_storage
from garmin_coach import conversation_history_search as search


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / "secrets")


def seed(user="alice", scope="all", count=1, sport="running", day="2026-07-01"):
    with config.user_context(user):
        history_import.initialize_history_import(scope, reference_date=date(2026, 9, 30))
        for index in range(count):
            history_storage.save_activity_metadata({
                "activityId": f"PRIVATE-ID-{user}-{index}",
                "activityName": "SECRET PRIVATE TITLE", "ownerEmail": "secret@example.com",
                "latitude": 50.123, "longitude": 4.123, "accessToken": "SECRET TOKEN",
                "activityType": {"typeKey": sport}, "startTimeLocal": day + "T12:00:00",
                "distance": 5000, "movingDuration": 1800,
            })


def query(text="Analyse mes activités en juillet 2026", reference="2026-09-30"):
    return search.parse_history_request(text, reference)


@pytest.mark.parametrize("text", ["Analyse mes activités en juin", "Compare juillet et septembre", "Analyse mes activités en février 2027", "Analyse du 2026-09-10 au 2026-09-01", "Analyse du 2024-01-01 au 2026-01-01"])
def test_ambiguous_future_or_invalid_period_is_not_silently_inferred(text):
    assert query(text)["status"] == "needs_clarification"


def test_previous_month_crosses_year_boundary():
    result = query("Bilan du mois précédent", "2026-01-14")
    assert result["periods"] == [{"start_date": "2025-12-01", "end_date": "2025-12-31"}]


def test_calendar_comparison_does_not_substitute_rolling_windows():
    result = query("Compare mon volume de juillet et septembre 2026")
    assert result["periods"] == [
        {"start_date": "2026-07-01", "end_date": "2026-07-31"},
        {"start_date": "2026-09-01", "end_date": "2026-09-30"},
    ]


def test_scheduling_is_not_history_retrieval():
    assert query("Programme mes entraînements en juillet 2026")["status"] == "not_requested"


@pytest.mark.parametrize("sport", ["cycling", "biking", "indoor_cycling"])
def test_cycling_aliases_use_existing_normalization(sport):
    seed(sport=sport)
    result = search.search_conversation_history("alice", query("Analyse mes activités vélo en juillet 2026"))
    assert result["periods"][0]["summary"]["activity_count"] == 1


def test_sport_clarification_replaces_ambiguous_selection(monkeypatch):
    from garmin_coach import coach_chat
    from garmin_coach.config import Settings
    captured = {}
    class Responses:
        def parse(self, **kwargs):
            captured.update(json.loads(kwargs["input"])["contexte"])
            return type("Result", (), {"output_parsed": coach_chat.CoachResponse(message="ok")})()
    class Client:
        def __init__(self, **kwargs):
            self.responses = Responses()
    monkeypatch.setattr(coach_chat, "OpenAI", Client)
    monkeypatch.setattr(coach_chat, "record_usage", lambda *args, **kwargs: None)
    seed(sport="cycling", day="2025-06-15")
    context = {"contexte_multisport": {"reference_date": "2026-09-30"}, "conversation_recente": [
        {"role": "user", "content": "Analyse vélo et natation en juin 2025"},
        {"role": "assistant", "content": "Quel sport souhaites-tu analyser ?"},
    ]}
    with config.user_context("alice"):
        response = coach_chat.generate_response({"content": "vélo"}, Settings(None, None, 300, "fake", "test"), context)
    assert response.conversation_state != "needs_clarification"
    assert captured["recherche_historique"]["sport"] == "cycling"


def test_period_boundaries_are_inclusive():
    seed(day="2026-07-01")
    with config.user_context("alice"):
        for day in ["2026-06-30", "2026-07-31", "2026-08-01"]:
            history_storage.save_activity_metadata({"activityId": day, "activityType": {"typeKey": "running"}, "startTimeLocal": day + "T12:00:00", "distance": 1000})
    period = search.search_conversation_history("alice", query())["periods"][0]
    assert period["summary"]["activity_count"] == 2
    assert [a["date"] for a in period["activities"]] == ["2026-07-01", "2026-07-31"]


def test_user_isolation_and_restoration():
    seed("alice", count=2)
    seed("bob", count=3, sport="swimming")
    with config.user_context("bob"):
        before = config.current_user_id()
        result = search.search_conversation_history("alice", query())
        assert config.current_user_id() == before
    assert result["periods"][0]["summary"]["activity_count"] == 2
    assert {a["sport"] for a in result["periods"][0]["activities"]} == {"running"}


def test_payload_privacy_allowlist():
    seed()
    serialized = json.dumps(search.search_conversation_history("alice", query()))
    for secret in ["PRIVATE-ID", "SECRET PRIVATE TITLE", "secret@example.com", "latitude", "longitude", "SECRET TOKEN", "accessToken"]:
        assert secret not in serialized


def test_old_details_are_never_opened_or_serialized(monkeypatch):
    seed(day="2024-07-01")
    from garmin_coach import local_activity_history
    def forbidden(*args, **kwargs):
        pytest.fail("Raw detail read must not occur for metadata history")
    monkeypatch.setattr(local_activity_history, "load_local_activity_details", forbidden)
    result = search.search_conversation_history("alice", query("Analyse juillet 2024"))
    assert result["periods"][0]["summary"]["activity_count"] == 1


def test_outside_import_is_explicit():
    seed(scope="3_months", day="2024-07-01")
    period = search.search_conversation_history("alice", query("Analyse juillet 2024"))["periods"][0]
    assert period["summary"]["activity_count"] == 0
    assert period["coverage_status"] == "incomplete_import"


def test_initialized_unfinished_import_marks_partial():
    seed()
    result = search.search_conversation_history("alice", query())
    assert result["periods"][0]["coverage_status"] == "incomplete_import"


def test_absent_import_is_empty_not_fabricated():
    result = search.search_conversation_history("alice", query())
    assert result["periods"][0]["summary"]["activity_count"] == 0
    assert result["periods"][0]["coverage_status"] == "no_local_activities"


def test_reader_error_does_not_fallback_or_disclose_exception(monkeypatch):
    seed()
    def fail(**kwargs):
        raise RuntimeError("secret@example.com PRIVATE-ID")
    monkeypatch.setattr(search, "iter_local_activity_history", fail)
    result = search.search_conversation_history("alice", query())
    assert result["status"] == "unavailable"
    assert result["error_code"] == "local_history_read_failed"
    assert "secret@example.com" not in json.dumps(result)
    assert "periods" not in result


def test_total_utf8_budget_preserves_full_canonical_counts():
    seed(count=100)
    result = search.search_conversation_history("alice", query())
    size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert size <= result["budget"]["max_bytes"] == 24000
    assert result["budget"]["activities_truncated"] is True
    period = result["periods"][0]
    assert period["summary"]["activity_count"] == 100
    assert len(period["activities"]) + period["activities_omitted"] == 100


def test_impossible_budget_returns_explicit_unavailable(monkeypatch):
    seed()
    monkeypatch.setattr(search, "MAX_SEARCH_BYTES", 1)
    assert search.search_conversation_history("alice", query())["status"] == "unavailable"


def test_selected_raw_details_are_whitelisted_and_bounded(monkeypatch):
    seed(count=3)
    calls = []
    def details(activity_id):
        calls.append(activity_id)
        return {"metadata": {"ownerEmail": "secret@example.com"}, "details": {"endpoints": {
            "get_activity_hr_in_timezones": {"data": [{"zoneNumber": 2, "secsInZone": 60, "ownerEmail": "secret@example.com", "latitude": 50.123}] * 12},
            "get_activity_split_summaries": {"data": {"splitSummaries": [{"duration": 120, "distance": 500, "activityName": "SECRET PRIVATE TITLE", "longitude": 4.123}] * 12}},
            "get_activity_details": {"data": {"gps": "SECRET GPS", "accessToken": "SECRET TOKEN"}},
        }}}
    monkeypatch.setattr(search, "load_local_activity_with_details", details)
    result = search.search_conversation_history("alice", query("Analyse ma sortie longue de course en juillet 2026"))
    assert len(calls) == 1
    period = result["periods"][0]
    assert period["summary"]["activity_count"] == 3
    assert period["activities_excluded_by_selection"] == 2
    assert period["activities_truncated_by_budget"] == 0
    projected = period["selected_activity_details"]
    assert projected["status"] == "available"
    for endpoint in projected["endpoints"].values():
        assert len(endpoint["rows"]) == 10
        assert endpoint["rows_omitted"] == 2
    assert projected["endpoints"]["get_activity_hr_in_timezones"]["rows"][0]["secsInZone"] == 60
    for secret in ["secret@example.com", "latitude", "longitude", "SECRET PRIVATE TITLE", "SECRET GPS", "SECRET TOKEN", "PRIVATE-ID"]:
        assert secret not in json.dumps(result)


def test_detail_exception_retains_observed_metadata_without_private_error(monkeypatch):
    seed()
    def fail(*args):
        raise ValueError("secret@example.com SECRET TOKEN")
    monkeypatch.setattr(search, "load_local_activity_with_details", fail)
    result = search.search_conversation_history("alice", query("Analyse ma sortie longue de course en juillet 2026"))
    assert result["status"] == "available"
    assert result["periods"][0]["summary"]["activity_count"] == 1
    assert result["periods"][0]["selected_activity_details"]["status"] == "unavailable"
    assert "secret@example.com" not in json.dumps(result)


def test_evolution_retains_empty_month_and_exact_distances():
    seed(day="2025-07-15", sport="cycling")
    with config.user_context("alice"):
        for ident, day, distance in [("july", "2025-07-20", 35000), ("september", "2025-09-20", 90000)]:
            history_storage.save_activity_metadata({"activityId": ident, "activityType": {"typeKey": "cycling"}, "startTimeLocal": day + "T12:00:00", "distance": distance, "movingDuration": 1800})
    result = search.search_conversation_history("alice", query("Analyse l'évolution de mon volume vélo de juillet à septembre 2025"))
    monthly = result["periods"][0]["monthly_evolution"]
    assert [m["observed_distance_by_sport_km"].get("cycling", 0) for m in monthly] == [40, 0, 90]
    assert [m["activity_count"] for m in monthly] == [2, 0, 1]
    assert [(m["start_date"], m["end_date"]) for m in monthly] == [("2025-07-01", "2025-07-31"), ("2025-08-01", "2025-08-31"), ("2025-09-01", "2025-09-30")]


def test_evolution_monthly_bounds_are_clipped_to_requested_period():
    seed(day="2025-07-15", sport="cycling")
    result = search.search_conversation_history("alice", query("Analyse l'évolution vélo du 2025-07-15 au 2025-09-10"))
    monthly = result["periods"][0]["monthly_evolution"]
    assert monthly[0]["start_date"] == "2025-07-15"
    assert monthly[-1]["end_date"] == "2025-09-10"
    assert len(monthly) == 3


def test_missing_distance_never_selects_arbitrary_longest(monkeypatch):
    with config.user_context("alice"):
        history_import.initialize_history_import("all", reference_date=date(2026, 9, 30))
        history_storage.save_activity_metadata({"activityId": "PRIVATE-ID-alice-0", "activityType": {"typeKey": "running"}, "startTimeLocal": "2026-07-01T12:00:00", "movingDuration": 1800})
    def forbidden(*args):
        pytest.fail("No measured distance means no longest detail candidate")
    monkeypatch.setattr(search, "load_local_activity_with_details", forbidden)
    result = search.search_conversation_history("alice", query("Analyse ma sortie longue de course en juillet 2026"))
    period = result["periods"][0]
    assert period["candidate_status"] == "no_distance_candidate"
    assert period["activities"] == []
    assert period["selected_activity_details"] is None


def test_clarification_chain_preserves_requested_sport_and_period(monkeypatch):
    from garmin_coach import coach_chat
    from garmin_coach.config import Settings
    captured = {}
    class Responses:
        def parse(self, **kwargs):
            captured.update(json.loads(kwargs["input"])["contexte"])
            return type("Result", (), {"output_parsed": coach_chat.CoachResponse(message="ok")})()
    class Client:
        def __init__(self, **kwargs):
            self.responses = Responses()
    monkeypatch.setattr(coach_chat, "OpenAI", Client)
    monkeypatch.setattr(coach_chat, "record_usage", lambda *args, **kwargs: None)
    seed(sport="cycling", day="2025-06-15")
    context = {"contexte_multisport": {"reference_date": "2026-09-30"}, "conversation_recente": [
        {"role": "user", "content": "Analyse mon volume vélo"},
        {"role": "assistant", "content": "Quelle période souhaites-tu analyser ? Précise les mois et l’année, ou les dates."},
        {"role": "user", "content": "juin"},
        {"role": "assistant", "content": "De quelle année parles-tu pour cette période historique ?"},
    ]}
    with config.user_context("alice"):
        coach_chat.generate_response({"content": "2025"}, Settings(None, None, 300, "fake", "test"), context)
    result = captured["recherche_historique"]
    assert result["sport"] == "cycling"
    assert result["periods"][0]["requested_period"] == {"start_date": "2025-06-01", "end_date": "2025-06-30"}
    assert result["periods"][0]["summary"]["activity_count"] == 1
