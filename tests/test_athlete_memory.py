import json
from datetime import date, datetime, timedelta, timezone

import pytest

from garmin_coach import athlete_memory


def test_memory_deduplicates_explicit_facts(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.json"
    monkeypatch.setattr(athlete_memory, "COACH_MEMORY_FILE", target)
    monkeypatch.setattr(athlete_memory, "ensure_directories", lambda: None)

    athlete_memory.remember_facts([
        "Préfère courir le mardi.",
        "Préfère courir le mardi.",
    ])

    assert [item["text"] for item in athlete_memory.load_memory()] == [
        "Préfère courir le mardi."
    ]
    stored = json.loads(target.read_text(encoding="utf-8"))
    assert stored["schema_version"] == 2


def test_legacy_memory_is_read_without_destructive_migration(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.json"
    target.write_text(
        json.dumps([{"fact": "Préfère courir le soir.", "recorded_at": "2026-10-01T10:00:00+02:00"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(athlete_memory, "COACH_MEMORY_FILE", target)

    memory = athlete_memory.load_memory()

    assert memory[0]["text"] == "Préfère courir le soir."
    assert memory[0]["source"] == "legacy_user_declared"
    assert isinstance(json.loads(target.read_text(encoding="utf-8")), list)


def test_corrupt_memory_is_safe_and_never_silently_overwritten(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.json"
    target.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(athlete_memory, "COACH_MEMORY_FILE", target)

    assert athlete_memory.load_memory() == []
    with pytest.raises(ValueError, match="store de mémoire"):
        athlete_memory.apply_memory_updates(
            [{
                "operation": "add",
                "category": "preference",
                "key": "preferred_running_time",
                "scope": "durable",
                "text": "L'utilisateur préfère courir le soir.",
                "source_excerpt": "courir le soir",
            }],
            source_message_id="message-1",
            source_text="Je préfère courir le soir",
        )
    assert target.read_text(encoding="utf-8") == "not-json"


def test_current_signal_can_be_upserted_then_resolved(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.json"
    monkeypatch.setattr(athlete_memory, "COACH_MEMORY_FILE", target)
    monkeypatch.setattr(athlete_memory, "ensure_directories", lambda: None)
    first = athlete_memory.MemoryUpdate(
        operation="upsert", category="current_signal", key="knee_discomfort",
        scope="current", text="L'utilisateur déclare une gêne légère au genou.",
        source_excerpt="gêne légère au genou",
    )
    athlete_memory.apply_memory_updates(
        [first], source_message_id="msg_one",
        source_text="J'ai une gêne légère au genou.", now="2026-10-07T01:00:00+02:00",
    )
    active = athlete_memory.load_memory(include_inactive=False)
    assert len(active) == 1

    athlete_memory.apply_memory_updates(
        [athlete_memory.MemoryUpdate(
            operation="resolve", category="current_signal", key="knee_discomfort",
            scope="current", source_excerpt="Ça va mieux maintenant",
            target_id=active[0]["id"],
        )],
        source_message_id="msg_two", source_text="Ça va mieux maintenant.",
        now="2026-10-07T02:00:00+02:00",
    )

    assert athlete_memory.load_memory(include_inactive=False) == []
    assert athlete_memory.load_memory()[0]["status"] == "resolved"


def test_memory_update_must_quote_the_user_and_rejects_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(athlete_memory, "COACH_MEMORY_FILE", tmp_path / "memory.json")
    monkeypatch.setattr(athlete_memory, "ensure_directories", lambda: None)
    update = athlete_memory.MemoryUpdate(
        operation="upsert", category="preference", key="preferred_time",
        scope="durable", text="L'utilisateur préfère courir le soir.",
        source_excerpt="courir le matin",
    )
    with pytest.raises(ValueError, match="ancrée"):
        athlete_memory.apply_memory_updates(
            [update], source_message_id="msg", source_text="Je préfère courir le soir."
        )
    with pytest.raises(ValueError, match="secret"):
        athlete_memory.MemoryUpdate(
            operation="add", category="other", key="credential_note", scope="durable",
            text="Mon mot de passe: secret123", source_excerpt="mot de passe",
        )


def test_relevant_memory_is_active_recent_and_bounded(tmp_path, monkeypatch) -> None:
    target = tmp_path / "memory.json"
    monkeypatch.setattr(athlete_memory, "COACH_MEMORY_FILE", target)
    monkeypatch.setattr(athlete_memory, "ensure_directories", lambda: None)
    now = datetime(2026, 10, 7, 2, tzinfo=timezone.utc)
    updates = [
        athlete_memory.MemoryUpdate(
            operation="upsert", category="preference", key=f"preference_{index}",
            scope="durable", text=f"L'utilisateur préfère la variante {index} le soir.",
            source_excerpt=f"variante {index}",
        )
        for index in range(20)
    ]
    source = " ".join(f"variante {index}" for index in range(20))
    athlete_memory.apply_memory_updates(
        updates, source_message_id="many", source_text=source,
        now=now.isoformat(),
    )

    selected = athlete_memory.select_relevant_memory(
        "Quand courir ?", now=now, max_items=5, max_chars=900
    )

    assert len(selected) <= 5
    assert len(json.dumps(selected, ensure_ascii=False)) < 1_200


def test_activity_trace_combines_recent_detail_with_global_averages() -> None:
    today = date.today()
    activities = [
        {
            "date": (today - timedelta(days=10)).isoformat(),
            "distanceKm": 5.0,
            "paceSeconds": 360,
        },
        {
            "date": (today - timedelta(days=120)).isoformat(),
            "distanceKm": 10.0,
            "paceSeconds": 420,
        },
    ]

    trace = athlete_memory.activity_trace(activities)

    assert trace["ensemble_historique"]["courses"] == 2
    assert trace["ensemble_historique"]["distance_moyenne_km"] == 7.5
    assert trace["derniers_28_jours"]["courses"] == 1
    assert trace["derniers_90_jours"]["courses"] == 1
