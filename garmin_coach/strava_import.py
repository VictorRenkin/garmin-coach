from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .config import REPORTS_DIR, ensure_directories
from .storage import load_seen_ids, save_activity, save_seen_ids
from .summary import build_summary


RUN_TYPES = {"run", "trail run", "virtual run", "course", "course à pied"}


def _value(row: dict[str, str], *names: str) -> str:
    values = {str(key).strip().lower(): (value or "").strip() for key, value in row.items()}
    return next((values[name.lower()] for name in names if values.get(name.lower())), "")


def _number(value: str) -> float | None:
    cleaned = value.strip().replace("\u202f", "").replace(" ", "")
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _date(value: str) -> str:
    for pattern in (
        "%b %d, %Y, %I:%M:%S %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%d %b %Y, %H:%M:%S",
    ):
        try:
            return datetime.strptime(value.strip(), pattern).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return value.strip()


def _activity(row: dict[str, str]) -> dict[str, Any] | None:
    sport = _value(row, "Activity Type", "Type d'activité", "Type d’activité")
    if sport.lower() not in RUN_TYPES:
        return None
    source_id = _value(row, "Activity ID", "Identifiant de l'activité", "ID de l'activité")
    if not source_id:
        return None
    distance_km = _number(_value(row, "Distance", "Distance.1"))
    elapsed = _number(_value(row, "Elapsed Time", "Temps écoulé"))
    moving = _number(_value(row, "Moving Time", "Temps de déplacement"))
    result: dict[str, Any] = {
        "activityId": f"strava-{source_id}",
        "source": "strava-export",
        "sourceActivityId": source_id,
        "activityName": _value(row, "Activity Name", "Nom de l'activité") or "Course Strava",
        "activityType": {"typeKey": "running"},
        "startTimeLocal": _date(_value(row, "Activity Date", "Date de l'activité")),
        "distance": distance_km * 1000 if distance_km is not None else None,
        "duration": moving or elapsed,
        "movingDuration": moving or elapsed,
        "elapsedDuration": elapsed,
        "averageHR": _number(_value(row, "Average Heart Rate", "Fréquence cardiaque moyenne")),
        "maxHR": _number(_value(row, "Max Heart Rate", "Fréquence cardiaque max")),
        "elevationGain": _number(_value(row, "Elevation Gain", "Dénivelé positif")),
        "calories": _number(_value(row, "Calories")),
        "averageRunningCadenceInStepsPerMinute": _number(_value(row, "Average Cadence", "Cadence moyenne")),
        "description": _value(row, "Activity Description", "Description de l'activité"),
    }
    return {key: value for key, value in result.items() if value is not None}


def _rows(stream: io.TextIOBase) -> Iterable[dict[str, str]]:
    yield from csv.DictReader(stream)


def _read_rows(source: Path) -> list[dict[str, str]]:
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as archive:
            candidates = [name for name in archive.namelist() if name.lower().endswith("activities.csv")]
            if not candidates:
                raise ValueError("L'archive Strava ne contient pas activities.csv.")
            raw = archive.read(min(candidates, key=len))
        return list(_rows(io.StringIO(raw.decode("utf-8-sig"))))
    if source.suffix.lower() == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            return list(_rows(stream))
    raise ValueError("Choisis l'export Strava ZIP ou son fichier activities.csv.")


def import_strava(source: str | Path) -> list[Path]:
    """Importe les courses d'un export personnel Strava sans utiliser son API."""
    ensure_directories()
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Fichier Strava introuvable : {path}")
    seen = load_seen_ids()
    reports: list[Path] = []
    for row in _read_rows(path):
        activity = _activity(row)
        if activity is None or str(activity["activityId"]) in seen:
            continue
        activity_id = str(activity["activityId"])
        save_activity(activity)
        date = str(activity.get("startTimeLocal") or "date-inconnue")[:10]
        report = REPORTS_DIR / f"{date}-{activity_id}.md"
        report.write_text(build_summary(activity), encoding="utf-8")
        reports.append(report)
        seen.add(activity_id)
    save_seen_ids(seen)
    return reports
