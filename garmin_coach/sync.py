from __future__ import annotations

from pathlib import Path
from datetime import date
from typing import Any

from .activity_data import fetch_and_save_activity_bundle
from .config import REPORTS_DIR, Settings, ensure_directories
from .storage import load_seen_ids, save_activity, save_seen_ids
from .summary import build_summary, is_running
from .history_import import load_history_import, update_history_import
from .history_storage import iter_activity_history, save_activity_metadata
from .history_pagination import _activity_date, _validated_page
from .storage import load_activities

MAX_SYNC_PAGES = 10


def _refresh_multisport_history(client: Any, limit: int) -> list[dict[str, Any]]:
    """Refresh a bounded recent head, keeping import checkpoints frozen."""
    state = load_history_import(detect_stale=False)
    if state.get('status') == 'error':
        raise RuntimeError('Invalid historical import state; recent sync refused')
    known = {str(a['activityId']) for a in iter_activity_history()}
    # Switching readers to multisport must retain pre-import legacy running data.
    if not state.get('reference_date'):
        for activity in load_activities():
            save_activity_metadata(activity)
    batch_size = min(1000, max(1, limit))
    collected = []
    page_ids_seen: set[str] = set()
    today = date.today()
    anchor_raw = state.get('latest_sync_date') or state.get('reference_date')
    anchor = date.fromisoformat(anchor_raw) if anchor_raw else None
    anchor_ids = set(state.get('latest_sync_anchor_ids') or [])
    head_ids = []
    for page_index in range(MAX_SYNC_PAGES):
        page, ids = _validated_page(client.get_activities(page_index * batch_size, batch_size))
        if page_index == 0:
            head_ids = ids
        if ids and set(ids) <= page_ids_seen:
            raise RuntimeError('Repeated Garmin recent page; sync incomplete')
        overlap = False
        for activity in page:
            activity_day = _activity_date(activity)
            if activity_day is None or activity_day > today:
                raise ValueError('Invalid or future activity date in recent sync')
            if str(activity['activityId']) in known and (str(activity['activityId']) in anchor_ids or (not state.get('latest_sync_date') and anchor is not None and activity_day <= anchor and activity_day < today)):
                overlap = True
            save_activity_metadata(activity)
            if str(activity['activityId']) not in page_ids_seen:
                collected.append(activity)
        page_ids_seen.update(ids)
        if len(page) < batch_size or overlap:
            break
    else:
        raise RuntimeError('Recent sync page budget exhausted; sync incomplete')
    prior = state.get('latest_sync_date') or state.get('reference_date')
    watermark = max(today, date.fromisoformat(prior)) if prior else today
    update_history_import({'latest_sync_date': watermark.isoformat(), 'latest_sync_anchor_ids': head_ids})
    return collected


def _report_path(activity: dict[str, Any]) -> Path:
    activity_id = str(activity.get("activityId", "inconnue"))
    raw_date = str(activity.get("startTimeLocal", "date-inconnue"))[:10]
    return REPORTS_DIR / f"{raw_date}-{activity_id}.md"


def sync_once(
    client: Any,
    *,
    bootstrap: bool = False,
    settings: Settings | None = None,
    limit: int = 20,
) -> list[Path]:
    """Récupère les activités récentes et crée les nouveaux bilans de course."""
    del settings  # La synchronisation interne reste strictement sans IA.
    ensure_directories()
    seen = load_seen_ids()
    activities = _refresh_multisport_history(client, limit)
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
