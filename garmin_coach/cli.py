from __future__ import annotations

import argparse
import zipfile
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
from .config import TOKEN_DIR, load_settings
from .garmin import connect
from .garmin_workouts import publish_plan
from .coach_chat import add_assistant_prompt
from .onboarding import inspect_garmin_account, onboarding_prompt
from .storage import load_activities
from .sync import sync_once
from .strava_import import import_strava
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
    print("Pour te déconnecter : .\\deconnexion-garmin.cmd")
    print()


def show_login_failure(reason: str) -> None:
    prepare_console()
    print()
    print("╭──────────────────────────────────────╮")
    print("│  👟  GARMIN COACH                    │")
    print("├──────────────────────────────────────┤")
    print("│  ❌  CONNEXION ÉCHOUÉE               │")
    print("╰──────────────────────────────────────╯")
    print(f"{reason}\n")


def logout_local_session() -> int:
    """Ferme la session en supprimant uniquement les deux jetons Garmin locaux."""
    removed = 0
    for name in ("oauth1_token.json", "oauth2_token.json"):
        target = TOKEN_DIR / name
        if target.is_file():
            target.unlink()
            removed += 1
    try:
        TOKEN_DIR.rmdir()
    except OSError:
        pass
    prepare_console()
    print()
    print("╭──────────────────────────────────────╮")
    print("│  👟  GARMIN COACH                    │")
    print("├──────────────────────────────────────┤")
    print("│  🚪  DÉCONNEXION RÉUSSIE             │")
    print("╰──────────────────────────────────────╯")
    print("Session Garmin fermée." if removed else "Aucune session Garmin active.")
    print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coach personnel Garmin")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("login", help="Tester la connexion à Garmin Connect")
    subparsers.add_parser("logout", help="Fermer la session Garmin locale")
    subparsers.add_parser("sync", help="Importer les nouvelles courses et créer leurs bilans")
    subparsers.add_parser("watch", help="Ancien mode continu, désormais désactivé")
    analyze = subparsers.add_parser(
        "analyze", help="Demander à l'IA d'analyser les courses importées"
    )
    analyze.add_argument(
        "--all", action="store_true", help="Analyser toutes les courses sans analyse IA"
    )
    weekly = subparsers.add_parser(
        "weekly", help="Faire le bilan d'une semaine et créer le plan personnalisé suivant"
    )
    weekly.add_argument(
        "--week-start", help="Lundi de la semaine à évaluer (AAAA-MM-JJ)"
    )
    weekly.add_argument(
        "--local", action="store_true", help="Conserver le plan localement sans l'envoyer à Garmin"
    )
    strava = subparsers.add_parser(
        "import-strava", help="Importer un export personnel Strava ZIP ou activities.csv"
    )
    strava.add_argument("source", help="Chemin de l'archive ZIP ou du fichier activities.csv")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = load_settings()

    if args.command == "logout":
        return logout_local_session()

    if args.command == "import-strava":
        try:
            reports = import_strava(args.source)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            print(f"IMPORT STRAVA IMPOSSIBLE : {exc}")
            return 1
        print(f"Courses Strava importées : {len(reports)}")
        for report in reports:
            print(f"Bilan créé : {report}")
        return 0

    if args.command == "analyze":
        if not settings.ai_api_key:
            print("ANALYSE IA : NON CONFIGURÉE")
            print("Configure une clé IA dans le fichier .env, puis relance la commande.")
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
        if not settings.ai_api_key:
            print("BILAN HEBDOMADAIRE : IA NON CONFIGURÉE")
            print("Configure une clé IA dans le fichier .env, puis relance la commande.")
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
        if args.local:
            print("Plan conservé localement : aucune séance envoyée à une montre.")
            return 0
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
        show_login_failure("Adresse e-mail ou mot de passe incorrect.")
        return 1
    except GarminConnectTooManyRequestsError:
        show_login_failure(
            "trop de tentatives. Attends quelques minutes avant de réessayer."
        )
        return 1
    except (GarminConnectConnectionError, GarthException) as exc:
        detail = str(exc).lower()
        if "401" in detail or "unauthorized" in detail or "authentication" in detail:
            show_login_failure("Adresse e-mail ou mot de passe incorrect.")
        else:
            show_login_failure("Garmin Connect ou la connexion réseau est indisponible.")
        return 1
    except ValueError as exc:
        show_login_failure(str(exc))
        return 1

    if args.command == "login":
        try:
            onboarding = inspect_garmin_account(client)
            if onboarding.get("status") == "needs_profile":
                add_assistant_prompt(
                    onboarding_prompt(onboarding), "garmin-onboarding-profile"
                )
        except Exception:
            # Une inspection indisponible ne doit pas invalider une connexion réussie.
            pass
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
