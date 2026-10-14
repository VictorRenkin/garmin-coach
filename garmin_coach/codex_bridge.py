from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .ai_analysis import (
    COACH_INSTRUCTIONS,
    CourseAnalysis,
    analysis_path,
    build_context,
    render_analysis_report,
    report_path,
    save_analysis,
)
from .activity_data import detail_path, fetch_and_save_activity_bundle
from .coach_chat import pending_requests, save_response
from .config import CODEX_STATE_FILE, ensure_directories
from .config import load_settings
from .garmin import connect
from .garmin_workouts import publish_plan
from .garmin_workouts import replace_future_plan
from .garmin_workouts import apply_plan_revision
from .storage import load_activities
from .training_cycle import find_planned_session


def load_baseline_ids() -> set[str]:
    if not CODEX_STATE_FILE.is_file():
        return set()
    try:
        payload = json.loads(CODEX_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {str(value) for value in payload.get("baseline_activity_ids", [])}


def activate_from_now() -> int:
    """Ignore l'historique déjà importé et analyse uniquement les futures courses."""
    ensure_directories()
    activity_ids = {
        str(activity.get("activityId", ""))
        for activity in load_activities()
        if activity.get("activityId") is not None
    }
    CODEX_STATE_FILE.write_text(
        json.dumps(
            {"baseline_activity_ids": sorted(activity_ids)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return len(activity_ids)


def pending_contexts() -> list[dict[str, Any]]:
    """Prépare les courses que Codex doit encore analyser, sans identité ni GPS."""
    activities = load_activities()
    baseline_ids = load_baseline_ids()
    pending: list[dict[str, Any]] = []
    for activity in activities:
        activity_id = str(activity.get("activityId", ""))
        if (
            not activity_id
            or activity_id in baseline_ids
            or analysis_path(activity_id).is_file()
        ):
            continue
        pending.append(
            {
                "activity_id": activity_id,
                "instructions": COACH_INSTRUCTIONS,
                "schema": CourseAnalysis.model_json_schema(),
                "contexte": build_context(
                    activity,
                    activities,
                    planned_session=find_planned_session(activity),
                ),
            }
        )
    return pending


def save_codex_analysis(activity_id: str, source: Path) -> Path:
    """Valide une réponse Codex avant de l'afficher dans le tableau de bord."""
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Le brouillon d'analyse n'est pas un JSON valide.") from exc
    analysis = CourseAnalysis.model_validate(raw)
    activity = next(
        (
            item
            for item in load_activities()
            if str(item.get("activityId", "")) == activity_id
        ),
        None,
    )
    if activity is None:
        raise ValueError(f"Course Garmin inconnue : {activity_id}")
    save_analysis(activity, analysis)
    target = report_path(activity)
    target.write_text(render_analysis_report(activity, analysis), encoding="utf-8")
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pont entre Motionest et Codex")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "activate", help="Commencer l'analyse aux prochaines courses seulement"
    )
    subparsers.add_parser("pending", help="Afficher les courses à analyser")
    save = subparsers.add_parser("save-analysis", help="Valider une analyse Codex")
    save.add_argument("--activity-id", required=True)
    save.add_argument("--input", required=True, type=Path)
    publish = subparsers.add_parser(
        "publish-plan", help="Publier un plan Codex validé dans Garmin Connect"
    )
    publish.add_argument("--input", required=True, type=Path)
    subparsers.add_parser(
        "backfill-details", help="Importer les détails sportifs de l'historique"
    )
    replace = subparsers.add_parser(
        "replace-plan", help="Remplacer les futures séances Garmin sans doublons"
    )
    replace.add_argument("--input", required=True, type=Path)
    subparsers.add_parser(
        "pending-requests", help="Afficher les demandes du tableau de bord"
    )
    response = subparsers.add_parser(
        "save-response", help="Enregistrer la réponse de Codex dans le tableau de bord"
    )
    response.add_argument("--request-id", required=True)
    response.add_argument("--input", required=True, type=Path)
    revision = subparsers.add_parser("apply-revision", help="Appliquer un plan révisé")
    revision.add_argument("--current", required=True, type=Path)
    revision.add_argument("--replacement", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "activate":
        count = activate_from_now()
        print(f"Coach ChatGPT activé : {count} course(s) existante(s) conservée(s) comme historique.")
        return 0
    if args.command == "pending":
        print(json.dumps({"courses": pending_contexts()}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "pending-requests":
        print(
            json.dumps(
                {"requests": pending_requests()}, ensure_ascii=False, indent=2
            )
        )
        return 0
    if args.command == "save-response":
        answer = save_response(args.request_id, args.input)
        print(f"Réponse enregistrée : {answer.get('id')}")
        return 0
    if args.command == "apply-revision":
        removed, published = apply_plan_revision(
            connect(load_settings()), args.current, args.replacement
        )
        print(f"Révision appliquée : {removed} retirée(s), {published} publiée(s).")
        return 0
    if args.command == "backfill-details":
        client = connect(load_settings())
        created = 0
        for activity in load_activities():
            activity_id = str(activity.get("activityId", ""))
            if not activity_id or detail_path(activity_id).is_file():
                continue
            fetch_and_save_activity_bundle(client, activity)
            created += 1
            print(f"Détails importés : {activity_id}")
        print(f"Historique enrichi : {created} course(s).")
        return 0
    if args.command == "replace-plan":
        removed, published = replace_future_plan(
            connect(load_settings()), args.input
        )
        print(f"Séances remplacées : {removed}; programmées : {published}/3")
        return 0
    if args.command == "publish-plan":
        count = publish_plan(connect(load_settings()), args.input)
        print(f"Séances programmées dans Garmin Connect : {count}/3")
        return 0
    target = save_codex_analysis(args.activity_id, args.input)
    print(f"Analyse enregistrée : {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
