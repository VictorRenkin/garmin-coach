from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ACTIVITY_DETAILS_DIR, ensure_directories


PRIVATE_METRIC_PARTS = ("latitude", "longitude", "position", "polyline")
PRIVATE_OBJECT_KEYS = {
    "startLatitude",
    "startLongitude",
    "endLatitude",
    "endLongitude",
    "latitude",
    "longitude",
    "geoPolylineDTO",
    "weatherStationDTO",
}


def detail_path(activity_id: str | int) -> Path:
    return ACTIVITY_DETAILS_DIR / f"{activity_id}.json"


def _without_location(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_location(item)
            for key, item in value.items()
            if key not in PRIVATE_OBJECT_KEYS
            and not any(part in key.lower() for part in PRIVATE_METRIC_PARTS)
        }
    if isinstance(value, list):
        return [_without_location(item) for item in value]
    return value


def _metric_samples(details: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    descriptors = details.get("metricDescriptors", [])
    allowed: list[tuple[int, str]] = []
    for descriptor in descriptors if isinstance(descriptors, list) else []:
        if not isinstance(descriptor, dict):
            continue
        index = descriptor.get("metricsIndex")
        key = str(descriptor.get("key") or "")
        if not isinstance(index, int) or not key:
            continue
        if any(part in key.lower() for part in PRIVATE_METRIC_PARTS):
            continue
        allowed.append((index, key))

    samples: list[dict[str, Any]] = []
    raw_samples = details.get("activityDetailMetrics", [])
    for raw in raw_samples if isinstance(raw_samples, list) else []:
        metrics = raw.get("metrics") if isinstance(raw, dict) else None
        if not isinstance(metrics, list):
            continue
        sample = {
            key: metrics[index]
            for index, key in allowed
            if index < len(metrics) and metrics[index] is not None
        }
        if sample:
            samples.append(sample)
    return [key for _, key in allowed], samples


def _safe_call(client: Any, method: str, *args: Any, **kwargs: Any) -> Any:
    function = getattr(client, method, None)
    if not callable(function):
        return None
    try:
        return function(*args, **kwargs)
    except Exception:
        return None


def fetch_activity_bundle(client: Any, activity: dict[str, Any]) -> dict[str, Any]:
    """Récupère toutes les mesures utiles sans conserver le tracé GPS."""
    activity_id = str(activity.get("activityId", ""))
    if not activity_id:
        raise ValueError("L'activité Garmin ne possède pas d'identifiant.")

    details = _safe_call(client, "get_activity_details", activity_id, 10000, 0) or {}
    metric_fields, samples = _metric_samples(details if isinstance(details, dict) else {})
    splits = _safe_call(client, "get_activity_splits", activity_id) or {}
    hr_zones = _safe_call(client, "get_activity_hr_in_timezones", activity_id) or []
    weather = _safe_call(client, "get_activity_weather", activity_id) or {}

    laps = splits.get("lapDTOs", []) if isinstance(splits, dict) else []
    return {
        "activity_id": activity_id,
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "privacy": "Coordonnées et tracé GPS exclus; mesures sportives conservées.",
        "metric_fields": metric_fields,
        "samples": samples,
        "sample_count": len(samples),
        "laps": _without_location(laps if isinstance(laps, list) else []),
        "events": _without_location(
            splits.get("eventDTOs", []) if isinstance(splits, dict) else []
        ),
        "heart_rate_zones": _without_location(hr_zones),
        "weather": _without_location(weather),
    }


def fetch_and_save_activity_bundle(client: Any, activity: dict[str, Any]) -> Path:
    ensure_directories()
    bundle = fetch_activity_bundle(client, activity)
    target = detail_path(bundle["activity_id"])
    target.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


def load_activity_bundle(activity_id: str | int) -> dict[str, Any] | None:
    target = detail_path(activity_id)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def latest_hr_zone_boundaries() -> list[dict[str, Any]]:
    """Retourne le profil de zones le plus récent présent dans les activités."""
    ensure_directories()
    candidates: list[tuple[str, list[dict[str, Any]]]] = []
    for target in ACTIVITY_DETAILS_DIR.glob("*.json"):
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        zones = payload.get("heart_rate_zones") if isinstance(payload, dict) else None
        if isinstance(zones, list) and zones:
            candidates.append((str(payload.get("fetched_at") or ""), zones))
    return max(candidates, default=("", []), key=lambda item: item[0])[1]


def public_detail(activity_id: str | int, max_points: int = 500) -> dict[str, Any] | None:
    payload = load_activity_bundle(activity_id)
    if payload is None:
        return None
    samples = payload.get("samples", [])
    if not isinstance(samples, list):
        samples = []
    step = max(1, len(samples) // max_points)
    return {
        "activity_id": str(activity_id),
        "metric_fields": payload.get("metric_fields", []),
        "samples": samples[::step][:max_points],
        "laps": payload.get("laps", []),
        "heart_rate_zones": payload.get("heart_rate_zones", []),
        "weather": payload.get("weather", {}),
        "privacy": payload.get("privacy"),
    }
