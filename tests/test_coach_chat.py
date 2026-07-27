import json

import pytest

from garmin_coach import coach_chat
from garmin_coach.config import Settings


def test_message_round_trip(tmp_path, monkeypatch) -> None:
    messages = tmp_path / "messages.json"
    drafts = tmp_path / "drafts"
    monkeypatch.setattr(coach_chat, "MESSAGES_FILE", messages)
    monkeypatch.setattr(coach_chat, "COACH_DRAFTS_DIR", drafts)
    monkeypatch.setattr(coach_chat, "ensure_directories", lambda: None)

    request = coach_chat.submit_message("Décale ma séance de jeudi")
    assert coach_chat.pending_requests()[0]["id"] == request["id"]

    response = tmp_path / "response.json"
    response.write_text(
        json.dumps(
            {"message": "La séance a été déplacée sans augmenter la charge.", "plan_modifie": True}
        ),
        encoding="utf-8",
    )
    answer = coach_chat.save_response(request["id"], response)

    assert answer["plan_modifie"] is True
    assert coach_chat.pending_requests() == []
    assert len(coach_chat.list_messages()) == 2


def test_proactive_question_is_added_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(coach_chat, "MESSAGES_FILE", tmp_path / "messages.json")
    monkeypatch.setattr(coach_chat, "COACH_DRAFTS_DIR", tmp_path / "drafts")
    monkeypatch.setattr(coach_chat, "ensure_directories", lambda: None)

    first = coach_chat.add_assistant_prompt("Comment te sens-tu ?", "run-42")
    second = coach_chat.add_assistant_prompt("Question en double", "run-42")

    assert first == second
    assert len(coach_chat.list_messages()) == 1


def test_generate_response_sends_context_and_uses_structured_output(monkeypatch) -> None:
    captured = {}
    expected = coach_chat.CoachResponse(
        message="Je te propose de passer à quatre séances.",
        action="update_objective",
        objective_update={"sessions_per_week": 4},
    )

    class Responses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return type("Result", (), {"output_parsed": expected})()

    class Client:
        def __init__(self, **kwargs):
            self.responses = Responses()

    monkeypatch.setattr(coach_chat, "OpenAI", Client)
    result = coach_chat.generate_response(
        {"content": "Je veux quatre séances"},
        Settings(None, None, 300, "test-key", "test-model"),
        {"objectif": {"sessionsPerWeek": 3}, "conversation_recente": []},
    )

    payload = json.loads(captured["input"])
    assert result == expected
    assert payload["demande"] == "Je veux quatre séances"
    assert payload["contexte"]["objectif"]["sessionsPerWeek"] == 3
    assert captured["text_format"] is coach_chat.CoachResponse


def test_chat_replies_immediately_without_api_key() -> None:
    response = coach_chat.generate_response(
        {"content": "La séance était difficile"},
        Settings(None, None, 300, None, "gpt-5.6-luna"),
        {"objectif": {"title": "Terminer un semi-marathon"}},
    )

    assert "difficile" in response.message
    assert "fatigue" in response.message


def test_local_coach_can_propose_an_objective_change() -> None:
    response = coach_chat.local_response(
        "Je veux préparer un 10 km", {"objectif": {"distanceKm": 21.1}}
    )

    assert response.action == "update_objective"
    assert response.objective_update is not None
    assert response.objective_update.distance_km == 10


@pytest.mark.parametrize(
    ("question", "response"),
    [
        ("Comment récupérer après ma course ?", {"message": "Récupération prudente."}),
        ("La séance était très difficile.", {"message": "As-tu une fatigue inhabituelle ?"}),
        ("J'ai une douleur au genou.", {"message": "Ne cours pas malgré cette douleur."}),
        ("Puis-je courir demain ?", {"message": "Il me manque ton niveau de fatigue."}),
        ("Prépare-moi pour un 10 km.", {"message": "Je propose le 10 km.", "action": "update_objective", "objective_update": {"title": "Préparer un 10 km", "distance_km": 10}}),
        ("Passe à quatre séances par semaine.", {"message": "Je propose quatre séances.", "action": "update_objective", "objective_update": {"sessions_per_week": 4}}),
        ("Ma course est le 2026-10-04.", {"message": "Je propose cette date.", "action": "update_objective", "objective_update": {"race_date": "2026-10-04"}}),
        ("Déplace la séance 1.", {"message": "À quelle date ?"}),
        ("Déplace la séance 0 au 2026-08-01.", {"message": "Je propose ce déplacement.", "action": "reschedule_session", "session_index": 0, "new_date": "2026-08-01"}),
        ("J'ai raté une séance.", {"message": "Souhaites-tu la reporter ?"}),
        ("L'allure était lente mais le parcours montait.", {"message": "Le relief limite la comparaison brute."}),
        ("Augmente fortement ma charge.", {"message": "Je préfère une progression prudente."}),
    ],
)
def test_common_coach_questions_have_valid_structured_responses(question, response) -> None:
    assert question
    parsed = coach_chat.CoachResponse.model_validate(response)
    assert parsed.message
