import json

from garmin_coach import ai_usage


def test_usage_is_recorded_and_summarized_atomically(tmp_path, monkeypatch) -> None:
    target = tmp_path / "ai_usage.json"
    monkeypatch.setattr(ai_usage, "USAGE_FILE", target)
    monkeypatch.setattr(ai_usage, "ensure_directories", lambda: None)
    usage = type(
        "Usage",
        (),
        {
            "input_tokens": 120,
            "input_tokens_details": type("Details", (), {"cached_tokens": 40})(),
            "output_tokens": 30,
            "total_tokens": 150,
        },
    )()
    response = type("Response", (), {"usage": usage})()

    ai_usage.record_usage(
        response,
        "conversation",
        "test-model",
        duration_seconds=1.25,
        payload_bytes=2048,
        cache_status="miss",
    )

    stored = json.loads(target.read_text(encoding="utf-8"))
    assert stored[0]["category"] == "conversation"
    assert stored[0]["cached_input_tokens"] == 40
    assert stored[0]["duration_seconds"] == 1.25
    assert stored[0]["payload_bytes"] == 2048
    assert stored[0]["cache_status"] == "hit"
    assert not target.with_suffix(".json.tmp").exists()
    assert ai_usage.usage_summary([target]) == {
        "total": 150,
        "input": 120,
        "cached_input": 40,
        "output": 30,
        "calls": 1,
        "average": 150,
        "level": "Économique",
        "categories": {
            "conversation": {
                "calls": 1,
                "input": 120,
                "cached_input": 40,
                "output": 30,
                "total": 150,
            }
        },
    }
