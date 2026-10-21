"""Bounded protection of uncertain uploads, using only the existing fake service."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from garmin_coach import config, garmin_delivery as delivery, plan_proposal
from tests.test_complex_double_day_3d import (
    CapturingClient, approve, pair, send, stored_delivery,
)


@pytest.mark.parametrize("lost_reply", ["missing_id", "timeout"])
def test_unknown_upload_cannot_be_created_twice(lost_reply, pair):
    class LostReplyClient(CapturingClient):
        def upload_running_workout(self, workout):
            super().upload_running_workout(workout)
            if lost_reply == "timeout":
                raise TimeoutError("Synthetic service created it but response was lost")
            return {}

    approve(pair, "morning")
    client = LostReplyClient()
    errors = []
    for _ in range(2):
        try:
            send(pair, client, 0)
        except (delivery.GarminDeliveryError, TimeoutError) as exc:
            errors.append(getattr(exc, "code", type(exc).__name__))
    assert errors == (["workout_id_missing", "upload_uncertain"] if lost_reply == "missing_id" else ["upload_uncertain"] * 2)
    assert client.uploads == 1
    assert client.schedules == []
    record = next(iter(stored_delivery(pair)["deliveries"].values()))
    assert record["upload_requested_at"] and not record.get("workout_id")
    state = delivery.get_garmin_delivery_state(pair.user)["sessions"][0]
    assert state["garmin_connect_status"] == "unknown" and state["upload_status"] == "uncertain"
    assert "Synthetic" not in state["last_error"]
    class NoRemote:
        def __getattr__(self, name):
            raise AssertionError("No remote method may be accessed on an uncertain retry")
    with pytest.raises(delivery.GarminDeliveryError) as error:
        send(pair, NoRemote(), 0)
    assert error.value.code == "upload_uncertain"
    with pytest.raises(delivery.GarminDeliveryError) as error:
        delivery.assert_upload_retry_safe(pair.user, session_index=0, expected_session_id="morning")
    assert error.value.code == "upload_uncertain"


@pytest.mark.parametrize("identifier", [None, True, False, 0, -1, 1.5, "", "bad", "1.0", "-1", "١"])
def test_invalid_upload_ids_leave_uncertainty_without_schedule(pair, identifier):
    class InvalidId(CapturingClient):
        def upload_running_workout(self, workout):
            super().upload_running_workout(workout)
            return {"workoutId": identifier}
    approve(pair, "morning")
    client = InvalidId()
    with pytest.raises(delivery.GarminDeliveryError) as error:
        send(pair, client, 0)
    assert error.value.code == "workout_id_missing"
    with pytest.raises(delivery.GarminDeliveryError) as error:
        send(pair, client, 0)
    assert error.value.code == "upload_uncertain"
    assert client.uploads == 1 and not client.schedules


def test_write_failure_before_upload_has_no_remote_effect(pair, monkeypatch):
    approve(pair, "morning")
    client = CapturingClient()
    def fail(store): raise OSError("Synthetic disk failure")
    monkeypatch.setattr(delivery, "_write_store", fail)
    with pytest.raises(OSError): send(pair, client, 0)
    assert client.uploads == 0 and not client.schedules


def test_write_failure_after_returned_id_preserves_intent_and_blocks_restart(pair, monkeypatch):
    approve(pair, "morning")
    client = CapturingClient()
    original = delivery._write_store
    def fail_id(store):
        if any(r.get("workout_id") for r in store["deliveries"].values()):
            raise OSError("Synthetic ID persistence failure")
        original(store)
    with monkeypatch.context() as scoped:
        scoped.setattr(delivery, "_write_store", fail_id)
        with pytest.raises(OSError): send(pair, client, 0)
    restarted = CapturingClient(client)
    with pytest.raises(delivery.GarminDeliveryError) as error: send(pair, restarted, 0)
    assert error.value.code == "upload_uncertain"
    assert restarted.uploads == 1 and not restarted.schedules


def test_interruption_after_intent_commit_is_conservatively_uncertain(pair, monkeypatch):
    approve(pair, "morning")
    client = CapturingClient()
    original = delivery._write_store
    def persist_then_stop(store):
        original(store)
        raise RuntimeError("Synthetic stop after intent commit, before upload")
    with monkeypatch.context() as scoped:
        scoped.setattr(delivery, "_write_store", persist_then_stop)
        with pytest.raises(RuntimeError): send(pair, client, 0)
    with pytest.raises(delivery.GarminDeliveryError) as error: send(pair, client, 0)
    assert error.value.code == "upload_uncertain"
    assert client.uploads == 0 and not client.schedules


def test_intent_survives_same_session_revision_and_does_not_block_known_sibling_id(pair):
    approve(pair, "evening")
    client = CapturingClient()
    known = send(pair, client, 1)
    approve(pair, "morning")
    class Missing(CapturingClient):
        def upload_running_workout(self, workout):
            super().upload_running_workout(workout)
            return {}
    missing = Missing(client)
    with pytest.raises(delivery.GarminDeliveryError): send(pair, missing, 0)
    original = pair.read()
    plan_proposal.revise_plan_session(pair.user, session_id="morning",
        expected_proposal_fingerprint=plan_proposal.proposal_fingerprint(original.proposal),
        source=pair.source, patch={"objective": "Revised objective, same stable identity"})
    approve(pair, "morning")
    projected = delivery.get_garmin_delivery_state(pair.user)["sessions"][0]
    assert projected["garmin_connect_status"] == "unknown" and projected["upload_status"] == "uncertain"
    with pytest.raises(delivery.GarminDeliveryError) as error: send(pair, missing, 0)
    assert error.value.code == "upload_uncertain"
    assert send(pair, missing, 1) == known
    assert missing.uploads == 2 and len(missing.schedules) == 1


def test_two_threads_only_attempt_one_uncertain_upload(pair):
    approve(pair, "morning")
    class Missing(CapturingClient):
        def upload_running_workout(self, workout):
            super().upload_running_workout(workout)
            return {}
    client = Missing()
    def attempt(_):
        try: send(pair, client, 0)
        except delivery.GarminDeliveryError as exc: return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["upload_uncertain", "workout_id_missing"]
    assert client.uploads == 1 and not client.schedules


def test_uncertain_intent_is_scoped_to_account_and_legacy_is_conservative(pair):
    approve(pair, "morning")
    approve(pair, "evening")
    client = CapturingClient()
    confirmed = send(pair, client, 1)
    with config.user_context(pair.user):
        store = delivery._read_store()
        store["deliveries"]["old-legacy"] = {"upload_requested_at": "2099-10-13T08:00:00+02:00", "session_id": None}
        delivery._write_store(store)
    with pytest.raises(delivery.GarminDeliveryError) as error: send(pair, client, 0)
    assert error.value.code == "upload_uncertain" and client.uploads == 1
    assert send(pair, client, 1) == confirmed
    assert client.uploads == 1 and len(client.schedules) == 1
    with config.user_context("other-synthetic-account"):
        assert delivery._read_store()["deliveries"] == {}
        session = pair.read().proposal.sessions[0]
        assert delivery._uncertain_upload(delivery._read_store(), session, "other-key") is None
