"""Isolated phase 1.2A checks; --real authorizes six existing-provider calls."""
import argparse
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from garmin_coach import config, dashboard, coach_chat, history_import, history_storage
from garmin_coach.conversation_sport_context import build_conversation_sport_context
from garmin_coach.local_activity_history import load_local_activity_history
from garmin_coach.runtime_context import build_runtime_motionest_context
from garmin_coach.training_history_summary import sport_classification

REFERENCE = date(2026, 10, 14)
PROFILES = {
    "beginner": [(d, "running") for d in (0, 3, 7, 10)],
    "busy": [(d, "running") for d in range(14) for _ in range(3)],
    "triathlon": [(d, sport) for d in (0, 3, 7, 10) for sport in ("running", "cycling", "lap_swimming")],
    "old": [(100, "running")], "no_hr": [(0, "running"), (8, "cycling")], "empty": [],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true")
    args = parser.parse_args()
    settings = config.load_settings()
    report = {"reference_date": str(REFERENCE), "scenarios": [], "real_calls": [],
              "token_note": "Actual provider usage; bytes are not token estimates."}
    with tempfile.TemporaryDirectory(prefix="motionest-12a-") as temp:
        root = Path(temp)
        config.GLOBAL_DATA_DIR = root
        config.USERS_DATA_DIR = root / "users"
        config.SECRETS_DIR = root / "secrets"
        for name, sessions in PROFILES.items():
            with config.user_context(name):
                history_import.initialize_history_import("all", reference_date=REFERENCE)
                for i, (offset, sport) in enumerate(sessions):
                    row = {"activityId": str(i), "activityName": "PRIVATE SYNTHETIC TITLE",
                           "activityType": {"typeKey": sport},
                           "startTimeLocal": f"{REFERENCE-timedelta(days=offset)}T08:00:00",
                           "distance": 5000, "movingDuration": 1800,
                           "elevationGain": 25}
                    if name != "no_hr":
                        row.update(averageHR=135, maxHR=155, activityTrainingLoad=40, aerobicTrainingEffect=2.5)
                    if sport == "cycling":
                        row["averagePower"] = 180
                    history_storage.save_activity_metadata(row)
                runtime = build_runtime_motionest_context(name)
                new = build_conversation_sport_context(name)
                rows = load_local_activity_history(start_date=REFERENCE-timedelta(days=27), end_date=REFERENCE)
                old_rows = []
                for row in rows[-10:]:
                    sport, source = sport_classification(row)
                    old_rows.append({"sport": sport, "classification_source": source,
                        "date": row.get("startTimeLocal"), "distance_m": row.get("distance"),
                        "duration_seconds": row.get("movingDuration", row.get("duration")),
                        "average_heart_rate": row.get("averageHR"), "average_power_watts": row.get("averagePower")})
                old = {"status": "available", "reference_date": str(REFERENCE),
                    "context": runtime.context.model_dump(mode="json"),
                    "recent_recorded_activities": old_rows, "recent_activity_limit": 10,
                    "recent_window_days": 28, "recent_activities_truncated": len(rows)>10,
                    "provenance": runtime.context.training_history_summary.model_dump(mode="json"),
                    "input_diagnostics": [d.model_dump(mode="json") for d in runtime.input_diagnostics]}
                context = dashboard.coach_context()
                report["scenarios"].append({"name":name,
                    "counts": {**{k:w["summary"]["activity_count"] for k,w in new["weeks"].items()},
                               **{k:w["activity_count"] for k,w in new["history"]["windows"].items()}},
                    "multisport_bytes_before":len(json.dumps(old,ensure_ascii=False,separators=(",",":")).encode()),
                    "multisport_bytes_after":len(json.dumps(new,ensure_ascii=False,separators=(",",":")).encode()),
                    "truncated":new["budget"]["activities_truncated"]})
                if args.real and name in ("beginner", "busy", "triathlon"):
                    for version, multi in (("before",old),("after",new)):
                        payload = dict(context, contexte_multisport=multi)
                        if version == "before":
                            # Keep the former payload and instructions intact for comparison.
                            original = coach_chat.COACH_CHAT_INSTRUCTIONS
                            start = original.index("contexte_multisport est")
                            end = original.index("Une valeur null", start)
                            coach_chat.COACH_CHAT_INSTRUCTIONS = original[:start] + (
                                "contexte_multisport contient le contexte canonique, sa provenance et sa confiance\n"
                                "par composant. recent_recorded_activities est limite a dix activites sur 28 jours;\n"
                                "courses_recentes et trace_sportive restent des vues de course uniquement.\n") + original[end:]
                        try:
                            response = coach_chat.generate_response({"content":
                                "Compare mes deux dernières semaines enregistrées : combien de séances par semaine, quels sports, et que suggèrent les tendances ? N'invente rien.",
                                "coach_mode":"balanced"}, settings, payload)
                            usage_path = root / "users" / name / "ai_usage.json"
                            usage = json.loads(usage_path.read_text())[-1] if usage_path.exists() else None
                            report["real_calls"].append({"profile":name,"version":version,
                                "message":response.message,"usage":usage})
                        except Exception as exc:
                            report["real_calls"].append({"profile":name,"version":version,"error_type":type(exc).__name__})
                        finally:
                            if version == "before": coach_chat.COACH_CHAT_INSTRUCTIONS = original
                        print(f"{name} {version}: completed", flush=True)
    target = Path("reports/phase-1.2a-validation.json")
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
