from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from .config import COACH_MEMORY_FILE, ensure_directories


def load_memory() -> list[dict[str, str]]:
    if not COACH_MEMORY_FILE.is_file():
        return []
    try:
        payload = json.loads(COACH_MEMORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def remember_facts(facts: list[str]) -> None:
    """Conserve de courts faits explicites, avec déduplication."""
    memory = load_memory()
    known = {str(item.get("fact") or "").strip().casefold() for item in memory}
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for raw in facts:
        fact = " ".join(str(raw).split()).strip()[:300]
        if len(fact) < 3 or fact.casefold() in known:
            continue
        memory.append({"fact": fact, "recorded_at": now})
        known.add(fact.casefold())
    ensure_directories()
    COACH_MEMORY_FILE.write_text(
        json.dumps(memory[-60:], ensure_ascii=False, indent=2), encoding="utf-8"
    )


def activity_trace(activities: list[dict[str, Any]]) -> dict[str, Any]:
    """Résume tout l'historique sans envoyer chaque activité au modèle."""
    today = date.today()
    valid: list[tuple[date, dict[str, Any]]] = []
    for item in activities:
        try:
            day = date.fromisoformat(str(item.get("date") or "")[:10])
        except ValueError:
            continue
        valid.append((day, item))

    def summary(rows: list[tuple[date, dict[str, Any]]]) -> dict[str, Any]:
        distances = [float(item["distanceKm"]) for _, item in rows if isinstance(item.get("distanceKm"), (int, float))]
        paces = [float(item["paceSeconds"]) for _, item in rows if isinstance(item.get("paceSeconds"), (int, float))]
        return {
            "courses": len(rows),
            "distance_totale_km": round(sum(distances), 1),
            "distance_moyenne_km": round(sum(distances) / len(distances), 1) if distances else None,
            "distance_maximale_km": round(max(distances), 1) if distances else None,
            "allure_moyenne_secondes_km": round(sum(paces) / len(paces)) if paces else None,
        }

    months: dict[str, list[tuple[date, dict[str, Any]]]] = defaultdict(list)
    for row in valid:
        months[row[0].strftime("%Y-%m")].append(row)
    return {
        "ensemble_historique": summary(valid),
        "derniers_28_jours": summary([row for row in valid if row[0] >= today - timedelta(days=28)]),
        "derniers_90_jours": summary([row for row in valid if row[0] >= today - timedelta(days=90)]),
        "evolution_mensuelle": [
            {"mois": month, **summary(months[month])}
            for month in sorted(months)[-6:]
        ],
    }
