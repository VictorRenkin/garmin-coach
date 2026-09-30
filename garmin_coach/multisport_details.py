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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
    GarthException,
)

from .config import MULTISPORT_DETAILS_DIR, ensure_directories
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


class DetailImportInterrupted(RuntimeError):
    def __init__(self, status: str) -> None:
        super().__init__(f"Import détaillé interrompu : {status}.")
        self.status = status


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
        except Exception as exc:
            endpoint["duration_seconds"] = round(max(0.0, clock() - call_started), 6)
            status, http_status, error_type = _failure_status(exc)
            endpoint["status"] = status
            endpoint["http_status"] = http_status
            endpoint["error_type"] = error_type
            endpoint["completed_at"] = attempted_at if status == "unsupported" else None

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
    client: Any,
    *,
    clock: Callable[[], float] = time.perf_counter,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Import selected activities one by one and resume incomplete endpoints."""
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
