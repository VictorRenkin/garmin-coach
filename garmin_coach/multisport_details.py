"""Sequential and resumable Garmin endpoint import for every sport."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
    GarthException,
)

from .config import (
    MULTISPORT_DETAILS_DIR,
    current_user_id,
    ensure_directories,
    user_context,
)
from .history_import import load_history_import, update_history_import
from .history_storage import (
    is_sensitive_metadata_key,
    iter_activity_history,
    sanitize_activity_metadata,
)


SCHEMA_VERSION = 1
ENDPOINTS = (
    "get_activity",
    "get_activity_details",
    "get_activity_splits",
    "get_activity_typed_splits",
    "get_activity_split_summaries",
    "get_activity_hr_in_timezones",
    "get_activity_power_in_timezones",
    "get_activity_weather",
    "get_activity_exercise_sets",
    "get_activity_gear",
)
TERMINAL_STATUSES = {"success", "empty", "not_applicable", "unsupported"}
NONTERMINAL_STATUSES = {
    "retry_pending",
    "rate_limited",
    "auth_failed",
    "failed",
}
ALL_STATUSES = TERMINAL_STATUSES | NONTERMINAL_STATUSES
_WRITE_LOCK = threading.Lock()
DEFAULT_DETAIL_WORKERS = 3
DEFAULT_QUEUE_MULTIPLIER = 2
MAX_DETAIL_WORKERS = 5
DEFAULT_NETWORK_RETRIES = 2


class DetailImportInterrupted(RuntimeError):
    def __init__(self, status: str) -> None:
        super().__init__(f"Import détaillé interrompu : {status}.")
        self.status = status


class ConcurrentImportStopped(RuntimeError):
    pass


def configured_detail_workers() -> int:
    raw = os.getenv("GARMIN_DETAIL_WORKERS", str(DEFAULT_DETAIL_WORKERS))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("GARMIN_DETAIL_WORKERS doit être un entier.") from exc
    if not 1 <= value <= MAX_DETAIL_WORKERS:
        raise ValueError("GARMIN_DETAIL_WORKERS doit être compris entre 1 et 5.")
    return value


def configured_detail_queue_size(worker_count: int) -> int:
    raw = os.getenv("GARMIN_DETAIL_QUEUE_MULTIPLIER", str(DEFAULT_QUEUE_MULTIPLIER))
    try:
        multiplier = int(raw)
    except ValueError as exc:
        raise ValueError("GARMIN_DETAIL_QUEUE_MULTIPLIER doit être un entier.") from exc
    if not 1 <= multiplier <= 10:
        raise ValueError("GARMIN_DETAIL_QUEUE_MULTIPLIER doit être compris entre 1 et 10.")
    return worker_count * multiplier


def _retry_after(exc: BaseException) -> float | None:
    value = getattr(exc, "retry_after", None)
    response = getattr(exc, "response", None)
    if value is None and response is not None:
        value = getattr(response, "headers", {}).get("Retry-After")
    try:
        return max(0.0, float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


@dataclass
class _ControllerMetrics:
    peak_requests: int = 0
    network_seconds: float = 0.0
    rate_limit_pauses: int = 0
    rate_limit_pause_seconds: float = 0.0
    retries: int = 0
    auth_stops: int = 0


class GlobalRequestController:
    """One adaptive request gate shared by every activity worker."""

    def __init__(
        self,
        worker_count: int,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        backoff_base: float = 1.0,
        backoff_cap: float = 60.0,
    ) -> None:
        self.worker_count = worker_count
        self.effective_limit = worker_count
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.metrics = _ControllerMetrics()
        self._condition = threading.Condition()
        self._active = 0
        self._paused_until = 0.0
        self._rate_streak = 0
        self._recovery_successes = 0
        self._auth_stopped = False

    @property
    def auth_stopped(self) -> bool:
        with self._condition:
            return self._auth_stopped

    def before_request(self) -> float:
        while True:
            with self._condition:
                if self._auth_stopped:
                    raise ConcurrentImportStopped("auth_failed")
                delay = self._paused_until - self.monotonic()
                if delay <= 0 and self._active < self.effective_limit:
                    self._active += 1
                    self.metrics.peak_requests = max(
                        self.metrics.peak_requests, self._active
                    )
                    return self.monotonic()
                self._condition.wait(
                    timeout=min(max(delay if delay > 0 else 0.05, 0.001), 0.05)
                )

    def after_request(
        self, started: float, status: str, retry_after: float | None = None
    ) -> None:
        with self._condition:
            self._active = max(0, self._active - 1)
            self.metrics.network_seconds += max(0.0, self.monotonic() - started)
            if status == "rate_limited":
                self._rate_streak += 1
                delay = retry_after
                if delay is None:
                    delay = min(
                        self.backoff_cap,
                        self.backoff_base * (2 ** (self._rate_streak - 1)),
                    )
                new_until = self.monotonic() + delay
                added = max(0.0, new_until - max(self._paused_until, self.monotonic()))
                self._paused_until = max(self._paused_until, new_until)
                self.effective_limit = 1
                self._recovery_successes = 0
                self.metrics.rate_limit_pauses += 1
                self.metrics.rate_limit_pause_seconds += added
            elif status == "auth_failed":
                if not self._auth_stopped:
                    self.metrics.auth_stops += 1
                self._auth_stopped = True
            elif status in TERMINAL_STATUSES:
                self._rate_streak = 0
                if self.effective_limit < self.worker_count:
                    self._recovery_successes += 1
                    if self._recovery_successes >= 5:
                        self.effective_limit += 1
                        self._recovery_successes = 0
            self._condition.notify_all()

    def record_retry(self) -> None:
        with self._condition:
            self.metrics.retries += 1


def _iso(moment: datetime | None = None) -> str:
    value = moment or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _activity_id(activity: dict[str, Any]) -> str:
    activity_id = str(activity.get("activityId") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", activity_id):
        raise ValueError("activityId Garmin absent ou invalide.")
    return activity_id


def detail_import_path(activity_id: str | int) -> Path:
    value = str(activity_id).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError("activityId Garmin invalide.")
    return Path(os.fspath(MULTISPORT_DETAILS_DIR)) / f"{value}.json"


def _walk_items(value: object) -> Iterator[tuple[str, object]]:
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key), nested
            yield from _walk_items(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_items(nested)


def _sport_key(activity: dict[str, Any]) -> str:
    kind = activity.get("activityType")
    value = kind.get("typeKey") if isinstance(kind, dict) else kind
    return str(value or activity.get("sportType") or "unknown").lower()


def _positive_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _has_signal(activity: dict[str, Any], signal: str) -> bool:
    for key, value in _walk_items(activity):
        normalized = re.sub(r"[^a-z0-9]", "", key.lower())
        matched = False
        if signal == "hr":
            matched = "heartrate" in normalized or normalized in {
                "averagehr",
                "maxhr",
                "minhr",
                "hr",
                "hasheartrate",
            }
        elif signal == "power":
            matched = any(word in normalized for word in ("power", "watt"))
        elif signal == "sets":
            matched = any(word in normalized for word in ("exerciseset", "setcount", "repetition"))
        elif signal == "splits":
            matched = any(word in normalized for word in ("lapcount", "splitcount"))
        if matched and (value is True or _positive_number(value) or bool(value)):
            return True
    return False


def _explicit_indoor(activity: dict[str, Any], sport: str) -> bool:
    for key, value in _walk_items(activity):
        normalized = re.sub(r"[^a-z0-9]", "", key.lower())
        if normalized in {"isindoor", "indoor"} and value is True:
            return True
    return any(
        marker in sport
        for marker in (
            "indoor",
            "treadmill",
            "lap_swimming",
            "pool_swimming",
            "strength",
            "yoga",
            "pilates",
        )
    )


def _has_split_signal(activity: dict[str, Any], sport: str) -> bool:
    if _positive_number(activity.get("distance")) or _has_signal(activity, "splits"):
        return True
    return any(
        marker in sport
        for marker in (
            "run",
            "cycl",
            "bike",
            "swim",
            "walk",
            "hik",
            "ski",
            "row",
        )
    )


def plan_activity_endpoints(activity: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Plan endpoints from activity signals without restricting unknown sports."""
    sport = _sport_key(activity)
    split_signal = _has_split_signal(activity, sport)
    hr_signal = _has_signal(activity, "hr")
    power_signal = _has_signal(activity, "power")
    sets_signal = _has_signal(activity, "sets") or any(
        marker in sport for marker in ("strength", "cardio", "hiit", "crossfit")
    )
    indoor = _explicit_indoor(activity, sport)
    applicable = {
        # The paginated list already supplies the useful summary metadata. A
        # per-activity get_activity() call would duplicate it without measured
        # value, so the decision is persisted but no network call is made.
        "get_activity": (False, "metadata_already_available"),
        "get_activity_details": (True, "sport_metrics"),
        "get_activity_splits": (split_signal, "split_or_distance_signal"),
        "get_activity_typed_splits": (split_signal, "split_or_distance_signal"),
        "get_activity_split_summaries": (split_signal, "split_or_distance_signal"),
        "get_activity_hr_in_timezones": (hr_signal, "heart_rate_signal"),
        "get_activity_power_in_timezones": (power_signal, "power_signal"),
        "get_activity_weather": (not indoor, "not_known_indoor"),
        "get_activity_exercise_sets": (sets_signal, "exercise_set_signal"),
        "get_activity_gear": (True, "sport_equipment_may_exist"),
    }
    return {
        endpoint: {"applicable": selected, "reason": reason}
        for endpoint, (selected, reason) in applicable.items()
    }


def _http_status(exc: Exception) -> int | None:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        wrapped_error = getattr(current, "error", None)
        for candidate in (
            getattr(current, "status_code", None),
            getattr(current, "code", None),
            getattr(getattr(current, "response", None), "status_code", None),
            getattr(getattr(wrapped_error, "response", None), "status_code", None),
        ):
            if isinstance(candidate, int):
                return candidate
        match = re.search(r"\b([45]\d\d)\b", str(current))
        if match:
            return int(match.group(1))
        current = current.__cause__ or current.__context__
    return None


def _failure_status(exc: Exception) -> tuple[str, int | None, str]:
    http_status = _http_status(exc)
    if http_status == 404:
        return "unsupported", http_status, type(exc).__name__
    if http_status == 429 or isinstance(exc, GarminConnectTooManyRequestsError):
        return "rate_limited", http_status or 429, type(exc).__name__
    if http_status in {401, 403} or isinstance(exc, GarminConnectAuthenticationError):
        return "auth_failed", http_status, type(exc).__name__
    if isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            GarminConnectConnectionError,
            GarthException,
        ),
    ):
        return "retry_pending", http_status, type(exc).__name__
    return "failed", http_status, type(exc).__name__


def _empty_response(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _sanitize_endpoint_response(endpoint: str, response: object) -> object:
    """Apply shared privacy rules plus descriptor-aware chart sanitizing."""
    if endpoint != "get_activity_details" or not isinstance(response, dict):
        return sanitize_activity_metadata(response)
    if not response:
        return {}
    descriptors = response.get("metricDescriptors")
    allowed: list[tuple[int, str]] = []
    if isinstance(descriptors, list):
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                continue
            index = descriptor.get("metricsIndex")
            key = str(descriptor.get("key") or "")
            if (
                isinstance(index, int)
                and key
                and not is_sensitive_metadata_key(key)
            ):
                allowed.append((index, key))
    samples: list[dict[str, Any]] = []
    raw_samples = response.get("activityDetailMetrics")
    if isinstance(raw_samples, list):
        for raw_sample in raw_samples:
            metrics = raw_sample.get("metrics") if isinstance(raw_sample, dict) else None
            if not isinstance(metrics, list):
                continue
            sample = {
                key: sanitize_activity_metadata(metrics[index])
                for index, key in allowed
                if index < len(metrics) and metrics[index] is not None
            }
            if sample:
                samples.append(sample)
    remaining = {
        key: value
        for key, value in response.items()
        if key not in {"metricDescriptors", "activityDetailMetrics"}
    }
    sanitized = sanitize_activity_metadata(remaining)
    if not isinstance(sanitized, dict):
        sanitized = {}
    if not sanitized and not allowed and not samples:
        return {}
    sanitized["metric_fields"] = [key for _, key in allowed]
    sanitized["samples"] = samples
    sanitized["sample_count"] = len(samples)
    return sanitized


def _endpoint_template(status: str, reason: str, now: datetime | None) -> dict[str, Any]:
    return {
        "status": status,
        "attempts": 0,
        "reason": reason,
        "first_attempt_at": None,
        "last_attempt_at": None,
        "completed_at": _iso(now) if status in TERMINAL_STATUSES else None,
        "http_status": None,
        "error_type": "",
        "duration_seconds": 0.0,
        "approximate_bytes": 0,
        "data": None,
    }


def _new_detail_state(activity: dict[str, Any], now: datetime | None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "activity_id": _activity_id(activity),
        "activity_type": sanitize_activity_metadata(activity.get("activityType")),
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "complete": False,
        "endpoints": {},
        "performance": {
            "total_seconds": 0.0,
            "network_calls": 0,
            "approximate_data_bytes": 0,
            "calls_avoided_not_applicable": 0,
            "calls_avoided_resume": 0,
        },
    }


def _is_complete(state: dict[str, Any]) -> bool:
    endpoints = state.get("endpoints")
    return bool(
        isinstance(endpoints, dict)
        and set(endpoints) == set(ENDPOINTS)
        and all(
            isinstance(value, dict) and value.get("status") in TERMINAL_STATUSES
            for value in endpoints.values()
        )
    )


def _validate_detail_state(payload: object, expected_id: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("État de détails multisports invalide.")
    activity_id = str(payload.get("activity_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", activity_id):
        raise ValueError("activity_id invalide.")
    if expected_id is not None and activity_id != expected_id:
        raise ValueError("activity_id incohérent.")
    endpoints = payload.get("endpoints")
    if not isinstance(endpoints, dict) or any(key not in ENDPOINTS for key in endpoints):
        raise ValueError("État d’endpoints invalide.")
    for endpoint in endpoints.values():
        if not isinstance(endpoint, dict) or endpoint.get("status") not in ALL_STATUSES:
            raise ValueError("Statut d’endpoint invalide.")
        if not isinstance(endpoint.get("attempts"), int) or endpoint["attempts"] < 0:
            raise ValueError("Nombre de tentatives invalide.")

    def inspect(value: object) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if is_sensitive_metadata_key(key):
                    raise ValueError(f"Champ sensible détecté : {key}.")
                inspect(nested)
        elif isinstance(value, list):
            for nested in value:
                inspect(nested)

    inspect(payload)
    payload["complete"] = _is_complete(payload)
    return payload


def load_detail_state(activity_id: str | int) -> dict[str, Any] | None:
    target = detail_import_path(activity_id)
    if not target.is_file():
        return None
    try:
        return _validate_detail_state(
            json.loads(target.read_text(encoding="utf-8")), str(activity_id)
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _atomic_write_detail(state: dict[str, Any]) -> dict[str, Any]:
    validated = _validate_detail_state(state, str(state.get("activity_id") or ""))
    ensure_directories()
    target = detail_import_path(validated["activity_id"])
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    with _WRITE_LOCK:
        try:
            temporary.write_text(
                json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _validate_detail_state(
                json.loads(temporary.read_text(encoding="utf-8")),
                validated["activity_id"],
            )
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return validated


def _invoke_endpoint(client: Any, endpoint: str, activity_id: str) -> Any:
    method = getattr(client, endpoint)
    if endpoint == "get_activity_details":
        return method(activity_id, 10_000, 0)
    return method(activity_id)


def fetch_activity_endpoints(
    client: Any,
    activity: dict[str, Any],
    *,
    clock: Callable[[], float] = time.perf_counter,
    now: datetime | None = None,
    on_checkpoint: Callable[[dict[str, Any]], None] | None = None,
    request_controller: GlobalRequestController | None = None,
) -> dict[str, Any]:
    """Fetch incomplete endpoints for one activity, saving after every result."""
    activity_id = _activity_id(activity)
    state = load_detail_state(activity_id) or _new_detail_state(activity, now)
    plan = plan_activity_endpoints(activity)
    endpoints = state["endpoints"]
    for name in ENDPOINTS:
        decision = plan[name]
        existing = endpoints.get(name)
        if not decision["applicable"]:
            if not isinstance(existing, dict) or existing.get("status") not in TERMINAL_STATUSES:
                endpoints[name] = _endpoint_template(
                    "not_applicable", decision["reason"], now
                )
        elif isinstance(existing, dict) and existing.get("status") in {
            "success",
            "empty",
        }:
            continue
        elif not callable(getattr(client, name, None)):
            endpoints[name] = _endpoint_template(
                "unsupported", "client_method_unavailable", now
            )
        elif not isinstance(existing, dict) or existing.get("status") == "not_applicable":
            endpoints[name] = _endpoint_template("retry_pending", decision["reason"], now)
    state["complete"] = _is_complete(state)
    state["updated_at"] = _iso(now)
    state["performance"]["calls_avoided_not_applicable"] = sum(
        value.get("status") == "not_applicable" for value in endpoints.values()
    )
    state = _atomic_write_detail(state)
    if on_checkpoint:
        on_checkpoint(state)

    run_started = clock()
    for name in ENDPOINTS:
        endpoint = endpoints[name]
        if endpoint["status"] in TERMINAL_STATUSES:
            if endpoint["status"] != "not_applicable" and endpoint["attempts"] > 0:
                state["performance"]["calls_avoided_resume"] += 1
            continue

        gate_started = (
            request_controller.before_request() if request_controller else None
        )
        attempted_at = _iso(now)
        endpoint["attempts"] += 1
        endpoint["first_attempt_at"] = endpoint.get("first_attempt_at") or attempted_at
        endpoint["last_attempt_at"] = attempted_at
        endpoint["http_status"] = None
        endpoint["error_type"] = ""
        endpoint["data"] = None
        call_started = clock()
        state["performance"]["network_calls"] += 1
        try:
            response = _invoke_endpoint(client, name, activity_id)
            sanitized = _sanitize_endpoint_response(name, response)
            encoded = json.dumps(sanitized, ensure_ascii=False).encode("utf-8")
            endpoint["duration_seconds"] = round(max(0.0, clock() - call_started), 6)
            endpoint["approximate_bytes"] = len(encoded)
            endpoint["data"] = sanitized
            endpoint["status"] = "empty" if _empty_response(sanitized) else "success"
            endpoint["completed_at"] = attempted_at
            if request_controller and gate_started is not None:
                request_controller.after_request(gate_started, endpoint["status"])
        except Exception as exc:
            endpoint["duration_seconds"] = round(max(0.0, clock() - call_started), 6)
            status, http_status, error_type = _failure_status(exc)
            endpoint["status"] = status
            endpoint["http_status"] = http_status
            endpoint["error_type"] = error_type
            endpoint["completed_at"] = attempted_at if status == "unsupported" else None
            if request_controller and gate_started is not None:
                request_controller.after_request(
                    gate_started, status, _retry_after(exc)
                )

        state["performance"]["approximate_data_bytes"] = sum(
            int(value.get("approximate_bytes") or 0) for value in endpoints.values()
        )
        state["performance"]["total_seconds"] = round(
            float(state["performance"].get("total_seconds") or 0.0)
            + max(0.0, clock() - run_started),
            6,
        )
        run_started = clock()
        state["updated_at"] = _iso(now)
        state["complete"] = _is_complete(state)
        state = _atomic_write_detail(state)
        if on_checkpoint:
            on_checkpoint(state)
        if endpoint["status"] in NONTERMINAL_STATUSES:
            raise DetailImportInterrupted(endpoint["status"])

    state["complete"] = _is_complete(state)
    state["updated_at"] = _iso(now)
    state = _atomic_write_detail(state)
    if on_checkpoint:
        on_checkpoint(state)
    return state


def _metadata_in_scope(activity: dict[str, Any], history: dict[str, Any]) -> bool:
    cutoff_raw = history.get("cutoff_date")
    if not cutoff_raw:
        return True
    raw = activity.get("startTimeLocal") or activity.get("startTimeGMT")
    if not isinstance(raw, str):
        return False
    try:
        activity_day = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        cutoff = date.fromisoformat(str(cutoff_raw))
        reference = date.fromisoformat(str(history["reference_date"]))
    except (TypeError, ValueError):
        return False
    return cutoff <= activity_day <= reference


def _selected_metadata(history: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for activity in iter_activity_history():
        if _metadata_in_scope(activity, history):
            yield activity


def _endpoint_counter(state: dict[str, Any] | None) -> Counter[str]:
    if not state:
        return Counter()
    return Counter(
        str(value.get("status"))
        for value in state.get("endpoints", {}).values()
        if isinstance(value, dict)
    )


def _detail_bytes(state: dict[str, Any] | None) -> int:
    if not state:
        return 0
    return int((state.get("performance") or {}).get("approximate_data_bytes") or 0)


def _is_retrying(state: dict[str, Any] | None) -> bool:
    return bool(
        state
        and not state.get("complete")
        and any(
            isinstance(value, dict)
            and value.get("status") in NONTERMINAL_STATUSES
            and int(value.get("attempts") or 0) > 0
            for value in state.get("endpoints", {}).values()
        )
    )


def _initial_progress(history: dict[str, Any]) -> dict[str, Any]:
    progress: dict[str, Any] = {
        "total": 0,
        "complete": 0,
        "pending": 0,
        "retrying": 0,
        "bytes": 0,
        "statuses": Counter(),
    }
    for activity in _selected_metadata(history):
        progress["total"] += 1
        detail = load_detail_state(_activity_id(activity))
        progress["complete"] += int(bool(detail and detail.get("complete")))
        progress["pending"] += int(not detail or not detail.get("complete"))
        progress["retrying"] += int(_is_retrying(detail))
        progress["bytes"] += _detail_bytes(detail)
        progress["statuses"].update(_endpoint_counter(detail))
    return progress


def _apply_progress_delta(
    progress: dict[str, Any],
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    before_complete = bool(before and before.get("complete"))
    after_complete = bool(after.get("complete"))
    progress["complete"] += int(after_complete) - int(before_complete)
    progress["pending"] -= int(after_complete) - int(before_complete)
    progress["retrying"] += int(_is_retrying(after)) - int(_is_retrying(before))
    progress["bytes"] += _detail_bytes(after) - _detail_bytes(before)
    progress["statuses"].subtract(_endpoint_counter(before))
    progress["statuses"].update(_endpoint_counter(after))


def _history_progress_updates(progress: dict[str, Any]) -> dict[str, Any]:
    statuses = progress["statuses"]
    return {
        "activities_detail_complete": max(0, int(progress["complete"])),
        "activities_detail_pending": max(0, int(progress["pending"])),
        "activities_retrying": max(0, int(progress["retrying"])),
        "endpoints_success": max(0, int(statuses["success"])),
        "endpoints_empty": max(0, int(statuses["empty"])),
        "endpoints_not_applicable": max(0, int(statuses["not_applicable"])),
        "endpoints_unsupported": max(0, int(statuses["unsupported"])),
        "endpoints_retry_pending": max(0, int(statuses["retry_pending"])),
        "endpoints_rate_limited": max(0, int(statuses["rate_limited"])),
        "endpoints_auth_failed": max(0, int(statuses["auth_failed"])),
        "endpoints_failed": max(0, int(statuses["failed"])),
    }


def import_multisport_details(
    client: Any | None = None,
    *,
    clock: Callable[[], float] = time.perf_counter,
    now: datetime | None = None,
    client_factory: Callable[[], Any] | None = None,
    worker_count: int | None = None,
    queue_size: int | None = None,
    network_retries: int = DEFAULT_NETWORK_RETRIES,
    controller: GlobalRequestController | None = None,
    activity_source: Iterator[dict[str, Any]] | None = None,
    allow_incomplete_pagination: bool = False,
) -> dict[str, Any]:
    """Import selected activities one by one and resume incomplete endpoints."""
    if client_factory is not None:
        return _import_multisport_details_concurrent(
            client_factory,
            clock=clock,
            now=now,
            worker_count=worker_count or configured_detail_workers(),
            queue_size=queue_size,
            network_retries=network_retries,
            controller=controller,
            activity_source=activity_source,
            allow_incomplete_pagination=allow_incomplete_pagination,
        )
    if client is None:
        raise ValueError("Un client Garmin ou une fabrique de clients est requis.")
    history = load_history_import(detect_stale=False)
    if not history.get("pagination_complete"):
        raise ValueError("La pagination des métadonnées doit être terminée.")
    progress = _initial_progress(history)
    timings = dict(history.get("timings") or {})
    prior_seconds = float(timings.get("details_seconds_total") or 0.0)
    prior_calls = int(timings.get("detail_network_calls") or 0)
    prior_avoided_not_applicable = int(
        timings.get("detail_calls_avoided_not_applicable") or 0
    )
    prior_avoided_resume = int(timings.get("detail_calls_avoided_resume") or 0)
    run_started = clock()
    calls = 0
    avoided_not_applicable = 0
    avoided_resume = 0

    history = update_history_import(
        {
            "status": "running",
            "retryable": False,
            "error": "",
            **_history_progress_updates(progress),
        },
        now=now,
    )

    try:
        for activity in _selected_metadata(history):
            activity_id = _activity_id(activity)
            before = load_detail_state(activity_id)
            current = before
            if before and before.get("complete"):
                avoided_resume += sum(
                    isinstance(value, dict)
                    and value.get("status") in {"success", "empty", "unsupported"}
                    for value in before.get("endpoints", {}).values()
                )
                continue

            def checkpoint(after: dict[str, Any]) -> None:
                nonlocal current, history, calls, avoided_not_applicable, avoided_resume
                before_performance = (current or {}).get("performance") or {}
                after_performance = after.get("performance") or {}
                calls += int(after_performance.get("network_calls") or 0) - int(
                    before_performance.get("network_calls") or 0
                )
                avoided_not_applicable += int(
                    after_performance.get("calls_avoided_not_applicable") or 0
                ) - int(before_performance.get("calls_avoided_not_applicable") or 0)
                avoided_resume += int(
                    after_performance.get("calls_avoided_resume") or 0
                ) - int(before_performance.get("calls_avoided_resume") or 0)
                _apply_progress_delta(progress, current, after)
                current = json.loads(json.dumps(after))
                elapsed = max(0.0, clock() - run_started)
                timings.update(
                    details_seconds_total=round(prior_seconds + elapsed, 6),
                    detail_approximate_bytes=max(0, int(progress["bytes"])),
                    detail_network_calls=prior_calls + calls,
                    detail_calls_avoided_not_applicable=prior_avoided_not_applicable
                    + avoided_not_applicable,
                    detail_calls_avoided_resume=prior_avoided_resume
                    + avoided_resume,
                )
                history = update_history_import(
                    {
                        "status": "running",
                        "timings": timings,
                        **_history_progress_updates(progress),
                    },
                    now=now,
                )

            fetch_activity_endpoints(
                client,
                activity,
                clock=clock,
                now=now,
                on_checkpoint=checkpoint,
            )

        timings.update(
            details_seconds_total=round(
                prior_seconds + max(0.0, clock() - run_started), 6
            ),
            detail_network_calls=prior_calls + calls,
            detail_calls_avoided_not_applicable=prior_avoided_not_applicable
            + avoided_not_applicable,
            detail_calls_avoided_resume=prior_avoided_resume + avoided_resume,
            detail_approximate_bytes=max(0, int(progress["bytes"])),
        )
        return update_history_import(
            {
                "status": "complete",
                "retryable": False,
                "error": "",
                "timings": timings,
                **_history_progress_updates(progress),
            },
            now=now,
        )
    except DetailImportInterrupted as exc:
        timings["details_seconds_total"] = round(
            prior_seconds + max(0.0, clock() - run_started), 6
        )
        timings["detail_network_calls"] = prior_calls + calls
        timings["detail_calls_avoided_not_applicable"] = (
            prior_avoided_not_applicable + avoided_not_applicable
        )
        timings["detail_calls_avoided_resume"] = (
            prior_avoided_resume + avoided_resume
        )
        timings["detail_approximate_bytes"] = max(0, int(progress["bytes"]))
        update_history_import(
            {
                "status": "error",
                "retryable": True,
                "error": f"details_{exc.status}",
                "timings": timings,
                **_history_progress_updates(progress),
            },
            now=now,
        )
        raise
    except Exception as exc:
        timings["details_seconds_total"] = round(
            prior_seconds + max(0.0, clock() - run_started), 6
        )
        timings["detail_network_calls"] = prior_calls + calls
        timings["detail_calls_avoided_not_applicable"] = (
            prior_avoided_not_applicable + avoided_not_applicable
        )
        timings["detail_calls_avoided_resume"] = (
            prior_avoided_resume + avoided_resume
        )
        timings["detail_approximate_bytes"] = max(0, int(progress["bytes"]))
        update_history_import(
            {
                "status": "error",
                "retryable": True,
                "error": f"details_{type(exc).__name__}",
                "timings": timings,
                **_history_progress_updates(progress),
            },
            now=now,
        )
        raise


def _performance_totals(
    history: dict[str, Any],
) -> tuple[int, int, int, int]:
    calls = avoided_not_applicable = avoided_resume = approximate_bytes = 0
    for activity in _selected_metadata(history):
        state = load_detail_state(_activity_id(activity))
        performance = (state or {}).get("performance") or {}
        calls += int(performance.get("network_calls") or 0)
        avoided_not_applicable += int(
            performance.get("calls_avoided_not_applicable") or 0
        )
        avoided_resume += int(performance.get("calls_avoided_resume") or 0)
        approximate_bytes += int(performance.get("approximate_data_bytes") or 0)
    return calls, avoided_not_applicable, avoided_resume, approximate_bytes


def _import_multisport_details_concurrent(
    client_factory: Callable[[], Any],
    *,
    clock: Callable[[], float],
    now: datetime | None,
    worker_count: int,
    queue_size: int | None,
    network_retries: int,
    controller: GlobalRequestController | None,
    activity_source: Iterator[dict[str, Any]] | None,
    allow_incomplete_pagination: bool,
) -> dict[str, Any]:
    """Bounded activity-level concurrency with coordinator-only checkpoints."""
    if not 1 <= worker_count <= MAX_DETAIL_WORKERS:
        raise ValueError("worker_count doit être compris entre 1 et 5.")
    if network_retries < 0 or network_retries > 10:
        raise ValueError("network_retries doit être compris entre 0 et 10.")
    capacity = queue_size or configured_detail_queue_size(worker_count)
    if capacity < worker_count:
        raise ValueError("La file doit pouvoir contenir au moins un travail par worker.")
    user_id = current_user_id()
    if not user_id:
        raise ValueError("Contexte utilisateur explicite requis pour l'import concurrent.")

    history = load_history_import(detect_stale=False)
    if not history.get("pagination_complete") and not allow_incomplete_pagination:
        raise ValueError("La pagination des métadonnées doit être terminée.")
    request_gate = controller or GlobalRequestController(worker_count)
    initial_calls, initial_na, initial_resume, _ = _performance_totals(history)
    prior_timings = dict(history.get("timings") or {})
    run_started = clock()
    queue_wait_seconds = 0.0
    peak_in_flight = 0
    completed_tasks = 0
    seen_ids: set[str] = set()
    failures: list[str] = []
    local = threading.local()
    metric_lock = threading.Lock()

    if not allow_incomplete_pagination:
        update_history_import(
            {
                "status": "running",
                "retryable": False,
                "error": "",
                **_history_progress_updates(_initial_progress(history)),
            },
            now=now,
        )

    def run_activity(activity: dict[str, Any], queued_at: float) -> dict[str, Any]:
        nonlocal queue_wait_seconds
        with user_context(user_id):
            with metric_lock:
                queue_wait_seconds += max(0.0, clock() - queued_at)
            if not hasattr(local, "client"):
                local.client = client_factory()
            attempts = 0
            while True:
                if request_gate.auth_stopped:
                    raise ConcurrentImportStopped("auth_failed")
                try:
                    return fetch_activity_endpoints(
                        local.client,
                        activity,
                        clock=clock,
                        now=now,
                        request_controller=request_gate,
                    )
                except DetailImportInterrupted as exc:
                    if exc.status == "auth_failed":
                        raise
                    if exc.status not in {"retry_pending", "rate_limited"}:
                        raise
                    if attempts >= network_retries:
                        raise
                    attempts += 1
                    request_gate.record_retry()
                    if exc.status == "retry_pending":
                        time.sleep(min(0.1 * (2 ** (attempts - 1)), 1.0))

    activities = activity_source or iter(_selected_metadata(history))
    futures: dict[Future[dict[str, Any]], str] = {}
    exhausted = False

    def submit_available(executor: ThreadPoolExecutor) -> None:
        nonlocal exhausted, peak_in_flight
        while not exhausted and len(futures) < capacity and not request_gate.auth_stopped:
            try:
                activity = next(activities)
            except StopIteration:
                exhausted = True
                break
            activity_id = _activity_id(activity)
            if activity_id in seen_ids:
                continue
            seen_ids.add(activity_id)
            existing = load_detail_state(activity_id)
            if existing and existing.get("complete"):
                continue
            future = executor.submit(run_activity, activity, clock())
            futures[future] = activity_id
            peak_in_flight = max(peak_in_flight, len(futures))

    with ThreadPoolExecutor(
        max_workers=worker_count, thread_name_prefix=f"garmin-details-{user_id}"
    ) as executor:
        submit_available(executor)
        while futures:
            done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
            for future in done:
                futures.pop(future)
                if future.cancelled():
                    continue
                try:
                    future.result()
                    completed_tasks += 1
                except ConcurrentImportStopped:
                    failures.append("auth_failed")
                except DetailImportInterrupted as exc:
                    failures.append(exc.status)
                except Exception as exc:
                    failures.append(type(exc).__name__)
            if request_gate.auth_stopped:
                for future in futures:
                    future.cancel()
            else:
                submit_available(executor)

    final_history = load_history_import(detect_stale=False)
    progress = _initial_progress(final_history)
    calls, avoided_na, avoided_resume, approximate_bytes = _performance_totals(
        final_history
    )
    elapsed = max(0.0, clock() - run_started)
    run_calls = max(0, calls - initial_calls)
    endpoint_rate = run_calls / elapsed if elapsed else 0.0
    activity_rate = completed_tasks / elapsed if elapsed else 0.0
    metrics = request_gate.metrics
    timings = dict(prior_timings)
    timings.update(
        details_seconds_total=round(
            float(prior_timings.get("details_seconds_total") or 0.0) + elapsed, 6
        ),
        detail_wall_clock_seconds=round(elapsed, 6),
        detail_network_seconds=round(metrics.network_seconds, 6),
        detail_network_calls=calls,
        detail_calls_avoided_not_applicable=avoided_na,
        detail_calls_avoided_resume=avoided_resume,
        detail_approximate_bytes=approximate_bytes,
        detail_worker_count=worker_count,
        detail_queue_capacity=capacity,
        detail_peak_in_flight=peak_in_flight,
        detail_peak_concurrency=metrics.peak_requests,
        detail_queue_wait_seconds=round(queue_wait_seconds, 6),
        detail_activities_per_second=round(activity_rate, 6),
        detail_endpoints_per_second=round(endpoint_rate, 6),
        detail_rate_limit_pauses=metrics.rate_limit_pauses,
        detail_rate_limit_pause_seconds=round(metrics.rate_limit_pause_seconds, 6),
        detail_retries=metrics.retries,
        detail_auth_stops=metrics.auth_stops,
    )
    if failures:
        error = "auth_failed" if request_gate.auth_stopped else failures[0]
        state = update_history_import(
            {
                "status": "error",
                "retryable": True,
                "error": f"details_{error}",
                "timings": timings,
                **_history_progress_updates(progress),
            },
            now=now,
        )
        raise DetailImportInterrupted(error)
    return update_history_import(
        {
            "status": "complete",
            "retryable": False,
            "error": "",
            "timings": timings,
            **_history_progress_updates(progress),
        },
        now=now,
    )
