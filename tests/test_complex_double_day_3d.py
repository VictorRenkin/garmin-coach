"""Missing combinations from the 3D matrix; all remote behavior is simulated.

Reuse the real stores, 3B blocks, 3C authorization and VerifiedClient. No network,
AI provider, alternative generator or duplicate calendar implementation.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from garmin_coach import config, garmin_delivery as delivery, plan_acceptance, plan_proposal
from garmin_coach.plan_proposal import PlanProposalV2, proposal_fingerprint
from tests.test_calendar_3c import accept, double_proposal, source_for
from tests.test_garmin_delivery import VerifiedClient
from tests.test_plan_proposal import settings
from tests.test_session_blocks_3b import effective_seconds, session as complex_session


class CapturingClient(VerifiedClient):
    """Existing fake service with payload capture and state shared across clients."""

    def __init__(self, previous=None):
        super().__init__()
        self.workouts = []
        if previous is not None:
            self.uploads = previous.uploads
            self.schedules = previous.schedules
            self.items = previous.items
            self.workouts = previous.workouts

    def upload_running_workout(self, workout):
        self.workouts.append(workout)
        return super().upload_running_workout(workout)


@pytest.fixture
def pair(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / "secrets")
    user = "synthetic-complex-double-3d"
    source = source_for(user)
    raw = double_proposal(source).model_dump(mode="json")
    for index, recovery in enumerate((.75, 1.5)):
        blocks = complex_session(recovery).model_dump(mode="json")
        raw["sessions"][index].update(
            structure=blocks["structure"], duration_minutes=blocks["duration_minutes"],
            session_type="intervalles", objective=f"Blocs synthétiques {index + 1}.",
        )
    proposal = PlanProposalV2.model_validate(raw)
    assert plan_proposal.validate_plan_proposal(proposal, source).valid
    monkeypatch.setattr(plan_proposal.ai_provider, "structured_completion", lambda *a, **kw: proposal)
    monkeypatch.setattr(plan_proposal, "build_plan_generation_input_v2", lambda *a, **kw: source)
    created = plan_proposal.generate_plan_proposal(user, settings=settings())
    assert created.status == "available"

    def read_state(user_id=user):
        # Re-read validated JSON rather than returning the old in-memory proposal.
        with config.user_context(user_id):
            return plan_proposal._state(source)

    monkeypatch.setattr(delivery, "get_plan_proposal_state", read_state)
    return SimpleNamespace(user=user, source=source, read=read_state)


def approve(pair, identity):
    return accept(pair.user, pair.read(), identity)


def send(pair, client, index):
    return delivery.publish_accepted_session(pair.user, client, session_index=index,
        expected_session_id=("morning", "evening")[index])


def stored_delivery(pair):
    with config.user_context(pair.user):
        return delivery._read_store()


def test_two_complex_same_day_workouts_keep_seconds_and_separate_consent_despite_identical_names(pair):
    client = CapturingClient()
    for index in (0, 1):
        with pytest.raises(delivery.GarminDeliveryError, match="Valide explicitement"):
            send(pair, client, index)
    assert client.uploads == 0 and client.schedules == []

    approve(pair, "morning")
    morning = send(pair, client, 0)
    with pytest.raises(delivery.GarminDeliveryError, match="Valide explicitement"):
        send(pair, client, 1)
    assert client.uploads == 1 and len(client.schedules) == 1
    approve(pair, "evening")
    evening = send(pair, client, 1)

    assert morning["session_id"] != evening["session_id"]
    assert morning["garmin_workout_id"] != evening["garmin_workout_id"]
    assert morning["garmin_calendar_event_id"] != evening["garmin_calendar_event_id"]
    assert client.workouts[0].workoutName == client.workouts[1].workoutName == "MOT 1310 RUN"
    for workout, recovery, total in zip(client.workouts, (45, 90), (2100, 2280)):
        steps = workout.workoutSegments[0].workoutSteps
        assert effective_seconds(steps) == total == workout.estimatedDurationInSecs
        assert [step.numberOfIterations for step in steps if hasattr(step, "numberOfIterations")] == [4, 2]
        assert steps[1].workoutSteps[1].endConditionValue == recovery
        assert steps[2].workoutSteps[1].endConditionValue == 30
        assert [steps[1].workoutSteps[0].zoneNumber, steps[2].workoutSteps[0].zoneNumber] == [3, 4]
        assert "starts_at" not in workout.model_dump()
    assert client.schedules == [(701, "2099-10-13"), (702, "2099-10-13")]
    restarted_client = CapturingClient(client)
    assert send(pair, restarted_client, 0) == morning
    assert send(pair, restarted_client, 1) == evening
    assert restarted_client.uploads == 2 and len(restarted_client.schedules) == 2


def test_revision_of_complex_morning_preserves_evening_record_after_store_reload(pair):
    client = CapturingClient()
    approve(pair, "morning")
    approve(pair, "evening")
    send(pair, client, 0)
    evening = send(pair, client, 1)
    original = pair.read()
    unchanged = original.proposal.sessions[1].model_dump(mode="json")
    changed = complex_session(1.5)
    revised = plan_proposal.revise_plan_session(pair.user, session_id="morning",
        expected_proposal_fingerprint=proposal_fingerprint(original.proposal), source=pair.source,
        patch={"structure": changed.structure.model_dump(mode="json"), "duration_minutes": changed.duration_minutes})
    assert pair.read().proposal == revised.proposal
    assert revised.proposal.sessions[1].model_dump(mode="json") == unchanged
    assert plan_acceptance.get_plan_acceptance_state(pair.user, proposal_state=pair.read(), session_id="morning").status == "stale"
    assert plan_acceptance.get_plan_acceptance_state(pair.user, proposal_state=pair.read(), session_id="evening").status == "accepted"
    restarted = CapturingClient(client)
    with pytest.raises(delivery.GarminDeliveryError) as error:
        send(pair, restarted, 0)
    assert error.value.code == "session_not_accepted"
    assert send(pair, restarted, 1) == evening
    assert restarted.uploads == 2 and len(restarted.schedules) == 2
    approve(pair, "morning")
    with pytest.raises(delivery.GarminDeliveryError) as error:
        send(pair, restarted, 0)
    assert error.value.code == "calendar_collision"
    assert restarted.uploads == 2 and len(restarted.schedules) == 2


def test_interruption_after_upload_id_persistence_resumes_without_a_second_upload(pair, monkeypatch):
    client = CapturingClient()
    approve(pair, "morning")
    original_write = delivery._write_store

    def persist_then_interrupt(store):
        original_write(store)
        if any(record.get("workout_id") for record in store["deliveries"].values()):
            raise RuntimeError("Synthetic process interruption after committed upload ID")

    with monkeypatch.context() as scoped:
        scoped.setattr(delivery, "_write_store", persist_then_interrupt)
        with pytest.raises(RuntimeError, match="Synthetic process interruption"):
            send(pair, client, 0)
    record = next(iter(stored_delivery(pair)["deliveries"].values()))
    assert record["workout_id"] == 701 and "schedule_requested_at" not in record
    assert client.uploads == 1 and client.schedules == []
    restarted = CapturingClient(client)
    assert send(pair, restarted, 0)["calendar_status"] == "scheduled"
    assert restarted.uploads == 1 and restarted.schedules == [(701, "2099-10-13")]


def test_schedule_ack_lost_after_simulated_remote_success_is_verified_without_resending(pair):
    class LostScheduleAck(CapturingClient):
        def schedule_workout(self, workout_id, scheduled_date):
            super().schedule_workout(workout_id, scheduled_date)
            raise TimeoutError("Synthetic response lost after scheduling")

    client = LostScheduleAck()
    approve(pair, "morning")
    with pytest.raises(delivery.GarminDeliveryError) as error:
        send(pair, client, 0)
    assert error.value.code == "calendar_schedule_uncertain"
    restarted = CapturingClient(client)
    recovered = send(pair, restarted, 0)
    assert recovered["calendar_status"] == "scheduled" and recovered["last_error"] is None
    assert restarted.uploads == 1 and restarted.schedules == [(701, "2099-10-13")]


def test_confirmation_absent_remains_pending_then_reconciles_from_reloaded_store(pair):
    class NoCalendarConfirmation(CapturingClient):
        def schedule_workout(self, workout_id, scheduled_date):
            self.schedules.append((workout_id, scheduled_date))

    client = NoCalendarConfirmation()
    approve(pair, "morning")
    first = send(pair, client, 0)
    assert first["calendar_status"] == "verification_pending"
    restarted = CapturingClient(client)
    assert send(pair, restarted, 0)["calendar_status"] == "verification_pending"
    assert restarted.uploads == 1 and len(restarted.schedules) == 1
    restarted.items.append({"id": 900, "itemType": "workout", "workoutId": 701, "date": "2099-10-13"})
    assert send(pair, restarted, 0)["calendar_status"] == "scheduled"
    assert restarted.uploads == 1 and len(restarted.schedules) == 1


@pytest.mark.parametrize("payload", [None, {"calendarItems": [None]}])
def test_bad_calendar_after_first_complex_delivery_never_sends_second_or_rewrites_known_ids(pair, monkeypatch, payload):
    client = CapturingClient()
    approve(pair, "morning")
    send(pair, client, 0)
    approve(pair, "evening")
    before = stored_delivery(pair)
    restarted = CapturingClient(client)
    with monkeypatch.context() as scoped:
        scoped.setattr(restarted, "get_scheduled_workouts", lambda *a: payload)
        with pytest.raises(delivery.GarminDeliveryError) as error:
            send(pair, restarted, 1)
    assert error.value.code == "calendar_unavailable"
    assert stored_delivery(pair) == before
    assert restarted.uploads == 1 and len(restarted.schedules) == 1


def test_upload_without_confirmed_id_never_schedules_or_claims_created_workout(pair):
    class NoUploadAck(CapturingClient):
        def upload_running_workout(self, workout):
            super().upload_running_workout(workout)
            return {}  # Synthetic service may have created it, but the caller has no ID.

    client = NoUploadAck()
    approve(pair, "morning")
    with pytest.raises(delivery.GarminDeliveryError) as error:
        send(pair, client, 0)
    assert error.value.code == "workout_id_missing"
    assert client.uploads == 1 and client.schedules == []
    record = next(iter(stored_delivery(pair)["deliveries"].values()))
    assert record["upload_requested_at"] and not record.get("workout_id")
