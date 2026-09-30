"""Privacy-preserving storage for Garmin activity-list metadata."""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ACTIVITY_HISTORY_DIR, ensure_directories


SCHEMA_VERSION = 1
_WRITE_LOCK = threading.Lock()
_SKIP = object()
_SENSITIVE_FRAGMENTS = (
    "latitude",
    "longitude",
    "coordinate",
    "position",
    "polyline",
    "gps",
    "geolocation",
    "location",
    "streetaddress",
    "postalcode",
    "owner",
    "fullname",
    "displayname",
    "username",
    "firstname",
    "lastname",
    "userinfo",
    "userprofile",
    "userid",
    "personid",
    "social",
    "email",
    "oauth",
    "token",
    "cookie",
    "password",
    "secret",
    "authentication",
    "authorization",
    "mf_code",
    "mfacode",
    "serialnumber",
    "deviceid",
    "unitid",
    "profileid",
    "accountid",
)
_SENSITIVE_EXACT = {
    "address",
    "city",
    "country",
    "identity",
    "profile",
    "user",
    "session",
    "sessionid",
}


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(key).lower())


def is_sensitive_metadata_key(key: object) -> bool:
    normalized = _normalized_key(key)
    return normalized in _SENSITIVE_EXACT or any(
        fragment in normalized for fragment in _SENSITIVE_FRAGMENTS
    )


def sanitize_activity_metadata(value: object) -> Any:
    """Recursively remove location, identity and authentication material."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if is_sensitive_metadata_key(key):
                continue
            sanitized = sanitize_activity_metadata(raw_value)
            if sanitized is not _SKIP:
                cleaned[key] = sanitized
        return cleaned
    if isinstance(value, (list, tuple)):
        cleaned_items = []
        for item in value:
            sanitized = sanitize_activity_metadata(item)
            if sanitized is not _SKIP:
                cleaned_items.append(sanitized)
        return cleaned_items
    return _SKIP


def _activity_id(activity: dict[str, Any]) -> str:
    activity_id = str(activity.get("activityId") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", activity_id):
        raise ValueError("activityId Garmin absent ou invalide.")
    return activity_id


def _validate_metadata(payload: object, expected_id: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Métadonnées d’activité invalides.")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Version de métadonnées d’activité invalide.")
    activity_id = _activity_id(payload)
    if expected_id is not None and activity_id != expected_id:
        raise ValueError("activityId incohérent après persistance.")
    if not isinstance(payload.get("imported_at"), str):
        raise ValueError("Date d’import absente.")

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
    return payload


def save_activity_metadata(
    activity: dict[str, Any], *, imported_at: datetime | None = None
) -> tuple[Path, bool]:
    """Atomically save one sanitized activity; return (path, newly_created)."""
    activity_id = _activity_id(activity)
    ensure_directories()
    target = Path(os.fspath(ACTIVITY_HISTORY_DIR)) / f"{activity_id}.json"
    if target.is_file():
        try:
            _validate_metadata(
                json.loads(target.read_text(encoding="utf-8")), activity_id
            )
            return target, False
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    sanitized = sanitize_activity_metadata(activity)
    if not isinstance(sanitized, dict):
        raise ValueError("Métadonnées Garmin invalides.")
    moment = imported_at or datetime.now(timezone.utc)
    payload = {
        **sanitized,
        "activityId": activity_id,
        "schema_version": SCHEMA_VERSION,
        "imported_at": moment.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
    }
    _validate_metadata(payload, activity_id)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    with _WRITE_LOCK:
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _validate_metadata(
                json.loads(temporary.read_text(encoding="utf-8")), activity_id
            )
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target, True


def iter_activity_history() -> Iterator[dict[str, Any]]:
    """Stream valid local metadata without loading the full history in memory."""
    root = Path(os.fspath(ACTIVITY_HISTORY_DIR))
    if not root.is_dir():
        return
    for path in sorted(root.glob("*.json"), key=lambda item: item.name):
        try:
            payload = _validate_metadata(
                json.loads(path.read_text(encoding="utf-8")), path.stem
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        yield payload
