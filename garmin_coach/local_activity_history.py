"""Read the active athlete's imported activity history from local files only."""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import ACTIVITY_HISTORY_DIR
from .history_import import load_history_import
from .history_storage import iter_activity_history, sanitize_activity_metadata
from .multisport_details import load_detail_state
from .storage import load_activities as load_legacy_activities


class LocalActivityHistoryError(RuntimeError):
    """The selected local history cannot be read without hiding an import error."""


def activity_sport_type(activity: dict[str, Any]) -> str:
    """Return Garmin's raw sport/type key without inventing a category."""
    activity_type = activity.get("activityType")
    if isinstance(activity_type, dict):
        value = activity_type.get("typeKey")
    else:
        value = activity_type or activity.get("activityTypeDTO")
    return str(value or "unknown").strip().lower() or "unknown"


def _activity_datetime(activity: dict[str, Any]) -> datetime | None:
    raw = activity.get("startTimeLocal") or activity.get("startTimeGMT")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _date_filter(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _multisport_files_exist() -> bool:
    root = Path(os.fspath(ACTIVITY_HISTORY_DIR))
    return root.is_dir() and next(root.glob("*.json"), None) is not None


def local_history_source() -> str:
    """Choose multisport unless only the pre-import legacy store is available."""
    state = load_history_import(detect_stale=False)
    if state.get("status") == "error":
        raise LocalActivityHistoryError(
            "L'état de l'import multisport est invalide; le fallback legacy est refusé."
        )
    if state.get("reference_date") or _multisport_files_exist():
        return "multisport"
    return "legacy"


def local_history_scope() -> dict[str, str | None]:
    """Expose the reader's selected source and frozen bounds."""
    source = local_history_source()
    state = load_history_import(detect_stale=False)
    return {
        "source": source,
        "scope": str(state.get("scope")) if source == "multisport" else None,
        "reference_date": (
            str(state.get("reference_date"))
            if source == "multisport" and state.get("reference_date")
            else None
        ),
        "cutoff_date": (
            str(state.get("cutoff_date"))
            if source == "multisport" and state.get("cutoff_date")
            else None
        ),
    }


def _scope_bounds(source: str) -> tuple[date | None, date | None]:
    if source != "multisport":
        return None, None
    state = load_history_import(detect_stale=False)
    reference = _date_filter(state.get("reference_date"))
    cutoff = _date_filter(state.get("cutoff_date"))
    return cutoff, reference


def iter_local_activity_history(
    *,
    start_date: date | datetime | str | None = None,
    end_date: date | datetime | str | None = None,
    sport_types: Iterable[str] | None = None,
    diagnostics: dict[str, int] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield sanitized metadata chronologically within the frozen import scope.

    Only the small metadata files are materialized for sorting. Multisport detail
    files are deliberately not opened by this iterator.
    """
    source = local_history_source()
    scope_start, scope_end = _scope_bounds(source)
    requested_start = _date_filter(start_date)
    requested_end = _date_filter(end_date)
    lower = max(filter(None, (scope_start, requested_start)), default=None)
    upper = min(filter(None, (scope_end, requested_end)), default=None)
    if isinstance(sport_types, str):
        selected_sports = {sport_types.strip().lower()}
    elif sport_types is not None:
        selected_sports = {str(value).strip().lower() for value in sport_types}
    else:
        selected_sports = None
    raw_activities = (
        iter_activity_history()
        if source == "multisport"
        else iter(load_legacy_activities())
    )
    activities: list[tuple[datetime, str, dict[str, Any]]] = []
    for raw in raw_activities:
        if diagnostics is not None:
            diagnostics["metadata_files_read"] = (
                diagnostics.get("metadata_files_read", 0) + 1
            )
        sanitized = sanitize_activity_metadata(raw)
        if not isinstance(sanitized, dict):
            continue
        moment = _activity_datetime(sanitized)
        # An initialized import has frozen bounds. Undated metadata cannot be
        # proven to belong to them and is therefore excluded safely.
        if moment is None and (lower is not None or upper is not None):
            continue
        activity_day = moment.date() if moment is not None else None
        if lower is not None and (activity_day is None or activity_day < lower):
            continue
        if upper is not None and (activity_day is None or activity_day > upper):
            continue
        if (
            selected_sports is not None
            and activity_sport_type(sanitized) not in selected_sports
        ):
            continue
        activity_id = str(sanitized.get("activityId") or "")
        activities.append((moment or datetime.min, activity_id, sanitized))
    activities.sort(key=lambda item: (item[0], item[1]))
    for _, _, activity in activities:
        yield activity


def load_local_activity_history(**filters: Any) -> list[dict[str, Any]]:
    """Materialize filtered metadata; details remain lazy and separate."""
    return list(iter_local_activity_history(**filters))


def load_recent_local_activities(limit: int = 20) -> list[dict[str, Any]]:
    """Return at most the newest ``limit`` in-scope activities, all sports."""
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("La limite d'activités récentes doit être positive.")
    activities = load_local_activity_history()
    return activities[-limit:]


def load_local_activity_details(activity_id: str | int) -> dict[str, Any] | None:
    """Load a detail state on demand; callers must already hold in-scope metadata."""
    return load_detail_state(activity_id)


def load_local_activity_with_details(
    activity_id: str | int,
) -> dict[str, Any] | None:
    """Load one in-scope activity and its detail state only when requested."""
    expected = str(activity_id)
    metadata = next(
        (
            activity
            for activity in iter_local_activity_history()
            if str(activity.get("activityId") or "") == expected
        ),
        None,
    )
    if metadata is None:
        return None
    return {"metadata": metadata, "details": load_local_activity_details(expected)}
