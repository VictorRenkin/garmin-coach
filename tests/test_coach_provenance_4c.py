"""Import progress, local lists and measured facts remain separate at chat input."""
import json

import pytest

from garmin_coach import config, history_import
from garmin_coach.conversation_sport_context import build_conversation_sport_context
from tests.test_conversation_sport_context import isolated, seed, sent_payload


@pytest.mark.parametrize("status,pagination", [("interrupted", False), ("complete", True), ("running", False)])
def test_model_distinguishes_import_progress_from_complete_local_activity_list(monkeypatch, status, pagination):
    seed("athlete", ["running"])
    with config.user_context("athlete"):
        history_import.update_history_import({"status": status, "pagination_complete": pagination})
        before = history_import.load_history_import(detect_stale=False)
    multi = sent_payload("athlete", monkeypatch)["contexte_multisport"]
    assert multi["import_status"] == status
    assert multi["import_pagination_complete"] is pagination
    assert multi["weeks"]["last_7_days"]["activity_list_complete"] is True
    assert multi["history"]["windows"]["last_28_days"]["activity_count"] == 1
    with config.user_context("athlete"):
        assert history_import.load_history_import(detect_stale=False) == before


def test_invalid_import_remains_unavailable_not_an_empty_training_history(monkeypatch):
    seed("athlete", ["running"])
    with config.user_context("athlete"):
        history_import.update_history_import({"status": "error"})
    multi = sent_payload("athlete", monkeypatch)["contexte_multisport"]
    assert multi["status"] == "unavailable"
    assert "history" not in multi and "weeks" not in multi


def test_import_progress_does_not_change_canonical_metrics_or_numeric_labels():
    seed("athlete", ["running", "cycling"])
    with config.user_context("athlete"):
        history_import.update_history_import({"status": "interrupted", "pagination_complete": False})
    partial = build_conversation_sport_context("athlete")
    with config.user_context("athlete"):
        history_import.update_history_import({"status": "complete", "pagination_complete": True})
    complete = build_conversation_sport_context("athlete")
    assert partial["weeks"] == complete["weeks"]
    assert partial["history"] == complete["history"]
    from garmin_coach.coach_numeric_presentation import build_numeric_presentation
    assert build_numeric_presentation({"contexte_multisport": partial}) == build_numeric_presentation({"contexte_multisport": complete})


def test_no_local_activities_remains_an_observation_with_import_limit(monkeypatch):
    seed("athlete", [])
    with config.user_context("athlete"):
        history_import.update_history_import({"status": "interrupted", "pagination_complete": False})
    multi = sent_payload("athlete", monkeypatch)["contexte_multisport"]
    assert multi["import_status"] == "interrupted"
    assert multi["history"]["windows"]["last_28_days"]["activity_count"] == 0
    assert multi["history"]["source"] == "multisport"
    encoded = json.dumps(multi)
    assert "private@example.com" not in encoded and "PRIVATE TITLE" not in encoded


def test_completed_import_keeps_its_existing_bounded_scope(monkeypatch):
    from tests.test_conversation_sport_context import REFERENCE
    seed("athlete", ["running"])
    with config.user_context("athlete"):
        history_import.initialize_history_import("3_months", reference_date=REFERENCE)
        state = history_import.update_history_import({"status": "complete", "pagination_complete": True})
    multi = sent_payload("athlete", monkeypatch)["contexte_multisport"]
    assert multi["import_status"] == "complete"
    assert multi["history"]["scope"] == "3_months"
    assert multi["history"]["scope_cutoff_date"] == state["cutoff_date"]


def test_import_progress_is_scoped_to_the_active_account(monkeypatch):
    seed("alice", ["running"])
    seed("bob", ["swimming"])
    with config.user_context("alice"):
        history_import.update_history_import({"status": "interrupted", "pagination_complete": False})
    with config.user_context("bob"):
        history_import.update_history_import({"status": "complete", "pagination_complete": True})
    alice = sent_payload("alice", monkeypatch)["contexte_multisport"]
    bob = sent_payload("bob", monkeypatch)["contexte_multisport"]
    assert (alice["import_status"], alice["import_pagination_complete"]) == ("interrupted", False)
    assert (bob["import_status"], bob["import_pagination_complete"]) == ("complete", True)


@pytest.mark.parametrize("provider", ["openai", "compatible"])
def test_both_model_boundaries_receive_the_local_list_and_import_scope_rules(monkeypatch, provider):
    from dataclasses import replace
    from types import SimpleNamespace
    from garmin_coach import coach_chat
    from garmin_coach.config import Settings
    from tests.test_coach_numeric_presentation import context
    captured = {}
    response = coach_chat.CoachResponse(message="Données locales observées seulement.")
    def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_parsed=response)
    def completion(settings, **kwargs):
        captured.update(kwargs)
        return response
    monkeypatch.setattr(coach_chat, "OpenAI", lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    monkeypatch.setattr(coach_chat, "structured_completion", completion)
    monkeypatch.setattr(coach_chat, "record_usage", lambda *args, **kwargs: None)
    settings = Settings(None, None, 300, "synthetic-key", "synthetic-model")
    if provider == "compatible":
        settings = replace(settings, ai_base_url="https://synthetic.invalid/v1")
    source = context()
    source["contexte_multisport"].update(import_status="interrupted", import_pagination_complete=False)
    coach_chat.generate_response({"content": "Explique les limites des données observées."}, settings, source)
    instructions = captured["instructions"]
    assert "true ne signifie pas que l'import global est termine" in instructions
    assert "history.scope, scope_cutoff_date" in instructions
    assert "Un statut absent" in instructions and "ne prouve pas l'absence reelle" in instructions
    sent = (json.loads(captured["input"]) if provider == "openai" else captured["payload"])["contexte"]
    assert sent["contexte_multisport"]["import_status"] == "interrupted"
    assert sent["presentation_numerique"]["summaries"]["contexte_multisport.history.windows.last_28_days"]["observed_duration_seconds"] == 37800
