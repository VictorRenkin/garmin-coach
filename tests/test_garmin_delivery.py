from __future__ import annotations

import json
import threading
import urllib.request
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from garmin_coach import config, dashboard, garmin_delivery as module, user_accounts
from garmin_coach.garmin_delivery import (
    GarminDeliveryError,
    build_plan_session_workout,
    publish_accepted_session,
)
from garmin_coach.plan_proposal import (
    PlanPeriodV2,
    PlanProposalStateV2,
    PlanProposalV2,
    PlanSessionStructureV2,
    PlanSessionV2,
)


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    monkeypatch.setattr(user_accounts, "SESSION_KEY_FILE", tmp_path / "session.key")


def running_session() -> PlanSessionV2:
    return PlanSessionV2(
        date=date(2099, 10, 13),
        sport="running",
        session_type="reprise_course_marche",
        objective="Reprendre les impacts de façon progressive.",
        duration_minutes=45,
        intensity_instruction="Rester en aisance respiratoire.",
        structure=PlanSessionStructureV2(
            warmup_minutes=10,
            main_set="Alterner trois minutes de course et deux minutes de marche.",
            repetitions=6,
            effort_minutes=3,
            recovery_minutes=2,
            cooldown_minutes=5,
        ),
        explanation="Une reprise structurée et progressive.",
    )


def proposal_state(session: PlanSessionV2 | None = None) -> PlanProposalStateV2:
    proposal = PlanProposalV2(
        period=PlanPeriodV2(start=date(2099, 10, 12), end=date(2099, 10, 18)),
        summary="Une semaine progressive.",
        strategy="Une séance structurée et contrôlée.",
        sessions=[session or running_session()],
    )
    return PlanProposalStateV2(
        status="available", input_fingerprint="a" * 64, proposal=proposal
    )


def accepted(monkeypatch: pytest.MonkeyPatch, state: PlanProposalStateV2) -> None:
    monkeypatch.setattr(
        module, "_accepted_proposal", lambda user_id: (state, "b" * 64)
    )


def test_plan_session_exports_exact_duration_and_repetitions() -> None:
    workout = build_plan_session_workout(running_session())

    assert workout.workoutName == "MOT 1310 RUN"
    assert workout.estimatedDurationInSecs == 45 * 60
    steps = workout.workoutSegments[0].workoutSteps
    assert len(steps) == 3
    assert steps[1].numberOfIterations == 6
    assert steps[1].workoutSteps[0].endConditionValue == 3 * 60
    assert steps[1].workoutSteps[1].endConditionValue == 2 * 60


class VerifiedClient:
    def __init__(self) -> None:
        self.uploads = 0
        self.schedules: list[tuple[int, str]] = []
        self.items: list[dict] = []

    def upload_running_workout(self, workout):
        self.uploads += 1
        return {"workoutId": 700 + self.uploads}

    def schedule_workout(self, workout_id, scheduled_date):
        self.schedules.append((workout_id, scheduled_date))
        self.items.append(
            {
                "id": 900 + len(self.items),
                "itemType": "workout",
                "workoutId": workout_id,
                "date": scheduled_date,
            }
        )

    def get_scheduled_workouts(self, year, month):
        return {"calendarItems": self.items}


def test_delivery_persists_ids_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accepted(monkeypatch, proposal_state())
    client = VerifiedClient()
    now = ["2026-10-18T18:31:16+02:00"]
    monkeypatch.setattr(module, "_now", lambda: now[0])

    first = publish_accepted_session("runner", client, session_index=0)
    now[0] = "2026-10-18T18:31:17+02:00"

    def unexpected_write(store):
        pytest.fail("An unchanged delivery must not rewrite its persisted facts.")

    monkeypatch.setattr(module, "_write_store", unexpected_write)
    second = publish_accepted_session("runner", client, session_index=0)

    assert first["motionest_status"] == "saved"
    assert first["garmin_connect_status"] == "created"
    assert first["garmin_workout_id"] == 701
    assert first["calendar_status"] == "scheduled"
    assert first["garmin_calendar_event_id"] == 900
    assert first["watch_sync_status"] == "unverified"
    assert first["structured_blocks_exported"] is True
    assert first["instructions_exported"] is False
    assert second == first
    assert client.uploads == 1
    assert client.schedules == [(701, "2099-10-13")]


def test_uncertain_calendar_is_verified_without_duplicate_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accepted(monkeypatch, proposal_state())
    client = VerifiedClient()
    client.schedule_workout = lambda workout_id, scheduled_date: client.schedules.append(
        (workout_id, scheduled_date)
    )

    now = ["2026-10-18T18:31:16+02:00"]
    monkeypatch.setattr(module, "_now", lambda: now[0])
    first = publish_accepted_session("runner", client, session_index=0)
    now[0] = "2026-10-18T18:31:17+02:00"
    second = publish_accepted_session("runner", client, session_index=0)

    assert second == first
    assert first["calendar_status"] == "verification_pending"
    assert second["calendar_status"] == "verification_pending"
    assert client.uploads == 1
    assert client.schedules == [(701, "2099-10-13")]


@pytest.mark.parametrize("change", ["pending_confirmed", "event_id_changed", "error_cleared", "confirmation_lost"])
def test_delivery_timestamp_advances_only_when_verified_facts_change(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    accepted(monkeypatch, proposal_state())
    client = VerifiedClient()
    now = ["2026-10-18T18:31:16+02:00"]
    monkeypatch.setattr(module, "_now", lambda: now[0])
    if change == "pending_confirmed":
        client.schedule_workout = lambda workout_id, scheduled_date: client.schedules.append(
            (workout_id, scheduled_date)
        )
    first = publish_accepted_session("runner", client, session_index=0)
    if change == "pending_confirmed":
        client.items.append({"id": 900, "itemType": "workout", "workoutId": 701, "date": "2099-10-13"})
    elif change == "event_id_changed":
        client.items[0]["id"] = 901
    elif change == "error_cleared":
        with config.user_context("runner"):
            store = module._read_store()
            record = next(iter(store["deliveries"].values()))
            record["last_error"] = "Synthetic previous verification error"
            module._write_store(store)
    else:
        client.items.clear()
    now[0] = "2026-10-18T18:31:17+02:00"
    second = publish_accepted_session("runner", client, session_index=0)

    assert first["updated_at"] == "2026-10-18T18:31:16+02:00"
    assert second["updated_at"] == now[0]
    assert second["garmin_workout_id"] == 701
    assert second["calendar_status"] == (
        "verification_pending" if change == "confirmation_lost" else "scheduled"
    )
    if change != "confirmation_lost":
        assert second["garmin_calendar_event_id"] == (901 if change == "event_id_changed" else 900)
        assert second["last_error"] is None
    with config.user_context("runner"):
        stored = next(iter(module._read_store()["deliveries"].values()))
    assert stored["updated_at"] == now[0]
    assert stored["calendar_status"] == second["calendar_status"]
    assert client.uploads == 1
    assert client.schedules == [(701, "2099-10-13")]


def test_non_running_session_is_not_invented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cycling = running_session().model_copy(update={"sport": "cycling"})
    accepted(monkeypatch, proposal_state(cycling))

    with pytest.raises(GarminDeliveryError, match="course") as error:
        publish_accepted_session("runner", VerifiedClient(), session_index=0)

    assert error.value.code == "sport_not_supported"


def test_unaccepted_proposal_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    state = proposal_state()
    monkeypatch.setattr(module, "get_plan_proposal_state", lambda user_id: state)
    monkeypatch.setattr(
        module,
        "get_plan_acceptance_state",
        lambda user_id, proposal_state: SimpleNamespace(status="missing"),
    )

    with pytest.raises(GarminDeliveryError) as error:
        module.get_garmin_delivery_state("runner")

    assert error.value.code == "proposal_not_accepted"


def test_dashboard_delivery_routes_keep_the_four_states_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery = {
        "session_index": 0,
        "session_date": "2099-10-13",
        "scheduled_date": "2099-10-13",
        "sport": "running",
        "supported": True,
        "motionest_status": "saved",
        "garmin_connect_status": "created",
        "garmin_workout_id": 701,
        "calendar_status": "scheduled",
        "garmin_calendar_event_id": 900,
        "watch_sync_status": "unverified",
        "structured_blocks_exported": True,
        "instructions_exported": False,
        "last_error": None,
        "updated_at": "2099-10-12T12:00:00+02:00",
    }
    calls: list[dict] = []
    monkeypatch.setattr(
        dashboard,
        "get_garmin_delivery_state",
        lambda user_id: {"status": "available", "sessions": [delivery]},
    )
    monkeypatch.setattr(dashboard, "connect", lambda settings: object())

    def publish(user_id, client, **kwargs):
        calls.append({"user_id": user_id, **kwargs})
        return delivery

    monkeypatch.setattr(dashboard, "publish_accepted_session", publish)
    cookie = f"motionest_session={user_accounts.create_session('route_user')}"
    server = dashboard.ThreadingHTTPServer(
        ("127.0.0.1", 0), dashboard.DashboardHandler
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        get_request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/garmin/workout-delivery",
            headers={"Cookie": cookie},
        )
        with urllib.request.urlopen(get_request, timeout=5) as response:
            get_payload = json.loads(response.read())
        assert get_payload["sessions"][0]["watch_sync_status"] == "unverified"

        post_request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/garmin/workout-delivery",
            data=json.dumps(
                {"session_index": 0, "scheduled_date": "2099-10-13"}
            ).encode(),
            headers={"Content-Type": "application/json", "Cookie": cookie},
            method="POST",
        )
        with urllib.request.urlopen(post_request, timeout=5) as response:
            post_payload = json.loads(response.read())
        assert post_payload["delivery"]["calendar_status"] == "scheduled"
        assert calls == [
            {
                "user_id": "route_user",
                "session_index": 0,
                "scheduled_date": date(2099, 10, 13),
            }
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
