from pydantic import BaseModel

from garmin_coach import ai_provider
from garmin_coach.config import Settings


class Result(BaseModel):
    message: str


def test_compatible_provider_uses_chat_completions_and_validates_json(monkeypatch) -> None:
    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            message = type("Message", (), {"content": '```json\n{"message":"Prêt"}\n```'})()
            return type("Completion", (), {"choices": [type("Choice", (), {"message": message})()]})()

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = type("Chat", (), {"completions": Completions()})()

    monkeypatch.setattr(ai_provider, "OpenAI", Client)
    settings = Settings(
        None,
        None,
        300,
        "provider-key",
        "provider/model",
        "nvidia",
        "https://example.test/v1",
    )

    result = ai_provider.structured_completion(
        settings,
        instructions="Réponds brièvement.",
        payload={"course": {"distance_km": 5}},
        result_type=Result,
    )

    assert result.message == "Prêt"
    assert captured["model"] == "provider/model"
    assert captured["client"]["base_url"] == "https://example.test/v1"
    assert "distance_km" in captured["messages"][1]["content"]


def test_json_extraction_accepts_text_around_object() -> None:
    assert ai_provider._json_from_text('Résultat : {"message":"ok"}') == {
        "message": "ok"
    }


def test_openai_routing_reserves_configured_model_for_deep_work() -> None:
    settings = Settings(None, None, 300, "key", "gpt-5.6-sol", "openai", None)

    assert ai_provider.routed_openai_model(settings, "economy") == "gpt-5.6-luna"
    assert ai_provider.routed_openai_model(settings, "balanced") == "gpt-5.6-terra"
    assert ai_provider.routed_openai_model(settings, "deep") == "gpt-5.6-sol"
