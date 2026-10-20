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
