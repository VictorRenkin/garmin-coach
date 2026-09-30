from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from garmin_coach import (
    config,
    history_import,
    history_storage,
    multisport_details,
)


REFERENCE = date(2026, 9, 30)
NOW = datetime(2026, 9, 30, 8, tzinfo=timezone.utc)


def activity(
    activity_id: str,
    sport: str,
    *,
    distance: float | None = 5000,
    indoor: bool | None = None,
    hr: bool = False,
    power: bool = False,
    sets: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "activityId": activity_id,
        "activityType": {"typeKey": sport},
        "startTimeLocal": "2026-09-29T08:00:00",
        "duration": 1800,
    }
    if distance is not None:
        payload["distance"] = distance
    if indoor is not None:
        payload["isIndoor"] = indoor
    if hr:
        payload["averageHR"] = 145
    if power:
        payload["averagePower"] = 220
    if sets:
        payload["exerciseSetCount"] = 4
    return payload


class FakeClient:
    def __init__(self, responses: dict[str, object] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def __getattr__(self, name: str):
        if name not in multisport_details.ENDPOINTS:
            raise AttributeError(name)

        def call(*args):
            self.calls.append((name, args))
            response = self.responses.get(name, {"endpoint": name, "value": 1})
            if isinstance(response, Exception):
                raise response
            return response

        return call


class HttpError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


@pytest.fixture
def isolated_details(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    monkeypatch.delenv("MOTIONEST_HISTORY_PAGE_SIZE", raising=False)
    return tmp_path


def prepare_history(user_id: str, activities: list[dict[str, Any]]) -> None:
    with config.user_context(user_id):
        history_import.initialize_history_import(
            "2_years", reference_date=REFERENCE, now=NOW
        )
        for item in activities:
            history_storage.save_activity_metadata(item, imported_at=NOW)
        history_import.update_history_import(
            {
                "status": "complete",
                "pagination_complete": True,
                "metadata_saved": len(activities),
            },
            now=NOW,
        )


@pytest.mark.parametrize(
    ("metadata", "called", "avoided"),
    [
        (
            activity("run", "running", hr=True),
            {
                "get_activity_details",
                "get_activity_splits",
                "get_activity_typed_splits",
                "get_activity_split_summaries",
                "get_activity_hr_in_timezones",
                "get_activity_weather",
                "get_activity_gear",
            },
            3,
        ),
        (
            activity("bike", "cycling", power=True),
            {
                "get_activity_details",
                "get_activity_splits",
                "get_activity_typed_splits",
                "get_activity_split_summaries",
                "get_activity_power_in_timezones",
                "get_activity_weather",
                "get_activity_gear",
            },
            3,
        ),
        (
            activity("swim", "lap_swimming"),
            {
                "get_activity_details",
                "get_activity_splits",
                "get_activity_typed_splits",
                "get_activity_split_summaries",
                "get_activity_gear",
            },
            5,
        ),
        (
            activity("walk", "walking"),
            {
                "get_activity_details",
                "get_activity_splits",
                "get_activity_typed_splits",
                "get_activity_split_summaries",
                "get_activity_weather",
                "get_activity_gear",
            },
            4,
        ),
        (
            activity("hike", "hiking"),
            {
                "get_activity_details",
                "get_activity_splits",
                "get_activity_typed_splits",
                "get_activity_split_summaries",
                "get_activity_weather",
                "get_activity_gear",
            },
            4,
        ),
        (
            activity("strength", "strength_training", distance=None, sets=True),
            {
                "get_activity_details",
                "get_activity_exercise_sets",
                "get_activity_gear",
            },
            7,
        ),
        (
            activity("unknown", "quantum_ball", distance=None),
            {"get_activity_details", "get_activity_weather", "get_activity_gear"},
            7,
        ),
    ],
)
def test_dynamic_endpoint_plan_by_sport_and_signals(metadata, called, avoided) -> None:
    plan = multisport_details.plan_activity_endpoints(metadata)

    assert {name for name, item in plan.items() if item["applicable"]} == called
    assert sum(not item["applicable"] for item in plan.values()) == avoided
    assert plan["get_activity"]["reason"] == "metadata_already_available"


def test_hr_power_and_indoor_signals_are_independent() -> None:
    no_signals = multisport_details.plan_activity_endpoints(
        activity("one", "custom", distance=None, indoor=True)
    )
    with_signals = multisport_details.plan_activity_endpoints(
        activity("two", "custom", distance=None, indoor=False, hr=True, power=True)
    )

    assert no_signals["get_activity_hr_in_timezones"]["applicable"] is False
    assert no_signals["get_activity_power_in_timezones"]["applicable"] is False
    assert no_signals["get_activity_weather"]["applicable"] is False
    assert with_signals["get_activity_hr_in_timezones"]["applicable"] is True
    assert with_signals["get_activity_power_in_timezones"]["applicable"] is True
    assert with_signals["get_activity_weather"]["applicable"] is True


def test_successful_running_import_uses_maxpoly_zero_and_never_get_activity(
    isolated_details: Path,
) -> None:
    metadata = activity("run-success", "running", hr=True)
    client = FakeClient()
    with config.user_context("u_success"):
        state = multisport_details.fetch_activity_endpoints(
            client, metadata, now=NOW
        )

    called_names = [name for name, _ in client.calls]
    assert state["complete"] is True
    assert "get_activity" not in called_names
    assert "get_activity_typed_splits" in called_names
    details_call = next(args for name, args in client.calls if name == "get_activity_details")
    assert details_call == ("run-success", 10_000, 0)
    assert state["endpoints"]["get_activity"]["status"] == "not_applicable"
    assert state["performance"]["network_calls"] == 7


def test_valid_empty_response_is_distinct_from_success(isolated_details: Path) -> None:
    metadata = activity("empty", "quantum_ball", distance=None, indoor=True)
    client = FakeClient({"get_activity_gear": {}})
    with config.user_context("u_empty"):
        state = multisport_details.fetch_activity_endpoints(client, metadata, now=NOW)

    assert state["complete"] is True
    assert state["endpoints"]["get_activity_gear"]["status"] == "empty"
    assert state["endpoints"]["get_activity_details"]["status"] == "success"


def test_404_is_terminal_unsupported_not_a_failure(isolated_details: Path) -> None:
    metadata = activity("unsupported", "custom", distance=None, indoor=True)
    client = FakeClient({"get_activity_gear": HttpError(404)})
    with config.user_context("u_404"):
        state = multisport_details.fetch_activity_endpoints(client, metadata, now=NOW)

    endpoint = state["endpoints"]["get_activity_gear"]
    assert endpoint["status"] == "unsupported"
    assert endpoint["http_status"] == 404
    assert state["complete"] is True


def test_wrapped_garminconnect_404_is_also_terminal_unsupported(
    isolated_details: Path,
) -> None:
    metadata = activity("wrapped-404", "custom", distance=None, indoor=True)
    wrapped = multisport_details.GarminConnectConnectionError(
        "API client error (404): endpoint missing"
    )
    with config.user_context("u_wrapped_404"):
        state = multisport_details.fetch_activity_endpoints(
            FakeClient({"get_activity_gear": wrapped}), metadata, now=NOW
        )

    assert state["endpoints"]["get_activity_gear"]["status"] == "unsupported"
    assert state["endpoints"]["get_activity_gear"]["http_status"] == 404
    assert state["complete"] is True


@pytest.mark.parametrize(
    ("error", "status", "http_status"),
    [
        (TimeoutError("slow"), "retry_pending", None),
        (ConnectionError("offline"), "retry_pending", None),
        (HttpError(429), "rate_limited", 429),
        (HttpError(401), "auth_failed", 401),
        (RuntimeError("invalid payload"), "failed", None),
    ],
)
def test_transient_and_auth_errors_never_become_empty_or_complete(
    isolated_details: Path, error: Exception, status: str, http_status: int | None
) -> None:
    metadata = activity(f"error-{status}", "custom", distance=None, indoor=True)
    client = FakeClient({"get_activity_details": error})
    with config.user_context(f"u_{status}"):
        with pytest.raises(multisport_details.DetailImportInterrupted):
            multisport_details.fetch_activity_endpoints(client, metadata, now=NOW)
        saved = multisport_details.load_detail_state(metadata["activityId"])

    assert saved is not None
    endpoint = saved["endpoints"]["get_activity_details"]
    assert endpoint["status"] == status
    assert endpoint["http_status"] == http_status
    assert endpoint["attempts"] == 1
    assert saved["complete"] is False


def test_partial_resume_does_not_repeat_successful_endpoint(
    isolated_details: Path,
) -> None:
    metadata = activity("resume", "running", hr=True)
    first = FakeClient({"get_activity_splits": TimeoutError("slow")})
    with config.user_context("u_resume"):
        with pytest.raises(multisport_details.DetailImportInterrupted):
            multisport_details.fetch_activity_endpoints(first, metadata, now=NOW)
        partial = multisport_details.load_detail_state("resume")

    assert partial is not None
    assert partial["endpoints"]["get_activity_details"]["status"] == "success"
    assert [name for name, _ in first.calls] == [
        "get_activity_details",
        "get_activity_splits",
    ]

    second = FakeClient()
    with config.user_context("u_resume"):
        complete = multisport_details.fetch_activity_endpoints(second, metadata, now=NOW)

    assert complete["complete"] is True
    assert "get_activity_details" not in [name for name, _ in second.calls]
    assert complete["endpoints"]["get_activity_details"]["attempts"] == 1
    assert complete["endpoints"]["get_activity_splits"]["attempts"] == 2


def test_complete_activity_is_not_downloaded_again(isolated_details: Path) -> None:
    metadata = activity("complete", "cycling", power=True)
    with config.user_context("u_complete"):
        multisport_details.fetch_activity_endpoints(FakeClient(), metadata, now=NOW)
        second_client = FakeClient()
        second = multisport_details.fetch_activity_endpoints(
            second_client, metadata, now=NOW
        )

    assert second["complete"] is True
    assert second_client.calls == []


def test_detail_sanitizer_removes_nested_gps_identity_and_secret_values(
    isolated_details: Path,
) -> None:
    metadata = activity("private", "running", hr=True)
    responses = {
        "get_activity_details": {
            "metricDescriptors": [
                {"metricsIndex": 0, "key": "directHeartRate"},
                {"metricsIndex": 1, "key": "directLatitude"},
            ],
            "activityDetailMetrics": [{"metrics": [145, 50.6]}],
            "owner": {"fullName": "Private Athlete"},
            "nested": {"accessToken": "secret-value", "sportLoad": 42},
        },
        "get_activity_splits": {
            "laps": [{"distance": 1000, "coordinates": [50.6, 4.2]}]
        },
    }
    with config.user_context("u_private"):
        state = multisport_details.fetch_activity_endpoints(
            FakeClient(responses), metadata, now=NOW
        )
        target = multisport_details.detail_import_path("private")
        serialized = target.read_text(encoding="utf-8").lower()

    details = state["endpoints"]["get_activity_details"]["data"]
    assert details["samples"] == [{"directHeartRate": 145}]
    assert details["nested"]["sportLoad"] == 42
    for forbidden in (
        "latitude",
        "coordinates",
        "private athlete",
        "secret-value",
        "accesstoken",
        "fullname",
    ):
        assert forbidden not in serialized


def test_detail_state_is_written_atomically(
    isolated_details: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def observed_replace(source, destination) -> None:
        source_path, destination_path = Path(source), Path(destination)
        assert source_path.suffix == ".tmp"
        json.loads(source_path.read_text(encoding="utf-8"))
        calls.append((source_path, destination_path))
        real_replace(source, destination)

    monkeypatch.setattr(multisport_details.os, "replace", observed_replace)
    with config.user_context("u_atomic_detail"):
        multisport_details.fetch_activity_endpoints(
            FakeClient(), activity("atomic", "custom", distance=None, indoor=True), now=NOW
        )
        target = multisport_details.detail_import_path("atomic")

    assert calls
    assert all(destination == target for _, destination in calls)
    assert not list(target.parent.glob("*.tmp"))


def test_sequential_import_updates_real_progress_and_performance(
    isolated_details: Path,
) -> None:
    activities = [
        activity("a-run", "running", hr=True),
        activity("b-strength", "strength_training", distance=None, sets=True),
        activity("c-unknown", "quantum_ball", distance=None),
    ]
    prepare_history("u_sequential", activities)
    client = FakeClient()
    ticks = iter(index / 100 for index in range(1000))
    with config.user_context("u_sequential"):
        state = multisport_details.import_multisport_details(
            client, clock=lambda: next(ticks), now=NOW
        )

    assert state["activities_detail_complete"] == 3
    assert state["activities_detail_pending"] == 0
    assert state["endpoints_success"] == 13
    assert state["endpoints_not_applicable"] == 17
    assert state["timings"]["detail_network_calls"] == 13
    assert state["timings"]["detail_calls_avoided_not_applicable"] == 17
    assert state["timings"]["detail_approximate_bytes"] > 0
    assert state["timings"]["details_seconds_total"] > 0
    called_ids = [str(args[0]) for _, args in client.calls]
    assert called_ids == sorted(called_ids)


def test_import_failure_is_retryable_and_keeps_real_endpoint_counters(
    isolated_details: Path,
) -> None:
    metadata = activity("retry-import", "custom", distance=None, indoor=True)
    prepare_history("u_import_error", [metadata])
    with config.user_context("u_import_error"):
        with pytest.raises(multisport_details.DetailImportInterrupted):
            multisport_details.import_multisport_details(
                FakeClient({"get_activity_details": HttpError(429)}), now=NOW
            )
        state = history_import.load_history_import(detect_stale=False)

    assert state["status"] == "error"
    assert state["retryable"] is True
    assert state["error"] == "details_rate_limited"
    assert state["activities_detail_pending"] == 1
    assert state["activities_retrying"] == 1
    assert state["endpoints_rate_limited"] == 1


def test_detail_storage_is_strictly_isolated_between_users(
    isolated_details: Path,
) -> None:
    metadata = activity("shared-id", "custom", distance=None, indoor=True)
    with config.user_context("u_alpha"):
        multisport_details.fetch_activity_endpoints(FakeClient(), metadata, now=NOW)
        alpha_path = multisport_details.detail_import_path("shared-id")
    with config.user_context("u_beta"):
        assert multisport_details.load_detail_state("shared-id") is None
        multisport_details.fetch_activity_endpoints(FakeClient(), metadata, now=NOW)
        beta_path = multisport_details.detail_import_path("shared-id")

    assert alpha_path != beta_path
    assert alpha_path.is_file() and beta_path.is_file()


def test_missing_client_method_is_unsupported_without_network_call(
    isolated_details: Path,
) -> None:
    class DetailsOnly:
        def __init__(self) -> None:
            self.calls = 0

        def get_activity_details(self, activity_id, maxchart, maxpoly):
            self.calls += 1
            return {"sportLoad": 1}

    client = DetailsOnly()
    metadata = activity("limited", "custom", distance=None, indoor=True)
    with config.user_context("u_limited"):
        state = multisport_details.fetch_activity_endpoints(client, metadata, now=NOW)

    assert client.calls == 1
    assert state["endpoints"]["get_activity_gear"]["status"] == "unsupported"
    assert state["endpoints"]["get_activity_gear"]["attempts"] == 0
    assert state["complete"] is True
