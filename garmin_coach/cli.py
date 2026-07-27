from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
    GarthException,
)

from .ai_analysis import analysis_path, analyze_and_save
from .config import load_settings
from .garmin import connect
from .garmin_workouts import publish_plan
from .storage import load_activities
from .sync import sync_once
from .training_cycle import find_planned_session, monday_of, review_and_plan


def prepare_console() -> None:
    """Active l'UTF-8 dans les consoles Windows pour les bordures et pictogrammes."""
    if os.name == "nt" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def show_login_success() -> None:
    prepare_console()
    print()
    print("╭──────────────────────────────────────╮")
    print("│  👟  GARMIN COACH                    │")
    print("├──────────────────────────────────────┤")
    print("│  ✅  CONNEXION RÉUSSIE               │")
    print("│                                      │")
    print("│  🏃  Garmin Connect est synchronisé  │")
    print("│  📊  Prêt à analyser tes courses     │")
    print("╰──────────────────────────────────────╯")
    print()


def show_login_failure(reason: str) -> None:
    prepare_console()
    print()
    print("╭──────────────────────────────────────╮")
    print("│  👟  GARMIN COACH                    │")
    print("├──────────────────────────────────────┤")
    print("│  ❌  CONNEXION ÉCHOUÉE               │")
    print("╰──────────────────────────────────────╯")
    print(f"\nRaison : {reason}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coach personnel Garmin")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("login", help="Tester la connexion à Garmin Connect")
    subparsers.add_parser("sync", help="Importer les nouvelles courses et créer leurs bilans")
    subparsers.add_parser("watch", help="Ancien mode continu, désormais désactivé")
    analyze = subparsers.add_parser(
        "analyze", help="Demander à l'IA d'analyser les courses importées"
    )
    analyze.add_argument(
        "--all", action="store_true", help="Analyser toutes les courses sans analyse IA"
    )
    weekly = subparsers.add_parser(
        "weekly", help="Faire le bilan d'une semaine et créer les 3 séances suivantes"
    )
    weekly.add_argument(
        "--week-start", help="Lundi de la semaine à évaluer (AAAA-MM-JJ)"
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = load_settings()

    if args.command == "analyze":
        if not settings.openai_api_key:
            print("ANALYSE IA : NON CONFIGURÉE")
            print("Ajoute OPENAI_API_KEY dans le fichier .env, puis relance la commande.")
            return 2
        activities = load_activities()
        pending = [
            activity
            for activity in activities
            if not analysis_path(str(activity.get("activityId", ""))).is_file()
        ]
        targets = pending if args.all else pending[-1:]
        if not targets:
            print("Toutes les courses demandées possèdent déjà une analyse IA.")
            return 0
        for activity in targets:
            target = analyze_and_save(
                activity,
                activities,
                settings,
                planned_session=find_planned_session(activity),
            )
            print(f"Analyse IA créée : {target}")
        return 0

    if args.command == "weekly":
        if not settings.openai_api_key:
            print("BILAN HEBDOMADAIRE : IA NON CONFIGURÉE")
            print("Ajoute OPENAI_API_KEY dans le fichier .env, puis relance la commande.")
            return 2
        if args.week_start:
            evaluated_week = date.fromisoformat(args.week_start)
        else:
            today = date.today()
            evaluated_week = (
                monday_of(today) - timedelta(days=7)
                if today.weekday() == 0
                else monday_of(today)
            )
        target = review_and_plan(evaluated_week, settings)
        print(f"Bilan terminé et prochaine semaine créée : {target}")
        try:
            garmin_client = connect(settings)
            published = publish_plan(garmin_client, target)
            print(f"Séances programmées dans Garmin Connect : {published}/3")
        except Exception as exc:
            print(
                "Le plan est sauvegardé, mais sa publication Garmin a échoué : "
                f"{type(exc).__name__}"
            )
            return 1
        return 0

    try:
        client = connect(settings)
    except GarminConnectAuthenticationError:
        show_login_failure("adresse, mot de passe ou code de vérification refusé.")
        return 1
    except GarminConnectTooManyRequestsError:
        show_login_failure(
            "trop de tentatives. Attends quelques minutes avant de réessayer."
        )
        return 1
    except (GarminConnectConnectionError, GarthException):
        show_login_failure("Garmin Connect ou la connexion réseau est indisponible.")
        return 1
    except ValueError as exc:
        show_login_failure(str(exc))
        return 1

    if args.command == "login":
        show_login_success()
        return 0
    if args.command == "sync":
        reports = sync_once(client)
        if reports:
            for report in reports:
                print(f"Bilan créé : {report}")
        else:
            print("Aucune nouvelle course à analyser.")
        return 0

    print("Surveillance périodique désactivée.")
    print("Ouvre le tableau de bord et clique sur « Actualiser et analyser ».")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
