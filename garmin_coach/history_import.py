"""Persistent, per-athlete state for the future Garmin history importer."""
from __future__ import annotations

import calendar
import json
import os
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import HISTORY_IMPORT_FILE, ensure_directories


SCHEMA_VERSION = 1
DEFAULT_SCOPE = "2_years"
DEFAULT_PAGE_SIZE = 250
VALID_SCOPES = {"3_months", "6_months", "1_year", "2_years", "all"}
VALID_STATUSES = {"idle", "running", "interrupted", "complete", "error"}
STALE_AFTER_SECONDS = 300
_WRITE_LOCK = threading.Lock()
_FIELDS = {
    "schema_version",
    "status",
    "scope",
    "reference_date",
    "latest_sync_date",
    "latest_sync_anchor_ids",
    "cutoff_date",
    "page_size",
    "next_offset",
    "pagination_complete",
    "pages_examined",
    "activities_examined",
    "metadata_saved",
    "metadata_existing",
    "activities_detail_complete",
    "activities_detail_pending",
    "activities_retrying",
    "endpoints_success",
    "endpoints_empty",
    "endpoints_not_applicable",
    "endpoints_unsupported",
    "endpoints_retry_pending",
    "endpoints_rate_limited",
    "endpoints_auth_failed",
    "endpoints_failed",
    "started_at",
    "updated_at",
    "heartbeat_at",
    "timings",
    "retryable",
    "error",
    "last_page_fingerprint",
    "resume_anchor_ids",
}
_COUNTERS = {
    "next_offset",
    "pages_examined",
    "activities_examined",
    "metadata_saved",
    "metadata_existing",
    "activities_detail_complete",
    "activities_detail_pending",
    "activities_retrying",
    "endpoints_success",
    "endpoints_empty",
    "endpoints_not_applicable",
    "endpoints_unsupported",
    "endpoints_retry_pending",
    "endpoints_rate_limited",
    "endpoints_auth_failed",
    "endpoints_failed",
}


def validate_scope(scope: object) -> str:
    value = str(scope or DEFAULT_SCOPE).strip()
    if value not in VALID_SCOPES:
        raise ValueError("Période d’historique Garmin invalide.")
    return value


def configured_page_size() -> int:
    raw = os.getenv("MOTIONEST_HISTORY_PAGE_SIZE", str(DEFAULT_PAGE_SIZE))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("MOTIONEST_HISTORY_PAGE_SIZE doit être un entier.") from exc
    if not 1 <= value <= 1000:
        raise ValueError("MOTIONEST_HISTORY_PAGE_SIZE doit être compris entre 1 et 1000.")
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def cutoff_for_scope(scope: str, reference_date: date) -> date | None:
    selected = validate_scope(scope)
    months = {"3_months": 3, "6_months": 6, "1_year": 12, "2_years": 24}
    return None if selected == "all" else _subtract_months(reference_date, months[selected])


def _empty_state(*, status: str = "idle", error: str = "") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "scope": DEFAULT_SCOPE,
        "reference_date": None,
        "latest_sync_date": None,
        "latest_sync_anchor_ids": [],
        "cutoff_date": None,
        "page_size": configured_page_size(),
        "next_offset": 0,
        "pagination_complete": False,
        "pages_examined": 0,
        "activities_examined": 0,
        "metadata_saved": 0,
        "metadata_existing": 0,
        "activities_detail_complete": 0,
        "activities_detail_pending": 0,
        "activities_retrying": 0,
        "endpoints_success": 0,
        "endpoints_empty": 0,
        "endpoints_not_applicable": 0,
        "endpoints_unsupported": 0,
        "endpoints_retry_pending": 0,
        "endpoints_rate_limited": 0,
        "endpoints_auth_failed": 0,
        "endpoints_failed": 0,
        "started_at": None,
        "updated_at": None,
        "heartbeat_at": None,
        "timings": {},
        "retryable": status in {"interrupted", "error"},
        "error": error,
        "last_page_fingerprint": "",
        "resume_anchor_ids": [],
    }


def _validate_state(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("État d’import invalide.")
    state = {key: payload.get(key) for key in _FIELDS}
    # Schema v1 existed before pagination. New checkpoint fields are optional
    # when reading those foundation files and are materialized on next write.
    state["metadata_existing"] = payload.get("metadata_existing", 0)
    for field in (
        "endpoints_success",
        "endpoints_empty",
        "endpoints_not_applicable",
        "endpoints_unsupported",
        "endpoints_retry_pending",
        "endpoints_rate_limited",
        "endpoints_auth_failed",
        "endpoints_failed",
    ):
        state[field] = payload.get(field, 0)
    state["last_page_fingerprint"] = str(
        payload.get("last_page_fingerprint") or ""
    )
    state["resume_anchor_ids"] = payload.get("resume_anchor_ids") or []
    state["latest_sync_anchor_ids"] = payload.get("latest_sync_anchor_ids") or []
    if state["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Version d’état d’import inconnue.")
    if state["status"] not in VALID_STATUSES:
        raise ValueError("Statut d’import invalide.")
    state["scope"] = validate_scope(state["scope"])
    if not isinstance(state["page_size"], int) or not 1 <= state["page_size"] <= 1000:
        raise ValueError("Taille de page d’import invalide.")
    for field in _COUNTERS:
        if not isinstance(state[field], int) or state[field] < 0:
            raise ValueError(f"Compteur d’import invalide : {field}.")
    if not isinstance(state["pagination_complete"], bool):
        raise ValueError("État de pagination invalide.")
    if not isinstance(state["timings"], dict):
        raise ValueError("Mesures temporelles invalides.")
    if not isinstance(state["retryable"], bool):
        raise ValueError("État de reprise invalide.")
    if not isinstance(state["resume_anchor_ids"], list) or any(
        not isinstance(value, str) for value in state["resume_anchor_ids"]
    ):
        raise ValueError("Ancres de reprise invalides.")
    if not isinstance(state['latest_sync_anchor_ids'], list) or len(state['latest_sync_anchor_ids']) > 1000 or any(not isinstance(value, str) for value in state['latest_sync_anchor_ids']):
        raise ValueError('Ancres de synchronisation invalides.')
    for field in ("reference_date", "cutoff_date", "latest_sync_date"):
        if state[field] is not None:
            date.fromisoformat(str(state[field]))
    for field in ("started_at", "updated_at", "heartbeat_at"):
        if state[field] is not None:
            datetime.fromisoformat(str(state[field]).replace("Z", "+00:00"))
    state["error"] = str(state["error"] or "")
    return state


def _atomic_write(state: dict[str, Any]) -> dict[str, Any]:
    validated = _validate_state(state)
    ensure_directories()
    target = Path(os.fspath(HISTORY_IMPORT_FILE))
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(validated, ensure_ascii=False, indent=2)
    with _WRITE_LOCK:
        try:
            temporary.write_text(encoded, encoding="utf-8")
            _validate_state(json.loads(temporary.read_text(encoding="utf-8")))
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return validated


def initialize_history_import(
    scope: object = DEFAULT_SCOPE,
    *,
    reference_date: date | None = None,
    now: datetime | None = None,
    page_size: int | None = None,
) -> dict[str, Any]:
    """Freeze a scope and reference date without starting future pagination."""
    selected = validate_scope(scope)
    existing = load_history_import(detect_stale=False)
    if existing.get("reference_date") and existing.get("scope") == selected:
        return existing
    moment = now or _now()
    reference = reference_date or moment.date()
    cutoff = cutoff_for_scope(selected, reference)
    state = _empty_state()
    state.update(
        scope=selected,
        reference_date=reference.isoformat(),
        cutoff_date=cutoff.isoformat() if cutoff else None,
        page_size=page_size if page_size is not None else configured_page_size(),
        started_at=_iso(moment),
        updated_at=_iso(moment),
        heartbeat_at=None,
    )
    return _atomic_write(state)


def resume_history_import(*, now: datetime | None = None) -> dict[str, Any]:
    """Mark an initialized import as running while retaining its frozen dates."""
    state = load_history_import(detect_stale=False)
    if not state.get("reference_date"):
        state = initialize_history_import(now=now)
    moment = now or _now()
    state.update(
        status="running",
        updated_at=_iso(moment),
        heartbeat_at=_iso(moment),
        retryable=False,
        error="",
    )
    return _atomic_write(state)


def update_history_import(
    updates: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Atomically checkpoint validated pagination fields."""
    unknown = set(updates) - _FIELDS
    if unknown:
        raise ValueError(f"Champ d’état d’import inconnu : {sorted(unknown)[0]}.")
    state = load_history_import(detect_stale=False)
    state.update(updates)
    moment = now or _now()
    state["updated_at"] = _iso(moment)
    if state.get("status") != "idle" and state.get("reference_date"):
        state["heartbeat_at"] = _iso(moment)
    return _atomic_write(state)


def load_history_import(
    *,
    detect_stale: bool = True,
    now: datetime | None = None,
    stale_after_seconds: int = STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    target = Path(os.fspath(HISTORY_IMPORT_FILE))
    if not target.is_file():
        return _empty_state()
    try:
        state = _validate_state(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return _empty_state(status="error", error="invalid_history_import_state")
    if detect_stale and state["status"] == "running":
        heartbeat = state.get("heartbeat_at")
        try:
            last_seen = datetime.fromisoformat(str(heartbeat).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            last_seen = datetime.min.replace(tzinfo=timezone.utc)
        moment = now or _now()
        if moment - last_seen > timedelta(seconds=stale_after_seconds):
            state.update(
                status="interrupted",
                retryable=True,
                error="stale_heartbeat",
                updated_at=_iso(moment),
            )
            return _atomic_write(state)
    return state
