from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import ACTIVITIES_DIR, STATE_FILE, ensure_directories


def load_seen_ids() -> set[str]:
    if not STATE_FILE.exists():
        return set()
    try:
        content = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return {str(value) for value in content.get("seen_activity_ids", [])}


def save_seen_ids(activity_ids: set[str]) -> None:
    ensure_directories()
    payload = {"seen_activity_ids": sorted(activity_ids)}
    STATE_FILE.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def save_activity(activity: dict[str, Any]) -> Path:
    ensure_directories()
    activity_id = str(activity.get("activityId", "inconnue"))
    target = ACTIVITIES_DIR / f"{activity_id}.json"
    target.write_text(
        json.dumps(activity, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return target


def load_activities() -> list[dict[str, Any]]:
    ensure_directories()
    activities: list[dict[str, Any]] = []
    for path in ACTIVITIES_DIR.glob("*.json"):
        try:
            activity = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(activity, dict):
            activities.append(activity)
    return sorted(
        activities, key=lambda item: str(item.get("startTimeLocal") or "")
    )
