from __future__ import annotations

import threading
import time
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from garmin_coach import config, history_import, history_storage, multisport_details


NOW = datetime(2026, 9, 30, 8, tzinfo=timezone.utc)


def metadata(activity_id: str) -> dict[str, Any]:
    return {
        "activityId": activity_id,
        "activityType": {"typeKey": "custom_indoor"},
        "startTimeLocal": "2026-09-29T08:00:00",
        "duration": 600,
        "isIndoor": True,
    }


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")


def prepare(user_id: str, count: int) -> None:
    with config.user_context(user_id):
        history_import.initialize_history_import(
            "2_years", reference_date=date(2026, 9, 30), now=NOW
        )
        for index in range(count):
            history_storage.save_activity_metadata(metadata(f"a-{index:03}"), imported_at=NOW)
        history_import.update_history_import(
            {"status": "complete", "pagination_complete": True}, now=NOW
        )


class SharedProbe:
    def __init__(self, latency: float = 0.0) -> None:
        self.latency = latency
        self.lock = threading.Lock()
        self.calls: list[tuple[str, str, float]] = []
        self.clients: list[ProbeClient] = []
        self.active = 0
        self.peak = 0
        self.failures: dict[tuple[str, str], list[Exception]] = {}

    def factory(self) -> "ProbeClient":
        client = ProbeClient(self)
        with self.lock:
            self.clients.append(client)
        return client


class ProbeClient:
    def __init__(self, probe: SharedProbe) -> None:
        self.probe = probe
        self.session = object()

    def __getattr__(self, endpoint: str):
        if endpoint not in multisport_details.ENDPOINTS:
            raise AttributeError(endpoint)

        def call(activity_id: str, *args: object) -> dict[str, object]:
            with self.probe.lock:
                self.probe.active += 1
                self.probe.peak = max(self.probe.peak, self.probe.active)
                self.probe.calls.append((endpoint, activity_id, time.monotonic()))
                failures = self.probe.failures.get((endpoint, activity_id), [])
                failure = failures.pop(0) if failures else None
            try:
                if self.probe.latency:
                    time.sleep(self.probe.latency)
                if failure:
                    raise failure
                return {"endpoint": endpoint, "activity": activity_id}
            finally:
                with self.probe.lock:
                    self.probe.active -= 1

        return call


class HttpFailure(RuntimeError):
    def __init__(self, status_code: int, retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.retry_after = retry_after


def run(user_id: str, probe: SharedProbe, workers: int, **kwargs: object) -> dict[str, Any]:
    with config.user_context(user_id):
        return multisport_details.import_multisport_details(
            client_factory=probe.factory,
            worker_count=workers,
            now=NOW,
            **kwargs,
        )


@pytest.mark.parametrize("workers", [1, 3, 4, 5])
def test_worker_configuration_is_bounded_and_uses_independent_clients(
    isolated: None, workers: int
) -> None:
    prepare(f"workers-{workers}", 12)
    probe = SharedProbe(0.001)
    state = run(f"workers-{workers}", probe, workers)

    assert state["activities_detail_complete"] == 12
    assert len({id(client) for client in probe.clients}) == workers
    assert len({id(client.session) for client in probe.clients}) == workers
    assert state["timings"]["detail_worker_count"] == workers
    assert state["timings"]["detail_peak_in_flight"] <= workers * 2
    assert probe.peak <= workers


def test_one_three_and_five_workers_are_functionally_equivalent(isolated: None) -> None:
    results = {}
    call_counts = {}
    for workers in (1, 3, 5):
        user = f"equivalent-{workers}"
        prepare(user, 10)
        probe = SharedProbe(0.001)
        state = run(user, probe, workers)
        results[workers] = (
            state["activities_detail_complete"],
            state["endpoints_success"],
            state["endpoints_not_applicable"],
        )
        call_counts[workers] = len(probe.calls)

    assert len(set(results.values())) == 1
    assert len(set(call_counts.values())) == 1


def test_user_context_is_explicit_inside_workers_and_storage_is_isolated(
    isolated: None,
) -> None:
    prepare("alpha", 4)
    prepare("beta", 4)
    run("alpha", SharedProbe(), 3)
    run("beta", SharedProbe(), 3)

    alpha = config.USERS_DATA_DIR / "alpha" / "activity_details_multisport"
    beta = config.USERS_DATA_DIR / "beta" / "activity_details_multisport"
    assert len(list(alpha.glob("*.json"))) == 4
    assert len(list(beta.glob("*.json"))) == 4
    assert alpha != beta


def test_duplicate_metadata_is_never_processed_twice(isolated: None, monkeypatch) -> None:
    prepare("dedupe", 1)
    duplicate = metadata("a-000")
    monkeypatch.setattr(
        multisport_details,
        "_selected_metadata",
        lambda history: iter([duplicate, duplicate, duplicate]),
    )
    probe = SharedProbe(0.002)
    run("dedupe", probe, 3)

    assert Counter(activity_id for _, activity_id, _ in probe.calls) == {"a-000": 2}


def test_rate_limit_pauses_globally_and_honors_retry_after(isolated: None) -> None:
    prepare("rate", 8)
    probe = SharedProbe(0.002)
    probe.failures[("get_activity_details", "a-000")] = [HttpFailure(429, 0.04)]
    started = time.monotonic()
    state = run("rate", probe, 3)
    elapsed = time.monotonic() - started

    timings = state["timings"]
    assert elapsed >= 0.035
    assert timings["detail_rate_limit_pauses"] == 1
    assert timings["detail_rate_limit_pause_seconds"] >= 0.035
    assert timings["detail_retries"] == 1
    assert state["activities_detail_complete"] == 8


def test_auth_failure_stops_new_calls_globally(isolated: None) -> None:
    prepare("auth", 20)
    probe = SharedProbe(0.003)
    probe.failures[("get_activity_details", "a-000")] = [HttpFailure(401)]
    with pytest.raises(multisport_details.DetailImportInterrupted) as captured:
        run("auth", probe, 3)

    with config.user_context("auth"):
        state = history_import.load_history_import(detect_stale=False)
    assert captured.value.status == "auth_failed"
    assert state["timings"]["detail_auth_stops"] == 1
    assert len(probe.calls) < 40
    assert state["retryable"] is True


def test_timeout_keeps_other_results_and_resume_only_fetches_pending(
    isolated: None,
) -> None:
    prepare("resume", 6)
    first = SharedProbe(0.001)
    first.failures[("get_activity_details", "a-000")] = [
        TimeoutError("slow") for _ in range(3)
    ]
    with pytest.raises(multisport_details.DetailImportInterrupted):
        run("resume", first, 3)
    with config.user_context("resume"):
        partial = history_import.load_history_import(detect_stale=False)
    assert partial["activities_detail_complete"] == 5

    second = SharedProbe()
    final = run("resume", second, 3)
    assert final["activities_detail_complete"] == 6
    assert {activity_id for _, activity_id, _ in second.calls} == {"a-000"}


def test_bounded_queue_does_not_scale_with_history(isolated: None) -> None:
    prepare("bounded", 80)
    state = run("bounded", SharedProbe(0.0005), 3)
    timings = state["timings"]
    assert timings["detail_queue_capacity"] == 6
    assert timings["detail_peak_in_flight"] <= 6
    assert state["activities_detail_complete"] == 80


def test_deterministic_worker_benchmark_preserves_calls_and_results(
    isolated: None,
) -> None:
    report: dict[int, dict[str, Any]] = {}
    signatures = set()
    for workers in (1, 3, 4, 5):
        user = f"benchmark-{workers}"
        prepare(user, 12)
        probe = SharedProbe(0.01)
        started = time.perf_counter()
        state = run(user, probe, workers)
        elapsed = time.perf_counter() - started
        signature = (
            state["activities_detail_complete"],
            state["endpoints_success"],
            state["endpoints_not_applicable"],
        )
        signatures.add(signature)
        report[workers] = {
            "seconds": round(elapsed, 4),
            "calls": len(probe.calls),
            "peak_concurrency": probe.peak,
            "equivalent_signature": signature,
        }
    baseline = report[1]["seconds"]
    for result in report.values():
        result["speedup"] = round(baseline / result["seconds"], 3)

    print("BENCHMARK=" + json.dumps(report, sort_keys=True))
    assert len(signatures) == 1
    assert {result["calls"] for result in report.values()} == {24}
    assert report[1]["peak_concurrency"] == 1
    for workers in (3, 4, 5):
        assert 2 <= report[workers]["peak_concurrency"] <= workers
