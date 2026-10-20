"""Read-only retries never authorize a new delivery or an unknown calendar."""
import copy

import pytest

from garmin_coach import config, garmin_delivery as delivery
from tests.test_garmin_delivery import isolated_data, accepted, proposal_state, VerifiedClient


@pytest.mark.parametrize("payload", [None, {"calendarItems": [None]}, {"calendarItems": [{"itemType": "workout"}]}])
def test_existing_confirmation_still_requires_a_well_formed_calendar(monkeypatch, payload):
    accepted(monkeypatch, proposal_state())
    client = VerifiedClient()
    delivery.publish_accepted_session("runner", client, session_index=0)
    with config.user_context("runner"):
        before = copy.deepcopy(delivery._read_store())
    monkeypatch.setattr(client, "get_scheduled_workouts", lambda *args: payload)
    with pytest.raises(delivery.GarminDeliveryError) as error:
        delivery.publish_accepted_session("runner", client, session_index=0)
    assert error.value.code == "calendar_unavailable"
    with config.user_context("runner"):
        assert delivery._read_store() == before
    assert client.uploads == 1 and len(client.schedules) == 1


def test_persisted_upload_without_schedule_history_cannot_bypass_new_collision(monkeypatch):
    accepted(monkeypatch, proposal_state())
    client = VerifiedClient()
    delivery.publish_accepted_session("runner", client, session_index=0)
    with config.user_context("runner"):
        store = delivery._read_store()
        next(iter(store["deliveries"].values())).pop("schedule_requested_at")
        delivery._write_store(store)
        before = copy.deepcopy(store)
    client.items.append({"id": 999, "itemType": "workout", "workoutId": 999, "date": "2099-10-13"})
    with pytest.raises(delivery.GarminDeliveryError) as error:
        delivery.publish_accepted_session("runner", client, session_index=0)
    assert error.value.code == "calendar_collision"
    with config.user_context("runner"):
        assert delivery._read_store() == before
    assert client.uploads == 1 and len(client.schedules) == 1
