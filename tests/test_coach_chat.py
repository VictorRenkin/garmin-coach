import json

import pytest

from garmin_coach import coach_chat
from garmin_coach.config import Settings


def test_exact_current_plan_question_is_grounded_without_api() -> None:
    result = coach_chat.generate_response(
        {"content": "Quelle seance est actuellement enregistree jeudi dans mon plan actif ?"},
        Settings(None, None, 300, None, "unused"),
        {
            "plan_actif": {
                "seances": [
                    {
                        "date": "2026-08-06",
                        "titre": "Endurance reguliere",
                        "type": "endurance facile",
                        "duree_estimee_minutes": 35,
                        "distance_estimee_km": 4.0,
                        "consigne_intensite": "Rester regulier.",
                    }
                ]
            },
            "conversation_recente": [
                {"role": "assistant", "content": "Intervalles 10 km jeudi"}
            ],
        },
    )

    assert "Endurance reguliere" in result.message
    assert "Intervalles" not in result.message
    assert result.action == "none"


def test_modify_session_requires_a_structured_patch() -> None:
    response = coach_chat.CoachResponse(
        message="Proposition concrete.",
        action="modify_session",
        session_index=1,
        session_patch={"titre": "Intervalles courts", "type": "intervalles"},
    )
    assert response.session_patch is not None
    assert response.session_patch.type == "intervalles"


def test_explicit_reschedule_is_confirmable_without_ai() -> None:
    result = coach_chat.generate_response(
        {"content": "Deplace la seance 1 au 2026-08-10"},
        Settings(None, None, 300, None, "unused"),
        {
            "plan_actif": {
                "seances": [{"date": "2026-08-06", "titre": "Endurance"}]
            }
        },
    )
    assert result.action == "reschedule_session"
    assert result.session_index == 0
    assert result.new_date == "2026-08-10"


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
    assert result == expected.model_copy(update={"source": "openai"})
    assert result.source == "openai"
    assert payload["demande"] == "Je veux quatre séances"
    assert payload["contexte"]["objectif"]["sessionsPerWeek"] == 3
    assert captured["text_format"] is coach_chat.CoachResponse


def test_delete_sessions_action_keeps_all_selected_indexes() -> None:
    response = coach_chat.CoachResponse(
        message="Je retire les autres séances après ton clic.",
        action="delete_sessions",
        session_indexes=[0, 1, 3, 4],
    )

    assert response.session_indexes == [0, 1, 3, 4]


def test_delete_sessions_action_requires_at_least_one_index() -> None:
    with pytest.raises(ValueError, match="séance à retirer"):
        coach_chat.CoachResponse(
            message="Je retire les séances.", action="delete_sessions"
        )


def test_chat_requires_ai_connection_instead_of_automatic_reply() -> None:
    with pytest.raises(RuntimeError, match="réponses automatiques est désactivé"):
        coach_chat.generate_response(
            {"content": "La séance était difficile"},
            Settings(None, None, 300, None, "gpt-5.6-sol"),
            {"objectif": {"title": "Terminer un semi-marathon"}},
        )


def test_chat_reports_ai_failure_instead_of_automatic_reply(monkeypatch) -> None:
    class FailingClient:
        def __init__(self, **kwargs):
            raise TimeoutError("API indisponible")

    monkeypatch.setattr(coach_chat, "OpenAI", FailingClient)
    with pytest.raises(RuntimeError, match="temporairement indisponible"):
        coach_chat.generate_response(
            {"content": "Bonjour"},
            Settings(None, None, 300, "test-key", "gpt-5.6-sol"),
            {"objectif": {"title": "Préparer un 10 km"}},
        )


def test_local_coach_can_propose_an_objective_change() -> None:
    response = coach_chat.local_response(
        "Je veux préparer un 10 km", {"objectif": {"distanceKm": 21.1}}
    )

    assert response.action == "update_objective"
    assert response.objective_update is not None
    assert response.objective_update.distance_km == 10


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Réduis la charge, je veux une semaine plus légère", "lighter"),
        ("Je veux une semaine de récupération", "recovery"),
        ("Augmente la charge progressivement", "progressive"),
    ],
)
def test_local_coach_proposes_real_plan_adaptations(question, expected) -> None:
    response = coach_chat.local_response(question, {"plan_actif": {"seances": [{}]}})

    assert response.action == "adapt_plan"
    assert response.plan_adjustment == expected
    assert "Confirme" in response.message


def test_local_coach_uses_previous_question_to_adapt_the_plan() -> None:
    context = {
        "conversation_recente": [
            {
                "role": "assistant",
                "content": "As-tu seulement une fatigue générale, ou aussi une douleur inhabituelle ?",
            },
            {"role": "user", "content": "Fatigue seulement, pas de douleur"},
        ]
    }

    response = coach_chat.local_response("Fatigue seulement, pas de douleur", context)

    assert response.action == "adapt_plan"
    assert response.plan_adjustment == "lighter"
    assert "sans douleur" in response.message


@pytest.mark.parametrize(
    ("question", "expected_fragment", "expected_action"),
    [
        ("Bonjour", "Bonjour", "none"),
        ("Que peux-tu faire ?", "comparer tes courses", "none"),
        ("Comment était ma dernière course ?", "8.2 km", "none"),
        ("La séance était facile", "effort facile", "none"),
        ("La séance était normale", "Ressenti normal", "none"),
        ("La séance était très difficile", "fatigue générale", "none"),
        ("J'ai une douleur au genou", "Ne cours pas", "none"),
        ("J'ai une douleur à la poitrine", "arrêter l'effort", "none"),
        ("Je veux préparer un 10 km", "10 km", "update_objective"),
        ("Passe à 4 séances par semaine", "4 séances", "update_objective"),
        ("Ma course est le 2026-10-04", "2026-10-04", "update_objective"),
        ("Reporte la séance 2 au 2026-08-04", "déplacer", "reschedule_session"),
        ("Je ne peux pas courir jeudi", "numéro de la séance", "none"),
        ("J'ai raté une séance", "rattraper", "none"),
        ("Réduis la charge cette semaine", "alléger", "adapt_plan"),
        ("Augmente la charge de 20 %", "progression prudente", "adapt_plan"),
        ("Je dors mal depuis trois jours", "sommeil", "none"),
        ("Je pars en vacances", "dates d'absence", "none"),
        ("Mon allure était de 22 min/km", "mesure erronée", "none"),
        ("Pourquoi mon cardio monte ?", "chaleur", "none"),
        ("Merci", "Avec plaisir", "none"),
    ],
)
def test_local_coach_reacts_to_realistic_questions(
    question, expected_fragment, expected_action
) -> None:
    context = {
        "objectif": {"title": "Terminer un semi-marathon"},
        "plan_actif": {
            "seances": [
                {"date": "2026-07-28", "titre": "Endurance facile"},
                {"date": "2026-07-30", "titre": "Intervalles"},
            ]
        },
        "courses_recentes": [{"distanceKm": 8.2, "paceSeconds": 360}],
    }

    response = coach_chat.local_response(question, context)

    assert expected_fragment in response.message
    assert response.action == expected_action


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
