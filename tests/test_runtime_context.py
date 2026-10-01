from __future__ import annotations

import http.client
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from garmin_coach import (
    ai_provider,
    config,
    dashboard,
    garmin,
    garmin_workouts,
    history_import,
    history_storage,
    runtime_context,
    user_accounts,
)
from garmin_coach.availability import build_availability_v1
from garmin_coach.goal import build_goal_v1
from garmin_coach.subjective_context import build_subjective_context_v1


REFERENCE = date(2026, 9, 30)


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    monkeypatch.setattr(user_accounts, "SESSION_KEY_FILE", tmp_path / "session.key")
    return tmp_path


def legacy_objective(**updates: Any) -> dict[str, Any]:
    result = {
        "goalType": "race",
        "title": "Private race title",
        "distanceKm": 21.0975,
        "sessionsPerWeek": 3,
        "raceDate": "2027-04-18",
        "goal_target_time": "02:00:00",
        "availableDays": ["tue", "thu", "sat"],
        "constraints": "Private unmapped constraint",
        "injuryPain": "non",
        "preferences": "Private declared preference",
    }
    result.update(updates)
    return result


def write_profile(user_id: str, objective: dict[str, Any]) -> None:
    with config.user_context(user_id):
        config.PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.PROFILE_FILE.write_text(
            json.dumps({"objective": objective}), encoding="utf-8"
        )


def prepare_history(user_id: str, count: int) -> None:
    with config.user_context(user_id):
        history_import.initialize_history_import("all", reference_date=REFERENCE)
        for index in range(count):
            day = REFERENCE - timedelta(days=index * 4)
            history_storage.save_activity_metadata(
                {
                    "activityId": f"{user_id}-{index}",
                    "activityName": f"Private activity {index}",
                    "activityType": {
                        "typeKey": "cycling" if index % 5 == 0 else "running"
                    },
                    "startTimeLocal": f"{day.isoformat()}T08:00:00",
                    "distance": 15000 if index % 5 == 0 else 6000 + index * 100,
                    "movingDuration": 2400,
                    "averageHR": 145,
                    "maxHR": 172,
                    "activityTrainingLoad": 70,
                    "hrTimeInZone_1": 200,
                    "hrTimeInZone_2": 1200,
                    "aerobicTrainingEffect": 2.8,
                    "anaerobicTrainingEffect": 0.0,
                }
            )


def build(user_id: str) -> runtime_context.RuntimeMotionestContextV1:
    return runtime_context.build_runtime_motionest_context(
        user_id, reference_date=REFERENCE
    )


def test_runtime_handles_empty_limited_and_rich_history() -> None:
    statuses: dict[int, tuple[str, str, int]] = {}
    for count in (0, 1, 18):
        user_id = f"u_history_{count}"
        write_profile(user_id, legacy_objective())
        prepare_history(user_id, count)
        result = build(user_id)
        statuses[count] = (
            result.context.athlete_state.status,
            result.context.integration.status,
            result.context.training_history_summary.activity_count,
        )
        assert result.reference_date == REFERENCE.isoformat()
        assert result.context.as_of == REFERENCE.isoformat()
        assert result.context.integration.blocking_issues == []

    assert statuses[0] == ("insufficient_data", "partial", 0)
    assert statuses[1][0] == "partial"
    assert statuses[1][2] == 1
    assert statuses[18][0] == "available"
    assert statuses[18][2] == 18


def test_missing_profile_produces_explicit_contract_blockers() -> None:
    result = build("u_missing")

    assert result.inputs_source == "legacy_profile"
    assert [item.code for item in result.input_diagnostics] == [
        "legacy_profile_missing"
    ]
    assert result.context.integration.status == "insufficient"
    assert {item.code for item in result.context.integration.blocking_issues} == {
        "goal_absent",
        "availability_absent",
    }


@pytest.mark.parametrize(
    "stored_payload",
    [
        '{"schema_version": 999, "secret": "must-stay-local"}',
        '{"schema_version": 1, "broken": ',
    ],
)
def test_invalid_or_corrupt_contract_store_is_safe_and_not_overwritten(
    stored_payload: str,
) -> None:
    user_id = "u_corrupt"
    write_profile(user_id, legacy_objective())
    with config.user_context(user_id):
        config.MOTIONEST_INPUTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.MOTIONEST_INPUTS_FILE.write_text(stored_payload, encoding="utf-8")
        before = config.MOTIONEST_INPUTS_FILE.read_text(encoding="utf-8")

    result = build(user_id)

    assert result.inputs_source == "safe_fallback"
    assert result.input_diagnostics[0].code == "motionest_inputs_store_invalid"
    assert result.context.integration.status == "insufficient"
    with config.user_context(user_id):
        assert config.MOTIONEST_INPUTS_FILE.read_text(encoding="utf-8") == before


def test_missing_legacy_source_never_exposes_a_stale_migrated_context() -> None:
    user_id = "u_stale_legacy"
    write_profile(user_id, legacy_objective())
    initial = build(user_id)
    assert initial.context.goal is not None
    with config.user_context(user_id):
        config.PROFILE_FILE.unlink()
        persisted = config.MOTIONEST_INPUTS_FILE.read_text(encoding="utf-8")

    current = build(user_id)

    assert current.inputs_source == "safe_fallback"
    assert current.context.goal is None
    assert current.context.integration.status == "insufficient"
    assert [item.code for item in current.input_diagnostics] == [
        "legacy_profile_missing"
    ]
    with config.user_context(user_id):
        assert config.MOTIONEST_INPUTS_FILE.read_text(encoding="utf-8") == persisted


def test_legacy_migration_is_idempotent_and_exposes_unmapped_diagnostics() -> None:
    user_id = "u_migration"
    write_profile(user_id, legacy_objective())

    first = build(user_id)
    with config.user_context(user_id):
        persisted_first = config.MOTIONEST_INPUTS_FILE.read_text(encoding="utf-8")
    second = build(user_id)
    with config.user_context(user_id):
        persisted_second = config.MOTIONEST_INPUTS_FILE.read_text(encoding="utf-8")

    assert persisted_first == persisted_second
    assert first == second
    assert first.context.availability is not None
    assert first.context.availability.preferred_sessions_per_week == 3
    assert "confirmed_sessions_per_week" not in persisted_first
    assert {
        (item.contract, item.code, item.field)
        for item in first.input_diagnostics
    } >= {
        ("goal", "legacy_title_unmapped", "title"),
        ("subjective_context", "legacy_constraint_unmapped", "constraints"),
        ("subjective_context", "legacy_preference_preserved", "preferences"),
    }
    assert "Private unmapped constraint" not in first.model_dump_json()


def test_legacy_declaration_change_refreshes_inputs_and_context() -> None:
    user_id = "u_refresh"
    write_profile(user_id, legacy_objective())
    first = build(user_id)

    write_profile(
        user_id,
        legacy_objective(
            goalType="beginner",
            distanceKm=None,
            raceDate=None,
            goal_target_time=None,
            sessionsPerWeek=2,
        ),
    )
    second = build(user_id)

    assert first.context.goal is not None and first.context.goal.intent == "race"
    assert second.context.goal is not None and second.context.goal.intent == "beginner"
    assert second.context.availability is not None
    assert second.context.availability.preferred_sessions_per_week == 2
    assert first != second


def test_unchanged_fingerprint_does_not_rewrite_but_changed_legacy_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "u_fingerprint"
    write_profile(user_id, legacy_objective())
    first = build(user_id)
    original_write = runtime_context._atomic_write_inputs
    writes: list[str] = []

    def recording_write(inputs: runtime_context.MotionestInputsV1) -> None:
        writes.append(inputs.legacy_objective_digest or inputs.source)
        original_write(inputs)

    monkeypatch.setattr(runtime_context, "_atomic_write_inputs", recording_write)
    unchanged = build(user_id)
    assert unchanged == first
    assert writes == []

    write_profile(user_id, legacy_objective(sessionsPerWeek=2))
    changed = build(user_id)
    assert len(writes) == 1
    assert changed.context.availability is not None
    assert changed.context.availability.preferred_sessions_per_week == 2


def test_explicit_v1_inputs_are_recalculated_without_legacy_override() -> None:
    user_id = "u_v1"
    write_profile(user_id, legacy_objective())
    runtime_context.save_motionest_inputs_v1(
        user_id,
        goal=build_goal_v1("progress"),
        availability=build_availability_v1(
            ["mon", "wed"], preferred_sessions_per_week=2
        ),
        subjective_context=build_subjective_context_v1(affecting_training=False),
    )
    first = build(user_id)
    runtime_context.save_motionest_inputs_v1(
        user_id,
        goal=build_goal_v1("comeback"),
        availability=build_availability_v1(["sat"]),
        subjective_context=build_subjective_context_v1(affecting_training=None),
    )
    second = build(user_id)

    assert first.inputs_source == second.inputs_source == "v1"
    assert first.context.goal is not None and first.context.goal.intent == "progress"
    assert second.context.goal is not None and second.context.goal.intent == "comeback"
    assert second.context.integration.status == "partial"


def test_explicit_v1_is_authoritative_over_a_later_stale_legacy_change() -> None:
    user_id = "u_v1_authority"
    write_profile(user_id, legacy_objective())
    migrated = build(user_id)
    assert migrated.inputs_source == "legacy_profile"

    runtime_context.save_motionest_inputs_v1(
        user_id,
        goal=build_goal_v1("progress"),
        availability=build_availability_v1(["mon", "wed"]),
        subjective_context=build_subjective_context_v1(affecting_training=False),
    )
    write_profile(
        user_id,
        legacy_objective(
            goalType="beginner",
            distanceKm=None,
            raceDate=None,
            goal_target_time=None,
        ),
    )

    current = build(user_id)

    assert current.inputs_source == "v1"
    assert current.context.goal is not None
    assert current.context.goal.intent == "progress"
    with config.user_context(user_id):
        stored = runtime_context.MotionestInputsV1.model_validate_json(
            config.MOTIONEST_INPUTS_FILE.read_text(encoding="utf-8")
        )
    assert stored.source == "v1"
    assert stored.legacy_objective_digest is None


def test_concurrent_runtime_migration_writes_once_and_returns_same_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "u_concurrent"
    write_profile(user_id, legacy_objective())
    workers = 6
    ready = threading.Barrier(workers)
    counter_lock = threading.Lock()
    writes = 0
    original_write = runtime_context._atomic_write_inputs

    def recording_write(inputs: runtime_context.MotionestInputsV1) -> None:
        nonlocal writes
        with counter_lock:
            writes += 1
        original_write(inputs)

    def run() -> runtime_context.RuntimeMotionestContextV1:
        ready.wait(timeout=5)
        return build(user_id)

    monkeypatch.setattr(runtime_context, "_atomic_write_inputs", recording_write)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(lambda _: run(), range(workers)))

    assert writes == 1
    assert all(result == results[0] for result in results)
    with config.user_context(user_id):
        stored = runtime_context.MotionestInputsV1.model_validate_json(
            config.MOTIONEST_INPUTS_FILE.read_text(encoding="utf-8")
        )
    assert stored.source == "legacy_profile"


def test_atomic_replace_failure_preserves_previous_explicit_v1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "u_atomic"
    runtime_context.save_motionest_inputs_v1(
        user_id,
        goal=build_goal_v1("progress"),
        availability=build_availability_v1(["mon"]),
        subjective_context=build_subjective_context_v1(affecting_training=False),
    )
    with config.user_context(user_id):
        before = config.MOTIONEST_INPUTS_FILE.read_text(encoding="utf-8")
        parent = Path(config.MOTIONEST_INPUTS_FILE)

    def fail_replace(source: object, target: object) -> None:
        raise OSError("simulated atomic replacement failure")

    monkeypatch.setattr(runtime_context.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic replacement failure"):
        runtime_context.save_motionest_inputs_v1(
            user_id,
            goal=build_goal_v1("comeback"),
            availability=build_availability_v1(["sat"]),
            subjective_context=build_subjective_context_v1(
                affecting_training=None
            ),
        )

    with config.user_context(user_id):
        assert config.MOTIONEST_INPUTS_FILE.read_text(encoding="utf-8") == before
        assert list(parent.parent.glob(f".{parent.name}.*.tmp")) == []


def test_two_users_are_strictly_isolated() -> None:
    write_profile("u_alice", legacy_objective(goalType="progress", distanceKm=None, raceDate=None, goal_target_time=None))
    write_profile("u_bob", legacy_objective(goalType="beginner", distanceKm=None, raceDate=None, goal_target_time=None))
    prepare_history("u_alice", 18)
    prepare_history("u_bob", 1)

    alice = build("u_alice")
    bob = build("u_bob")

    assert alice.context.goal is not None and alice.context.goal.intent == "progress"
    assert bob.context.goal is not None and bob.context.goal.intent == "beginner"
    assert alice.context.training_history_summary.activity_count == 18
    assert bob.context.training_history_summary.activity_count == 1
    assert alice.context.athlete_state.status == "available"
    assert bob.context.athlete_state.status == "partial"


def test_runtime_rejects_a_user_id_that_could_escape_its_workspace() -> None:
    with pytest.raises(ValueError, match="Identifiant utilisateur"):
        runtime_context.build_runtime_motionest_context(
            "../another-user", reference_date=REFERENCE
        )


def _serve() -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_dashboard_onboarding_endpoint_builds_runtime_without_external_calls(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    user_id = "u_runtime_route"
    write_profile(user_id, legacy_objective())
    prepare_history(user_id, 1)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("External side effect called during runtime context build")

    monkeypatch.setattr(garmin, "connect", forbidden)
    monkeypatch.setattr(dashboard, "connect", forbidden)
    monkeypatch.setattr(ai_provider, "structured_completion", forbidden)
    monkeypatch.setattr(garmin_workouts, "publish_plan", forbidden)
    monkeypatch.setattr(dashboard, "publish_plan", forbidden)
    monkeypatch.setattr(dashboard, "generate_initial_plan", forbidden)
    cookie = f"motionest_session={user_accounts.create_session(user_id)}"
    server, thread = _serve()
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        connection.request("GET", "/api/onboarding", headers={"Cookie": cookie})
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()

    assert response.status == 200
    runtime = payload["motionestRuntime"]
    assert runtime["schema_version"] == 1
    assert runtime["context"]["training_history_summary"]["activity_count"] == 1
    exposed = json.dumps(runtime, ensure_ascii=False)
    for forbidden_value in (
        "Private activity",
        "Private race title",
        "Private unmapped constraint",
        "must-stay-local",
        "oauth_token",
        "latitude",
        "longitude",
    ):
        assert forbidden_value not in exposed
        assert forbidden_value not in caplog.text
