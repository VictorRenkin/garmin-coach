from __future__ import annotations

from datetime import datetime
from typing import Any


def _number(activity: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = activity.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "inconnue"
    total = round(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:d} h {minutes:02d} min {secs:02d} s" if hours else f"{minutes:d} min {secs:02d} s"


def _pace(distance_m: float | None, duration_s: float | None) -> str:
    if not distance_m or not duration_s or distance_m <= 0:
        return "inconnue"
    seconds_per_km = duration_s / (distance_m / 1000)
    minutes, seconds = divmod(round(seconds_per_km), 60)
    return f"{minutes:d}:{seconds:02d}/km"


def _activity_date(activity: dict[str, Any]) -> str:
    raw = activity.get("startTimeLocal") or activity.get("startTimeGMT")
    if not isinstance(raw, str):
        return "date inconnue"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%d/%m/%Y à %H:%M")
    except ValueError:
        return raw


def is_running(activity: dict[str, Any]) -> bool:
    kind = activity.get("activityType")
    if isinstance(kind, dict):
        key = str(kind.get("typeKey", ""))
    else:
        key = str(kind or activity.get("activityTypeDTO", ""))
    return "run" in key.lower() or "course" in key.lower()


def build_summary(activity: dict[str, Any]) -> str:
    name = str(activity.get("activityName") or "Course à pied")
    distance_m = _number(activity, "distance")
    duration_s = _number(activity, "movingDuration", "duration", "elapsedDuration")
    average_hr = _number(activity, "averageHR", "averageHeartRate")
    max_hr = _number(activity, "maxHR", "maxHeartRate")
    elevation = _number(activity, "elevationGain", "totalAscent")
    calories = _number(activity, "calories")
    training_effect = _number(activity, "aerobicTrainingEffect")

    distance_km = f"{distance_m / 1000:.2f} km" if distance_m is not None else "inconnue"
    lines = [
        f"# Bilan — {name}",
        "",
        f"- Date : {_activity_date(activity)}",
        f"- Distance : {distance_km}",
        f"- Temps en mouvement : {_duration(duration_s)}",
        f"- Allure moyenne : {_pace(distance_m, duration_s)}",
    ]
    if average_hr is not None:
        lines.append(f"- Fréquence cardiaque moyenne : {average_hr:.0f} bpm")
    if max_hr is not None:
        lines.append(f"- Fréquence cardiaque maximale : {max_hr:.0f} bpm")
    if elevation is not None:
        lines.append(f"- Dénivelé positif : {elevation:.0f} m")
    if calories is not None:
        lines.append(f"- Calories : {calories:.0f} kcal")

    observations: list[str] = []
    if training_effect is not None:
        if training_effect < 2:
            observations.append("Charge légère : séance principalement tournée vers la récupération.")
        elif training_effect < 3:
            observations.append("Charge modérée : séance utile pour entretenir l'endurance.")
        elif training_effect < 4:
            observations.append("Séance productive : stimulus aérobie significatif.")
        else:
            observations.append("Charge élevée : prévois une récupération suffisante avant une nouvelle séance intense.")
        observations.append(f"Effet d'entraînement aérobie Garmin : {training_effect:.1f}/5.")

    if average_hr is not None and max_hr is not None and max_hr > 0:
        ratio = average_hr / max_hr
        if ratio >= 0.9:
            observations.append("La fréquence cardiaque est restée très proche du maximum observé : effort très soutenu.")
        elif ratio >= 0.8:
            observations.append("L'intensité cardiaque moyenne était soutenue.")
        else:
            observations.append("L'intensité cardiaque moyenne semble maîtrisée.")

    lines.extend(["", "## Première lecture", ""])
    lines.extend(f"- {item}" for item in observations)
    if not observations:
        lines.append("- Les données principales ont été enregistrées. L'analyse personnalisée sera enrichie avec ton profil et ton plan.")

    lines.extend(
        [
            "",
            "> Ce bilan est une aide à l'entraînement, pas un avis médical. Une douleur inhabituelle doit conduire à interrompre l'effort.",
            "",
        ]
    )
    return "\n".join(lines)
