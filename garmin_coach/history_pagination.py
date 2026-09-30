"""Sequential, resumable pagination of Garmin activity-list metadata."""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
    GarthException,
)

from .history_import import (
    initialize_history_import,
    load_history_import,
    resume_history_import,
    update_history_import,
)
from .history_storage import save_activity_metadata


class InvalidGarminHistoryResponse(ValueError):
    pass


class LocalHistoryPersistenceError(RuntimeError):
    pass


def _activity_date(activity: dict[str, Any]) -> date | None:
    raw = activity.get("startTimeLocal") or activity.get("startTimeGMT")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _page_fingerprint(activity_ids: list[str]) -> str:
    encoded = json.dumps(activity_ids, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _error_kind(exc: Exception) -> str:
    if isinstance(exc, GarminConnectAuthenticationError):
        return "garmin_auth"
    if isinstance(exc, GarminConnectTooManyRequestsError):
        return "garmin_rate_limit"
    if isinstance(
        exc,
        (GarminConnectConnectionError, GarthException, TimeoutError, ConnectionError),
    ):
        return "garmin_network"
    if isinstance(exc, InvalidGarminHistoryResponse):
        return "invalid_garmin_response"
    if isinstance(exc, (LocalHistoryPersistenceError, OSError, json.JSONDecodeError)):
        return "local_persistence"
    return f"{type(exc).__name__}"


def _validated_page(payload: object) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(payload, list):
        raise InvalidGarminHistoryResponse("Garmin n’a pas renvoyé une liste.")
    activities: list[dict[str, Any]] = []
    activity_ids: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            raise InvalidGarminHistoryResponse("Activité Garmin non structurée.")
        activity_id = str(item.get("activityId") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", activity_id):
            raise InvalidGarminHistoryResponse("Activité Garmin sans activityId.")
        activities.append(item)
        activity_ids.append(activity_id)
    return activities, activity_ids


def paginate_activity_history(
    client: Any,
    *,
    clock: Any = time.perf_counter,
    now: datetime | None = None,
    on_activity_persisted: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Fetch all selected metadata sequentially and checkpoint after each page."""
    state = load_history_import(detect_stale=False)
    if not state.get("reference_date"):
        state = initialize_history_import(now=now)
    if state.get("pagination_complete"):
        return state

    original_next = int(state.get("next_offset") or 0)
    page_size = int(state["page_size"])
    reference = date.fromisoformat(str(state["reference_date"]))
    cutoff = (
        date.fromisoformat(str(state["cutoff_date"]))
        if state.get("cutoff_date")
        else None
    )
    state = resume_history_import(now=now)
    timings = dict(state.get("timings") or {})
    prior_total = float(timings.get("pagination_seconds_total") or 0.0)
    run_started = clock()
    refresh_head = original_next > 0
    cursor = 0

    try:
        while True:
            fetch_started = clock()
            raw_page = client.get_activities(cursor, page_size)
            fetch_seconds = max(0.0, clock() - fetch_started)
            activities, activity_ids = _validated_page(raw_page)
            fingerprint = _page_fingerprint(activity_ids)
            is_overlap = original_next > 0 and cursor < original_next
            if (
                activities
                and not is_overlap
                and fingerprint == state.get("last_page_fingerprint")
            ):
                raise InvalidGarminHistoryResponse(
                    "Garmin a renvoyé deux pages identiques sans progression."
                )

            page_dates = [_activity_date(activity) for activity in activities]
            in_scope: list[dict[str, Any]] = []
            for activity, activity_day in zip(activities, page_dates):
                if cutoff is None:
                    in_scope.append(activity)
                elif activity_day is not None and cutoff <= activity_day <= reference:
                    in_scope.append(activity)

            # De-duplicate inside a page before touching the filesystem.
            unique_in_scope: dict[str, dict[str, Any]] = {}
            for activity in in_scope:
                unique_in_scope.setdefault(str(activity["activityId"]), activity)
            duplicate_ids_in_page = len(in_scope) - len(unique_in_scope)

            persist_started = clock()
            saved = 0
            existing = duplicate_ids_in_page
            for activity in unique_in_scope.values():
                try:
                    _, created = save_activity_metadata(activity, imported_at=now)
                except Exception as exc:
                    raise LocalHistoryPersistenceError(
                        "Échec de persistance des métadonnées."
                    ) from exc
                if created:
                    saved += 1
                else:
                    existing += 1
                if on_activity_persisted is not None:
                    on_activity_persisted(activity)
            persist_seconds = max(0.0, clock() - persist_started)

            page_is_empty = not activities
            page_is_partial = len(activities) < page_size
            dated = [value for value in page_dates if value is not None]
            page_entirely_before_cutoff = bool(
                cutoff is not None and dated and len(dated) == len(activities)
                and max(dated) < cutoff
            )
            complete = page_is_empty or page_is_partial or page_entirely_before_cutoff

            was_head_refresh = refresh_head
            if refresh_head:
                checkpoint_offset = original_next
                refresh_head = False
                cursor = (
                    original_next
                    if original_next <= page_size
                    else max(0, original_next - page_size)
                )
            else:
                next_cursor = cursor + len(activities)
                if activities and next_cursor <= cursor:
                    raise InvalidGarminHistoryResponse("Offset Garmin sans progression.")
                checkpoint_offset = next_cursor if activities else cursor
                cursor = next_cursor
            if was_head_refresh:
                complete = False

            timings.update(
                fetch_seconds_total=round(
                    float(timings.get("fetch_seconds_total") or 0.0)
                    + fetch_seconds,
                    6,
                ),
                persist_seconds_total=round(
                    float(timings.get("persist_seconds_total") or 0.0)
                    + persist_seconds,
                    6,
                ),
                last_page_fetch_seconds=round(fetch_seconds, 6),
                last_page_persist_seconds=round(persist_seconds, 6),
                pagination_seconds_total=round(
                    prior_total + max(0.0, clock() - run_started), 6
                ),
            )
            page_timings = list(timings.get("pages") or [])
            page_timings.append(
                {
                    "offset": 0 if was_head_refresh else checkpoint_offset - len(activities),
                    "activities": len(activities),
                    "fetch_seconds": round(fetch_seconds, 6),
                    "sanitize_persist_seconds": round(persist_seconds, 6),
                }
            )
            timings["pages"] = page_timings
            state = update_history_import(
                {
                    "status": "complete" if complete else "running",
                    "retryable": False,
                    "error": "",
                    "pages_examined": int(state["pages_examined"]) + 1,
                    "activities_examined": int(state["activities_examined"])
                    + len(activities),
                    "metadata_saved": int(state["metadata_saved"]) + saved,
                    "metadata_existing": int(state["metadata_existing"]) + existing,
                    "next_offset": checkpoint_offset,
                    "pagination_complete": complete,
                    "last_page_fingerprint": fingerprint,
                    "resume_anchor_ids": activity_ids[-10:],
                    "timings": timings,
                },
                now=now,
            )
            if complete:
                return state
    except Exception as exc:
        timings["pagination_seconds_total"] = round(
            prior_total + max(0.0, clock() - run_started), 6
        )
        update_history_import(
            {
                "status": "error",
                "retryable": True,
                "error": _error_kind(exc),
                "pagination_complete": False,
                "timings": timings,
            },
            now=now,
        )
        raise
