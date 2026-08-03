from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import PLANS_DIR

from .training_cycle import (
    WEEKLY_INSTRUCTIONS,
    WeeklyCycle,
    _context_for_week,
    load_activities,
    monday_of,
    plan_path,
    save_weekly_cycle,
    session_statuses,
)


def _latest_planned_week(current_day: date) -> date | None:
    candidates: list[date] = []
    for target in PLANS_DIR.glob("*.json"):
        try:
            week = date.fromisoformat(target.stem)
        except ValueError:
            continue
        if week <= current_day:
            candidates.append(week)
    return max(candidates, default=None)


def _close_expired_plan(week_start: date, current_day: date) -> list[str]:
    """Close unanswered sessions once the whole planned week has ended."""
    target = PLANS_DIR / f"{week_start.isoformat()}.json"
    if current_day <= week_start + timedelta(days=6) or not target.is_file():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    sessions = payload.get("seances", [])
    if not isinstance(sessions, list) or len(sessions) != 3:
        return []
    statuses = session_statuses(sessions, load_activities(), current_day)
    changed = False
    for session, status in zip(sessions, statuses):
        if status == "a_venir" and isinstance(session, dict):
            session["statut"] = "skippee"
            changed = True
    if changed:
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        statuses = session_statuses(sessions, load_activities(), current_day)
    return statuses


def weekly_context(today: date | None = None) -> dict[str, Any]:
    current_day = today or date.today()
    evaluated_week = _latest_planned_week(current_day)
    if evaluated_week is None:
        future_plan = PLANS_DIR / f"{(monday_of(current_day) + timedelta(days=7)).isoformat()}.json"
        reason = (
            "Le prochain plan est déjà programmé ; le bilan attend ses trois séances."
            if future_plan.is_file()
            else "Aucun plan actif de trois séances n'est encore disponible."
        )
        return {"due": False, "reason": reason}
    next_plan = PLANS_DIR / f"{(evaluated_week + timedelta(days=7)).isoformat()}.json"
    if next_plan.is_file():
        return {"due": False, "reason": "Le plan de la semaine suivante existe déjà."}
    statuses = _close_expired_plan(evaluated_week, current_day)
    context = _context_for_week(evaluated_week)
    adherence = context["semaine_evaluee"]["adherence_calculee"]
    cycle_closed = len(statuses) == 3 and "a_venir" not in statuses
    if adherence != "3/3" and not cycle_closed:
        return {
            "due": False,
            "reason": f"Le bilan attend la fin des trois séances ({adherence} terminées).",
        }
    return {
        "due": True,
        "evaluated_week": evaluated_week.isoformat(),
        "instructions": WEEKLY_INSTRUCTIONS,
        "schema": WeeklyCycle.model_json_schema(),
        "context": context,
    }


def save_codex_weekly_cycle(evaluated_week: date, source: Path) -> Path:
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Le brouillon hebdomadaire n'est pas un JSON valide.") from exc
    cycle = WeeklyCycle.model_validate(raw)
    context = _context_for_week(evaluated_week)
    adherence = context["semaine_evaluee"]["adherence_calculee"]
    cycle.revue.adherence = adherence
    if adherence == "non mesurable":
        prefix = "Aucun plan de trois séances n'existait : l'adhérence ne peut pas être mesurée. "
    else:
        completed = int(adherence[0])
        missing = 3 - completed
        prefix = (
            "Les trois séances planifiées ont été enregistrées. "
            if missing == 0
            else f"{missing} séance(s) planifiée(s) n'ont pas été enregistrée(s). "
        )
    cycle.revue.message_adherence = prefix + cycle.revue.message_adherence
    required_dates = context["semaine_suivante"]["dates_obligatoires"]
    for session, required_date in zip(cycle.seances_semaine_suivante, required_dates):
        session.date = required_date
    return save_weekly_cycle(evaluated_week, cycle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cycle hebdomadaire créé par Codex")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("context", help="Afficher le bilan après trois séances terminées")
    save = subparsers.add_parser("save", help="Valider et sauvegarder le nouveau plan")
    save.add_argument("--week-start", required=True)
    save.add_argument("--input", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "context":
        print(json.dumps(weekly_context(), ensure_ascii=False, indent=2))
        return 0
    target = save_codex_weekly_cycle(date.fromisoformat(args.week_start), args.input)
    print(f"Plan hebdomadaire enregistré : {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
