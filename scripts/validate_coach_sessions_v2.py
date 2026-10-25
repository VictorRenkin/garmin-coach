"""Six synthetic model-intent probes; dry run unless explicitly --real.

This calls the configured provider directly with production instructions/schema,
not the deterministic grounded shortcut. It never confirms a draft or publishes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

PROMPTS = (
    ("week", "J'ai quoi comme séances cette semaine ?"),
    ("shorten", "Raccourcis ma séance de mardi à 25 minutes."),
    ("move", "Déplace-la à jeudi."),
    ("remove", "Supprime ma séance de mardi."),
    ("evening", "Je parle de celle du soir, pas du matin."),
    ("cancel", "Annule, finalement."),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--config-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--real", action="store_true")
    args = parser.parse_args()
    if args.real and not args.config_root:
        parser.error("--real exige --config-root explicite ; aucun appel par défaut")
    candidate = args.candidate.resolve()
    report = {"real": args.real, "candidate": str(candidate), "cases": [], "calls": 0,
              "scope": "provider intent only; no confirmation, mutation or Garmin"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="motionest-coach-v2-probe-") as temporary:
        root = Path(temporary)
        # Code only: application imports resolve their data/config roots here.
        shutil.copytree(candidate / "garmin_coach", root / "garmin_coach",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        from dotenv import load_dotenv
        if args.config_root:
            load_dotenv(args.config_root.resolve() / ".env", override=True)
        sys.path[:0] = [str(root), str(candidate)]
        from garmin_coach import config, coach_chat, dashboard, plan_proposal
        from garmin_coach.ai_provider import structured_completion
        from garmin_coach.coach_calendar_context import build_coach_calendar_context
        from tests.test_calendar_3c import source_for, double_proposal
        from tests.test_plan_proposal import settings as fixture_settings
        assert config.ROOT_DIR.samefile(root)
        report["source_digest"] = hashlib.sha256((candidate / "garmin_coach/coach_chat.py").read_bytes()).hexdigest()
        def forbidden(*a, **kw):
            raise AssertionError("Garmin/import forbidden during model probes")
        for key in ("connect", "authenticate_with_credentials", "start_initial_setup"):
            setattr(dashboard, key, forbidden)
        user = "synthetic-model-v2"
        source = source_for(user)
        raw = double_proposal(source).model_dump(mode="json")
        raw["period"] = {"start": "2026-10-26", "end": "2026-11-01"}
        for index, session in enumerate(raw["sessions"]):
            session.update(date="2026-10-27", starts_at=f"2026-10-27T{8 if index == 0 else 18:02d}:00:00+01:00",
                           duration_minutes=35, objective="Endurance facile", intensity_instruction="Facile, pouvoir parler",
                           structure={"warmup_minutes": 5, "main_set": "Courir facilement 25 minutes", "effort_minutes": 25, "cooldown_minutes": 5})
        proposal = plan_proposal.PlanProposalV2.model_validate(raw)
        state = plan_proposal.PlanProposalStateV2(status="available", input_fingerprint="a" * 64, proposal=proposal)
        configured = config.load_settings() if args.real else fixture_settings()
        report["provider"] = configured.ai_provider
        report["configured_model"] = configured.ai_model
        # Existing real store readers remain read-only; source state is synthetic.
        with config.user_context(user), patch("garmin_coach.coach_calendar_context.get_plan_proposal_state", return_value=state):
            calendar = build_coach_calendar_context(user)
            baseline = {str(p.relative_to(root / "data")): p.read_bytes() for p in (root / "data").rglob("*") if p.is_file()}
            for name, prompt in PROMPTS:
                active_calendar = json.loads(json.dumps(calendar))
                if name != "evening":
                    active_calendar["sessions"] = [active_calendar["sessions"][1]]
                recent = []
                if name == "move":
                    recent = [{"role": "user", "content": "Je parle de la séance evening de mardi 27 octobre à 18h."}]
                if name == "evening":
                    recent = [{"role": "assistant", "content": "Mardi comporte morning à 08h et evening à 18h. Laquelle ?"}]
                if name == "cancel":
                    recent = [{"role": "assistant", "content": "Brouillon local en attente pour evening ; aucun effet avant confirmation."}]
                context = {"calendrier_plan_v2": active_calendar, "conversation_recente": recent,
                           "contexte_multisport": {"reference_date": "2026-10-25"}}
                payload = {"demande": prompt, "contexte": context}
                item = {"name": name, "prompt": prompt, "payload_chars": len(json.dumps(payload, ensure_ascii=False))}
                report["cases"].append(item)
                save()
                if not args.real:
                    item["status"] = "prepared_no_call"
                    continue
                # Exactly one attempt per hypothesis, at most six initial calls.
                report["calls"] += 1
                try:
                    response = structured_completion(configured,
                        instructions=coach_chat.COACH_CHAT_INSTRUCTIONS + "\n" + coach_chat.HISTORY_SEARCH_INSTRUCTIONS,
                        payload=payload, result_type=coach_chat.CoachResponse, quality="economy",
                        reasoning_effort="none", category="coach_v2_probe", timeout_seconds=60,
                        max_retries=0, max_output_tokens=900)
                    output = response.model_dump(mode="json")
                    intent = output.get("session_action_v2")
                    checks = {"no_legacy_action": output.get("action") == "none",
                              "no_claimed_plan_mutation": output.get("plan_modifie") is False,
                              "bounded_message": len(output["message"]) <= 3000}
                    if intent:
                        checks["stable_identity"] = intent.get("session_id") == "evening"
                    if name in {"shorten", "move", "evening"}:
                        checks["intent_or_explicit_clarification"] = bool(intent) or output.get("conversation_state") == "needs_clarification"
                    if name == "week":
                        checks["duration_recognized"] = "35" in output["message"]
                    if name == "cancel":
                        checks["no_new_intent"] = intent is None
                    item.update(status="technical_success", response=output, checks=checks,
                                automatic_checks_passed=all(checks.values()), human_review_required=True)
                except Exception as exc:
                    item.update(status="failed", error_type=type(exc).__name__)
                usage = root / "data/users" / user / "ai_usage.json"
                report["usage"] = json.loads(usage.read_text()) if usage.exists() else []
                save()
            # Usage logging is the only allowed additional write during probes.
            after = {str(p.relative_to(root / "data")): p.read_bytes() for p in (root / "data").rglob("*") if p.is_file() and p.name != "ai_usage.json"}
            report["synthetic_state_unchanged"] = baseline == after
    report["total_tokens"] = sum(u.get("total_tokens", 0) for u in report.get("usage", []))
    save()
    print(json.dumps({"calls": report["calls"], "cases": len(report["cases"]), "total_tokens": report["total_tokens"]}))


if __name__ == "__main__":
    main()
