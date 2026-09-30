from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from garmin_coach import (
    config,
    history_import,
    history_pagination,
    history_storage,
    onboarding,
)


REFERENCE = date(2026, 9, 30)


def activity(
    index: int,
    *,
    day: date | None = None,
    activity_id: int | str | None = None,
    sport: str = "running",
    **extra: Any,
) -> dict[str, Any]:
    selected_day = day or (REFERENCE - timedelta(days=index))
    return {
        "activityId": activity_id if activity_id is not None else 10_000 + index,
        "activityType": {"typeKey": sport, "typeId": index % 12},
        "activityName": f"Activity {index}",
        "startTimeLocal": f"{selected_day.isoformat()}T08:00:00",
        "duration": 1800 + index,
        "movingDuration": 1700 + index,
        "distance": 5000 + index,
        **extra,
    }


class FakeGarmin:
    def __init__(
        self,
        activities: list[dict[str, Any]],
        *,
        fail_once_at: int | None = None,
        repeated_page: bool = False,
    ) -> None:
        self.activities = activities
        self.fail_once_at = fail_once_at
        self.repeated_page = repeated_page
        self.calls: list[tuple[int, int]] = []
        self.failed = False

    def get_activities(self, start: int, limit: int):
        self.calls.append((start, limit))
        if self.fail_once_at == start and not self.failed:
            self.failed = True
            raise ConnectionError("network unavailable")
        if self.repeated_page and start:
            return self.activities[:limit]
        return self.activities[start : start + limit]


@pytest.fixture
def isolated_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    monkeypatch.delenv("MOTIONEST_HISTORY_PAGE_SIZE", raising=False)
    return tmp_path


@pytest.fixture
def memory_metadata(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    saved_ids: set[str] = set()

    def save(item: dict[str, Any], **kwargs):
        activity_id = str(item["activityId"])
        created = activity_id not in saved_ids
        saved_ids.add(activity_id)
        return Path(f"{activity_id}.json"), created

    monkeypatch.setattr(history_pagination, "save_activity_metadata", save)
    return saved_ids


def initialize(
    user_id: str,
    *,
    scope: str = "all",
    page_size: int = 250,
) -> None:
    with config.user_context(user_id):
        history_import.initialize_history_import(
            scope, reference_date=REFERENCE, page_size=page_size
        )


@pytest.mark.parametrize(
    ("count", "expected_offsets"),
    [
        (49, [0]),
        (163, [0]),
        (250, [0, 250]),
        (251, [0, 250]),
        (500, [0, 250, 500]),
        (613, [0, 250, 500]),
    ],
)
def test_pagination_has_no_hidden_100_or_250_activity_limit(
    isolated_history: Path,
    memory_metadata: set[str],
    count: int,
    expected_offsets: list[int],
) -> None:
    user_id = f"u_count_{count}"
    initialize(user_id)
    client = FakeGarmin([activity(index) for index in range(count)])

    with config.user_context(user_id):
        state = history_pagination.paginate_activity_history(client)

    assert [start for start, _ in client.calls] == expected_offsets
    assert all(limit == 250 for _, limit in client.calls)
    assert state["pagination_complete"] is True
    assert state["activities_examined"] == count
    assert state["metadata_saved"] == count
    assert len(memory_metadata) == count


def test_pagination_streams_each_persisted_activity_to_bounded_consumer(
    isolated_history: Path, memory_metadata: set[str]
) -> None:
    initialize("u_stream", page_size=2)
    observed: list[str] = []
    client = FakeGarmin([activity(index) for index in range(5)])

    with config.user_context("u_stream"):
        state = history_pagination.paginate_activity_history(
            client,
            on_activity_persisted=lambda item: observed.append(str(item["activityId"])),
        )

    assert observed == [str(10_000 + index) for index in range(5)]
    assert state["pagination_complete"] is True
    assert len(memory_metadata) == 5


@pytest.mark.parametrize("scope", ["3_months", "6_months", "1_year", "2_years", "all"])
def test_selected_scope_is_applied_to_paginated_metadata(
    isolated_history: Path,
    memory_metadata: set[str],
    scope: str,
) -> None:
    user_id = f"u_scope_{scope}"
    initialize(user_id, scope=scope, page_size=50)
    with config.user_context(user_id):
        initial = history_import.load_history_import(detect_stale=False)
    cutoff = date.fromisoformat(initial["cutoff_date"]) if initial["cutoff_date"] else None
    count = 800 if cutoff is None else (REFERENCE - cutoff).days + 20
    items = [activity(index) for index in range(count)]
    client = FakeGarmin(items)

    with config.user_context(user_id):
        state = history_pagination.paginate_activity_history(client)

    expected = {
        str(item["activityId"])
        for item in items
        if cutoff is None
        or cutoff <= date.fromisoformat(item["startTimeLocal"][:10]) <= REFERENCE
    }
    assert memory_metadata == expected
    assert state["metadata_saved"] == len(expected)
    assert state["pagination_complete"] is True


def test_cutoff_in_the_middle_of_a_page_is_partial_and_inclusive(
    isolated_history: Path, memory_metadata: set[str]
) -> None:
    initialize("u_cutoff", scope="3_months", page_size=4)
    cutoff = date(2026, 6, 30)
    items = [
        activity(0, day=date(2026, 7, 2), activity_id="newer"),
        activity(1, day=cutoff, activity_id="exact"),
        activity(2, day=date(2026, 6, 29), activity_id="older-1"),
        activity(3, day=date(2026, 7, 1), activity_id="unordered-new"),
        activity(4, day=date(2026, 6, 28), activity_id="older-2"),
        activity(5, day=date(2026, 6, 27), activity_id="older-3"),
    ]
    with config.user_context("u_cutoff"):
        state = history_pagination.paginate_activity_history(FakeGarmin(items))

    assert memory_metadata == {"newer", "exact", "unordered-new"}
    assert state["pagination_complete"] is True


def test_duplicate_activity_ids_are_safe_and_counted_as_existing(
    isolated_history: Path, memory_metadata: set[str]
) -> None:
    initialize("u_duplicate", page_size=2)
    items = [
        activity(0, activity_id="same"),
        activity(1, activity_id="other"),
        activity(2, activity_id="same"),
    ]
    with config.user_context("u_duplicate"):
        state = history_pagination.paginate_activity_history(FakeGarmin(items))

    assert memory_metadata == {"same", "other"}
    assert state["activities_examined"] == 3
    assert state["metadata_saved"] == 2
    assert state["metadata_existing"] == 1


def test_multiple_and_unknown_sports_are_all_persisted(
    isolated_history: Path,
) -> None:
    initialize("u_multisport", page_size=10)
    sports = ["running", "cycling", "walking", "lap_swimming", "strength_training", "quantum_ball"]
    items = [activity(index, sport=sport) for index, sport in enumerate(sports)]
    with config.user_context("u_multisport"):
        state = history_pagination.paginate_activity_history(FakeGarmin(items))
        stored = list(history_storage.iter_activity_history())

    assert state["metadata_saved"] == len(sports)
    assert {item["activityType"]["typeKey"] for item in stored} == set(sports)


def test_network_failure_keeps_checkpoint_and_retry_resumes_with_overlap(
    isolated_history: Path, memory_metadata: set[str]
) -> None:
    initialize("u_retry", page_size=250)
    items = [activity(index) for index in range(520)]
    first_client = FakeGarmin(items, fail_once_at=500)
    with config.user_context("u_retry"):
        with pytest.raises(ConnectionError):
            history_pagination.paginate_activity_history(first_client)
        failed = history_import.load_history_import(detect_stale=False)

    assert failed["next_offset"] == 500
    assert failed["status"] == "error"
    assert failed["retryable"] is True
    assert failed["error"] == "garmin_network"

    retry_client = FakeGarmin(items)
    with config.user_context("u_retry"):
        complete = history_pagination.paginate_activity_history(retry_client)

    assert [start for start, _ in retry_client.calls] == [0, 250, 500]
    assert complete["pagination_complete"] is True
    assert complete["reference_date"] == REFERENCE.isoformat()
    assert complete["metadata_saved"] == 520
    assert complete["metadata_existing"] >= 500


def test_new_activity_before_resume_is_found_by_head_refresh(
    isolated_history: Path, memory_metadata: set[str]
) -> None:
    initialize("u_new_head", page_size=250)
    original = [activity(index) for index in range(500)]
    with config.user_context("u_new_head"):
        with pytest.raises(ConnectionError):
            history_pagination.paginate_activity_history(
                FakeGarmin(original, fail_once_at=500)
            )
    newest = activity(999, day=REFERENCE, activity_id="brand-new")
    shifted = [newest, *original]
    retry_client = FakeGarmin(shifted)
    with config.user_context("u_new_head"):
        state = history_pagination.paginate_activity_history(retry_client)

    assert "brand-new" in memory_metadata
    assert len(memory_metadata) == 501
    assert retry_client.calls[0][0] == 0
    assert state["pagination_complete"] is True


def test_persistence_failure_does_not_advance_next_offset(
    isolated_history: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize("u_persist_fail", page_size=2)
    stored: set[str] = set()

    def fail_on_second_page(item: dict[str, Any], **kwargs):
        activity_id = str(item["activityId"])
        if activity_id == "10002":
            raise OSError("disk full")
        created = activity_id not in stored
        stored.add(activity_id)
        return Path(f"{activity_id}.json"), created

    monkeypatch.setattr(history_pagination, "save_activity_metadata", fail_on_second_page)
    with config.user_context("u_persist_fail"):
        with pytest.raises(history_pagination.LocalHistoryPersistenceError):
            history_pagination.paginate_activity_history(
                FakeGarmin([activity(index) for index in range(4)])
            )
        state = history_import.load_history_import(detect_stale=False)

    assert state["next_offset"] == 2
    assert state["pages_examined"] == 1
    assert state["error"] == "local_persistence"
    assert state["pagination_complete"] is False


def test_identical_pages_are_rejected_instead_of_looping(
    isolated_history: Path, memory_metadata: set[str]
) -> None:
    initialize("u_identical", page_size=2)
    client = FakeGarmin([activity(index) for index in range(4)], repeated_page=True)
    with config.user_context("u_identical"):
        with pytest.raises(history_pagination.InvalidGarminHistoryResponse):
            history_pagination.paginate_activity_history(client)
        state = history_import.load_history_import(detect_stale=False)

    assert client.calls == [(0, 2), (2, 2)]
    assert state["next_offset"] == 2
    assert state["error"] == "invalid_garmin_response"


def test_nested_privacy_fields_are_removed_but_unknown_sport_data_survives(
    isolated_history: Path,
) -> None:
    raw = activity(
        0,
        startLatitude=50.1,
        ownerFullName="Private Athlete",
        accessToken="secret-token",
        deviceId="private-device-id",
        summary={
            "nested": {"longitude": 4.2, "powerBalance": 49.5},
            "connectIQ": {
                "muscleOxygen": 71.2,
                "route": {"coordinates": [50.1, 4.2]},
            },
            "userProfile": {"firstName": "Private"},
        },
        geoPolylineDTO={"polyline": "encoded-route"},
    )
    with config.user_context("u_privacy"):
        path, created = history_storage.save_activity_metadata(raw)
        payload = json.loads(path.read_text(encoding="utf-8"))
        serialized = path.read_text(encoding="utf-8").lower()

    assert created is True
    assert payload["summary"]["nested"]["powerBalance"] == 49.5
    assert payload["summary"]["connectIQ"]["muscleOxygen"] == 71.2
    for forbidden in (
        "latitude",
        "longitude",
        "coordinates",
        "polyline",
        "ownerfullname",
        "accesstoken",
        "deviceid",
        "userprofile",
        "private athlete",
        "secret-token",
    ):
        assert forbidden not in serialized


def test_history_storage_is_isolated_between_users(isolated_history: Path) -> None:
    with config.user_context("u_alpha"):
        history_storage.save_activity_metadata(activity(0, activity_id="alpha"))
    with config.user_context("u_beta"):
        assert list(history_storage.iter_activity_history()) == []
        history_storage.save_activity_metadata(activity(1, activity_id="beta"))
    with config.user_context("u_alpha"):
        assert [item["activityId"] for item in history_storage.iter_activity_history()] == ["alpha"]


def test_inspection_streams_local_history_without_second_garmin_activity_call(
    isolated_history: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = "u_local_inspection"
    initialize(user_id, scope="2_years", page_size=10)
    with config.user_context(user_id):
        history_storage.save_activity_metadata(activity(0, sport="running"))
        history_storage.save_activity_metadata(activity(1, sport="cycling"))
        # A valid older file may remain from a previous broader scope. Local
        # inspection must honor the currently frozen cutoff without deleting it.
        history_storage.save_activity_metadata(
            activity(2, day=date(2020, 1, 1), sport="lap_swimming")
        )

    class WorkoutsOnly:
        def get_activities(self, start, limit):
            raise AssertionError("L’inspection ne doit plus rappeler get_activities")

        def get_workouts(self, start, limit):
            raise AssertionError("L’inspection locale ne doit rappeler aucun endpoint Garmin")

    with config.user_context(user_id):
        state = onboarding.inspect_garmin_account(WorkoutsOnly(), force=True)

    assert state["garmin_summary"]["activites_trouvees"] == 2
    assert state["garmin_summary"]["sports_detectes"] == {"course": 1, "vélo": 1}


def test_timings_and_real_counters_are_checkpointed(
    isolated_history: Path, memory_metadata: set[str]
) -> None:
    initialize("u_timings", page_size=2)
    ticks = iter(index / 10 for index in range(20))
    with config.user_context("u_timings"):
        state = history_pagination.paginate_activity_history(
            FakeGarmin([activity(index) for index in range(3)]),
            clock=lambda: next(ticks),
        )

    assert state["pages_examined"] == 2
    assert state["activities_examined"] == 3
    assert state["metadata_saved"] == 3
    assert state["metadata_existing"] == 0
    assert state["timings"]["fetch_seconds_total"] == 0.2
    assert state["timings"]["persist_seconds_total"] == 0.2
    assert state["timings"]["pagination_seconds_total"] == 1.0
    assert [page["activities"] for page in state["timings"]["pages"]] == [2, 1]
    assert all(page["fetch_seconds"] >= 0 for page in state["timings"]["pages"])
