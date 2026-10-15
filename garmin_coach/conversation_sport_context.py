"""Privacy-safe conversation projection using canonical local calculations."""
import json
import math
from datetime import date, timedelta
from typing import Any

from .config import user_context
from .local_activity_history import load_local_activity_history
from .runtime_context import build_runtime_motionest_context
from .training_history_summary import _distance_relevant, _duration, _window, sport_classification

# UTF-8 JSON budget for the entire multisport projection, not an activity count.
MAX_CONTEXT_BYTES = 48_000


def _size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _measurement(activity: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = activity.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            return value
    return None


def _project_activity(activity: dict[str, Any]) -> dict[str, Any]:
    sport, classification = sport_classification(activity)
    metrics = {
        "duration_seconds": _duration(activity),
        "distance_m": _measurement(activity, "distance") if _distance_relevant(sport) else None,
        "average_heart_rate": _measurement(activity, "averageHR", "averageHeartRate"),
        "maximum_heart_rate": _measurement(activity, "maxHR", "maxHeartRate"),
        "average_power_watts": _measurement(activity, "averagePower"),
        "training_load": _measurement(activity, "activityTrainingLoad"),
        "aerobic_training_effect": _measurement(activity, "aerobicTrainingEffect"),
        "anaerobic_training_effect": _measurement(activity, "anaerobicTrainingEffect"),
    }
    return {
        "date": str(activity.get("startTimeLocal") or activity.get("startTimeGMT"))[:10],
        "sport": sport, **metrics,
        "provenance": {"source": "local_activity_metadata", "classification": classification},
        "missing_metrics": [key for key, value in metrics.items() if value is None and not (key == "distance_m" and not _distance_relevant(sport))],
        "distance_relevant": _distance_relevant(sport),
    }


def build_conversation_sport_context(user_id: str) -> dict[str, Any]:
    runtime = build_runtime_motionest_context(user_id)
    reference = date.fromisoformat(runtime.reference_date)
    with user_context(user_id):
        activities = load_local_activity_history(start_date=reference - timedelta(days=13), end_date=reference)
    weeks = {}
    for key, start, end in (
        ("last_7_days", reference - timedelta(days=6), reference),
        ("previous_7_days", reference - timedelta(days=13), reference - timedelta(days=7)),
    ):
        selected = [item for item in activities if start.isoformat() <= str(item.get("startTimeLocal") or item.get("startTimeGMT"))[:10] <= end.isoformat()]
        weeks[key] = {
            "summary": _window(selected, start, end).model_dump(mode="json"),
            "activities": [_project_activity(item) for item in selected],
            "activities_omitted": 0,
            "activity_list_complete": True,
        }
    payload = {
        "status": "available", "reference_date": runtime.reference_date,
        "context": runtime.context.model_dump(mode="json"),
        "history": runtime.conversation_history,
        "weeks": weeks,
        "input_diagnostics": [item.model_dump(mode="json") for item in runtime.input_diagnostics],
        "budget": {"max_bytes": MAX_CONTEXT_BYTES, "activities_truncated": False,
                   "summary_preserves_all_activities": True},
    }
    # Alternate weeks so neither disappears first. Canonical totals remain intact.
    while _size(payload) > MAX_CONTEXT_BYTES:
        candidates = [week for week in weeks.values() if week["activities"]]
        if not candidates:
            # Never silently remove canonical facts to satisfy a size constraint.
            raise ValueError("Canonical conversation summaries exceed the context budget")
        week = max(candidates, key=lambda item: len(item["activities"]))
        week["activities"].pop(0)
        week["activities_omitted"] += 1
        week["activity_list_complete"] = False
        payload["budget"]["activities_truncated"] = True
    return payload
