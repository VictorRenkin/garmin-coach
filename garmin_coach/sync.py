from __future__ import annotations

from pathlib import Path
from typing import Any

from .activity_data import fetch_and_save_activity_bundle
from .config import REPORTS_DIR, Settings, ensure_directories
from .storage import load_seen_ids, save_activity, save_seen_ids
from .summary import build_summary, is_running


def _report_path(activity: dict[str, Any]) -> Path:
    activity_id = str(activity.get("activityId", "inconnue"))
    raw_date = str(activity.get("startTimeLocal", "date-inconnue"))[:10]
    return REPORTS_DIR / f"{raw_date}-{activity_id}.md"


def sync_once(
    client: Any, *, bootstrap: bool = False, settings: Settings | None = None
) -> list[Path]:
    """Récupère les activités récentes et crée les nouveaux bilans de course."""
    del settings  # La synchronisation interne reste strictement sans IA.
    ensure_directories()
    seen = load_seen_ids()
    activities = client.get_activities(0, 20)
    running = [activity for activity in activities if is_running(activity)]

    # Au premier lancement, on mémorise l'historique sans produire 20 alertes.
    if not seen and bootstrap:
        save_seen_ids({str(item.get("activityId")) for item in running})
        return []

    created: list[Path] = []
    for activity in reversed(running):
        activity_id = str(activity.get("activityId"))
        if activity_id in seen:
            continue
        save_activity(activity)
        try:
            fetch_and_save_activity_bundle(client, activity)
        except Exception as exc:
            print(
                f"Détails sportifs en attente pour {activity_id} : {type(exc).__name__}",
                flush=True,
            )
        target = _report_path(activity)
        target.write_text(build_summary(activity), encoding="utf-8")
        created.append(target)
        seen.add(activity_id)

    save_seen_ids(seen)
    return created


def watch(client: Any, settings: Settings) -> None:
    """Ancien point d'entrée conservé sans action automatique."""
    del client, settings
    print(
        "Surveillance automatique désactivée. Utilise « Actualiser et analyser ».",
        flush=True,
    )