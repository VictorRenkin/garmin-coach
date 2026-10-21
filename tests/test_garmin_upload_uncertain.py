"""Characterize the existing unknown-upload gap without a real service."""
import pytest

from garmin_coach import garmin_delivery as delivery
from tests.test_complex_double_day_3d import (
    CapturingClient, approve, pair, send, stored_delivery,
)


@pytest.mark.parametrize("lost_reply", ["missing_id", "timeout"])
def test_characterize_unknown_upload_can_be_created_twice(lost_reply, pair):
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
    # This is a characterization of the existing defect, not a desired contract.
    assert errors == (["workout_id_missing"] * 2 if lost_reply == "missing_id" else ["TimeoutError"] * 2)
    assert client.uploads == 2
    assert client.schedules == []
    assert stored_delivery(pair)["deliveries"] == {}
