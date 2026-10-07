import json
from datetime import datetime

import pytest

from garmin_coach import athlete_memory, coach_chat, config, dashboard
from garmin_coach.config import Settings, user_context


def _update(
    *, operation="upsert", category="current_signal", key="fatigue_current",
    scope="current", text="L'utilisateur déclare une fatigue actuelle.",
    excerpt="je me sens fatigué", target_id=None,
):
    return athlete_memory.MemoryUpdate(
        operation=operation,
        category=category,
        key=key,
        scope=scope,
        text=text,
        source_excerpt=excerpt,
        target_id=target_id,
    )


def test_one_ai_call_returns_answer_and_durable_memory(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(athlete_memory, "COACH_MEMORY_FILE", tmp_path / "memory.json")
    expected = coach_chat.CoachResponse(
        message="Je le garderai en tête pour les prochains entraînements.",
        memory_updates=[_update(
            category="preference", key="preferred_running_time", scope="durable",
            text="L'utilisateur préfère courir le soir.",
            excerpt="Je préfère courir le soir",
        )],
    )
    calls = []

    class Responses:
        def parse(self, **kwargs):
            calls.append(kwargs)
            return type("Result", (), {"output_parsed": expected, "usage": None})()

    class Client:
        def __init__(self, **kwargs):
            self.responses = Responses()

    monkeypatch.setattr(coach_chat, "OpenAI", Client)
    response = coach_chat.generate_response(
        {"id": "message-1", "content": "Je préfère courir le soir"},
        Settings(None, None, 300, "test-key", "test-model"),
        {"memoire_pertinente": [], "conversation_recente": []},
    )
    athlete_memory.apply_memory_updates(
        response.memory_updates,
        source_message_id="message-1",
        source_text="Je préfère courir le soir",
    )

    assert len(calls) == 1
    assert athlete_memory.load_memory(include_inactive=False)[0]["scope"] == "durable"


def test_current_signal_is_resolved_without_diagnosis(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(athlete_memory, "COACH_MEMORY_FILE", tmp_path / "memory.json")
    created = athlete_memory.apply_memory_updates(
        [_update(
            key="feeling_unwell", text="L'utilisateur déclare commencer à ne pas se sentir bien.",
            excerpt="Depuis hier je commence à ne pas me sentir bien",
        )],
        source_message_id="message-1",
        source_text="Depuis hier je commence à ne pas me sentir bien",
    )
    athlete_memory.apply_memory_updates(
        [_update(
            operation="resolve", key="feeling_unwell", text=None,
            excerpt="Ça va mieux maintenant", target_id=created[-1]["id"],
        )],
        source_message_id="message-2",
        source_text="Ça va mieux maintenant",
    )

    assert athlete_memory.load_memory(include_inactive=False) == []
    assert "infection" not in json.dumps(athlete_memory.load_memory()).casefold()


def test_ambiguous_injury_can_require_one_clarification() -> None:
    response = coach_chat.CoachResponse(
        message="Est-ce que la gêne augmente quand tu cours doucement ?",
        conversation_state="needs_clarification",
        clarification_topic="current_pain",
        action="none",
    )
    assert response.conversation_state == "needs_clarification"
    assert response.objective_update is None


def test_active_medical_prohibition_blocks_training_action() -> None:
    response = coach_chat.CoachResponse(
        message="Je prépare la suite.", action="adapt_plan", plan_adjustment="lighter"
    )
    with pytest.raises(ValueError, match="contrainte déclarée active"):
        coach_chat._validate_conversation_output(
            response,
            {"content": "Je veux continuer"},
            {"memoire_pertinente": [{"key": "medical_no_running"}]},
        )
    frequency = coach_chat.CoachResponse(
        message="Je propose trois séances.",
        action="update_objective",
        objective_update={"sessions_per_week": 3},
    )
    with pytest.raises(ValueError, match="contrainte déclarée active"):
        coach_chat._validate_conversation_output(
            frequency,
            {"content": "Je veux trois séances malgré tout"},
            {"memoire_pertinente": [{"key": "medical_no_running"}]},
        )


def test_availability_correction_supersedes_without_applying_decision(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(athlete_memory, "COACH_MEMORY_FILE", tmp_path / "memory.json")
    first = athlete_memory.apply_memory_updates(
        [_update(
            category="availability", key="wednesday_availability", scope="durable",
            text="L'utilisateur ne peut pas courir le mercredi.",
            excerpt="Je ne peux pas courir le mercredi",
        )],
        source_message_id="message-1", source_text="Je ne peux pas courir le mercredi",
    )[-1]
    athlete_memory.apply_memory_updates(
        [_update(
            category="availability", key="wednesday_availability", scope="durable",
            text="L'utilisateur peut courir le mercredi.",
            excerpt="En fait je peux courir le mercredi maintenant",
        )],
        source_message_id="message-2",
        source_text="En fait je peux courir le mercredi maintenant",
    )

    active = athlete_memory.load_memory(include_inactive=False)
    assert len(active) == 1 and "peut courir" in active[0]["text"]
    assert next(item for item in athlete_memory.load_memory() if item["id"] == first["id"])["status"] == "superseded"
    assert not (tmp_path / "motionest_inputs_v1.json").exists()


def test_mixed_calendar_request_is_not_short_circuited() -> None:
    assert coach_chat._has_additional_context(
        "Déplace ma séance de jeudi. Depuis hier je me sens aussi très fatigué."
    )


def test_context_keeps_eight_recent_messages_and_bounded_memory(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(coach_chat, "MESSAGES_FILE", tmp_path / "messages.json")
    messages = [
        {"id": str(index), "role": "user" if index % 2 else "assistant", "content": f"message {index}", "status": "complete"}
        for index in range(50)
    ]
    (tmp_path / "messages.json").write_text(json.dumps(messages), encoding="utf-8")
    monkeypatch.setattr(dashboard, "load_public_activities", lambda: [])
    monkeypatch.setattr(dashboard, "load_relevant_plan", lambda: None)
    monkeypatch.setattr(dashboard, "current_user_id", lambda: None)
    monkeypatch.setattr(dashboard, "load_objective", lambda: {})
    monkeypatch.setattr(dashboard, "load_onboarding", lambda: {})
    monkeypatch.setattr(
        dashboard, "select_relevant_memory",
        lambda query: [{"text": "préférence ancienne conservée"}],
    )

    context = dashboard.coach_context()
    encoded = json.dumps(context, ensure_ascii=False)

    assert len(context["conversation_recente"]) == 8
    assert context["conversation_recente"][0]["content"] == "message 42"
    assert context["memoire_pertinente"][0]["text"] == "préférence ancienne conservée"
    assert len(encoded.encode("utf-8")) < 10_000


def test_memory_is_isolated_between_two_users(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    with user_context("athlete_a"):
        athlete_memory.apply_memory_updates(
            [_update(
                category="preference", key="preferred_running_time", scope="durable",
                text="L'utilisateur préfère courir le soir.",
                excerpt="Je préfère courir le soir",
            )],
            source_message_id="message-a", source_text="Je préfère courir le soir",
        )
    with user_context("athlete_b"):
        assert athlete_memory.load_memory() == []
        athlete_memory.apply_memory_updates(
            [_update(
                category="preference", key="preferred_running_time", scope="durable",
                text="L'utilisateur préfère courir le matin.",
                excerpt="Je préfère courir le matin",
            )],
            source_message_id="message-b", source_text="Je préfère courir le matin",
        )
    with user_context("athlete_a"):
        assert athlete_memory.load_memory(include_inactive=False)[0]["text"].endswith("soir.")
    with user_context("athlete_b"):
        assert athlete_memory.load_memory(include_inactive=False)[0]["text"].endswith("matin.")


def test_retry_reuses_failed_message_without_duplication(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(coach_chat, "MESSAGES_FILE", tmp_path / "messages.json")
    monkeypatch.setattr(coach_chat, "COACH_DRAFTS_DIR", tmp_path / "drafts")
    monkeypatch.setattr(coach_chat, "ensure_directories", lambda: None)
    request = coach_chat.submit_message("Peux-tu m'aider ?")
    coach_chat.mark_request_failed(request["id"])

    retried = coach_chat.retry_request(request["id"])

    assert retried["status"] == "pending"
    assert retried["retry_count"] == 1
    assert len(coach_chat.list_messages()) == 1
