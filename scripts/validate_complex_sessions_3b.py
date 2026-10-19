"""Five bounded real-AI checks. Synthetic accounts and VerifiedClient only.

No calls are made without --real. The API-call ceiling includes repairs/retries.
Reports distinguish transport/structured output from exact executable behavior.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from garmin_coach import ai_provider, coach_chat, config, dashboard, garmin_delivery, plan_acceptance, plan_proposal
from garmin_coach.garmin_workouts import build_running_workout
from garmin_coach.plan_proposal import PlanPeriodV2, PlanProposalStateV2, PlanProposalV2, PlanSessionV2
from garmin_coach.session_blocks import SessionBlock
from garmin_coach.training_cycle import PlannedSession
from tests.test_garmin_delivery import VerifiedClient
from tests.test_garmin_workouts import make_session
from tests.test_plan_proposal import runtime_for, states


def block(effort, recovery, repetitions, zone):
    return dict(effort_minutes=effort, recovery_minutes=recovery, repetitions=repetitions,
                intensity_instruction="Effort contrôlé.", effort_hr_zone=zone, recovery_hr_zone=1)


CASES = {
    "v2_two_blocks": [block(3, .5, 2, 3), block(2, 1.5, 2, 4)],
    "legacy_mixed_intensity": [block(2, .5, 3, 3), block(1, 1.5, 3, 4)],
    "legacy_recovery_45": [block(3, .75, 4, 3), block(2, .5, 2, 4)],
    "invalid_nested": None,
    "legacy_modify_existing": [block(3, .75, 4, 3), block(2, 1.5, 2, 4)],
}


def effective_seconds(steps):
    return sum(step.numberOfIterations * effective_seconds(step.workoutSteps)
               if hasattr(step, "numberOfIterations") else step.endConditionValue for step in steps)


def compare_blocks(actual, expected):
    if actual is None or len(actual) != len(expected):
        return False
    keys = ("effort_minutes", "recovery_minutes", "repetitions", "effort_hr_zone", "recovery_hr_zone")
    return all(all(getattr(item, key) == wanted[key] for key in keys)
               for item, wanted in zip(actual, expected))


def exercise_acceptance(user, current):
    """Exercise actual explicit acceptance and existing delivery, using a fake client."""
    client = VerifiedClient()
    with patch.object(garmin_delivery, "get_plan_proposal_state", lambda user_id: current):
        try:
            garmin_delivery.publish_accepted_session(user, client, session_index=0, scheduled_date=date(2099, 10, 13))
        except garmin_delivery.GarminDeliveryError as error:
            assert error.code == "proposal_not_accepted"
        else:
            raise AssertionError("A non-accepted proposal was delivered.")
        assert client.uploads == 0
        plan_acceptance.record_plan_acceptance_decision(user, decision="accepted",
            input_fingerprint=current.input_fingerprint,
            expected_proposal_fingerprint=plan_proposal.proposal_fingerprint(current.proposal), proposal_state=current)
        first = garmin_delivery.publish_accepted_session(user, client, session_index=0, scheduled_date=date(2099, 10, 13))
        second = garmin_delivery.publish_accepted_session(user, client, session_index=0, scheduled_date=date(2099, 10, 13))
        assert first == second and client.uploads == 1 and len(client.schedules) == 1
        return dict(first=first, second=second, simulated_uploads=client.uploads)


def requested_session(expected):
    seconds = (10 + 5 + sum(item["repetitions"] * (item["effort_minutes"] + item["recovery_minutes"])
                             for item in expected)) * 60
    instructions = [f"bloc {index + 1} : {item['repetitions']} répétitions de {item['effort_minutes']:g} min "
        f"en zone FC {item['effort_hr_zone']}, récupération {item['recovery_minutes'] * 60:g} secondes en zone FC 1 "
        "après chaque effort, y compris le dernier" for index, item in enumerate(expected)]
    return ("; puis ".join(instructions) + f". Échauffement 10 min, retour au calme 5 min. "
            f"Durée totale exacte {seconds / 60:g} minutes. Les cibles FC sont les cibles synthétiques "
            "explicitement prescrites ici ; ne les remplace pas par d'autres cibles."), round(seconds)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--env", type=Path)
    parser.add_argument("--case", choices=list(CASES), action="append")
    parser.add_argument("--output", type=Path, default=Path("reports/phase-3b-real.json"))
    args = parser.parse_args()
    if not args.real:
        raise SystemExit("Use --real explicitly; no calls made. Cases: " + ", ".join(CASES))
    if args.env:
        load_dotenv(args.env, override=True)
    settings = replace(config.load_settings(), email=None, password=None)
    if not settings.ai_api_key or settings.ai_provider != "openai" or settings.ai_base_url:
        raise SystemExit("Configured native OpenAI provider required; no calls made.")
    selected = args.case or list(CASES)
    report = dict(synthetic_only=True, real_garmin_calls=0, api_attempts=0,
                  api_call_ceiling=len(selected), profiles=[])
    original = coach_chat.OpenAI

    class Responses:
        def __init__(self, inner):
            self.inner = inner

        def parse(self, **kwargs):
            assert report["api_attempts"] < report["api_call_ceiling"], "Bounded call ceiling reached."
            report["api_attempts"] += 1
            assert "PRIVATE-" not in kwargs["input"]
            result = self.inner.parse(**kwargs)
            report["profiles"][-1]["technical_success"] = True
            return result

    class Client:
        def __init__(self, **kwargs):
            kwargs["max_retries"] = 0
            self.responses = Responses(original(**kwargs).responses)

    def save():
        usages = [entry for item in report["profiles"] for entry in item.get("usage", [])]
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            report[key] = sum(entry[key] for entry in usages)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="motionest-complex-3b-") as tmp:
        root = Path(tmp)
        with patch.multiple(config, GLOBAL_DATA_DIR=root, USERS_DATA_DIR=root / "users", SECRETS_DIR=root / "secrets"), \
             patch.object(coach_chat, "OpenAI", Client), patch.object(ai_provider, "OpenAI", Client):
            for name in selected:
                user = "synthetic-" + name
                item = dict(name=name, technical_success=False, functional_success=False)
                report["profiles"].append(item)
                with config.user_context(user):
                    try:
                        expected = CASES[name]
                        baseline = make_session(date="2099-10-13")
                        if name == "legacy_modify_existing":
                            baseline = PlannedSession.model_validate({**baseline.model_dump(),
                                "blocks": CASES["legacy_recovery_45"], "repetitions_bloc": None,
                                "effort_minutes": None, "recuperation_minutes": None, "duree_estimee_minutes": 35})
                        if name == "v2_two_blocks":
                            runtime = runtime_for(user)
                            recommendation, decision = states(runtime, confirmed=1)
                            source = plan_proposal.build_plan_generation_input_v2(user, runtime,
                                recommendation_state=recommendation, decision_state=decision)
                            detail, seconds = requested_session(expected)
                            source.important_preferences = ["Cette semaine, une séance de course comportant exactement " + detail]
                            item["request"] = source.important_preferences[0]
                            with patch.object(plan_proposal, "build_plan_generation_input_v2", lambda *a, **kw: source):
                                current = plan_proposal.generate_plan_proposal(user, runtime=runtime,
                                    recommendation_state=recommendation, decision_state=decision, settings=settings)
                            item["state"] = current.model_dump(mode="json")
                            assert current.status == "available" and current.proposal is not None
                            generated = current.proposal.sessions[0]
                            assert compare_blocks(generated.structure.blocks, expected)
                            assert generated.structure.warmup_minutes == 10 and generated.structure.cooldown_minutes == 5
                            assert generated.duration_minutes == seconds / 60
                            workout = garmin_delivery.build_plan_session_workout(generated)
                            assert effective_seconds(workout.workoutSegments[0].workoutSteps) == seconds == workout.estimatedDurationInSecs
                            item["delivery"] = exercise_acceptance(user, current)
                            item["workout"] = workout.model_dump(mode="json")
                        else:
                            target = root / f"{name}.json"
                            target.write_text(json.dumps({"seances": [baseline.model_dump()]}, ensure_ascii=False), encoding="utf-8")
                            before = target.read_bytes()
                            if expected is None:
                                prompt = ("Modifie la séance 1 en imbriquant 3 séries de 4 répétitions dans une autre répétition. "
                                    "Je veux explicitement des blocs imbriqués, aucune simplification en blocs successifs. "
                                    "Si le contrat ne sait pas représenter cette demande, explique la limite sans action ni changement du plan.")
                            else:
                                detail, seconds = requested_session(expected)
                                prompt = ("Modifie la séance 1 : " + detail + " Conserve la date et la distance estimée. "
                                    "Je confirme précisément cette modification ; retourne son patch structuré sans publication Garmin.")
                            context = dict(objectif={"title": "Course à pied synthétique", "sessionsPerWeek": 1},
                                plan_actif={"seances": [baseline.model_dump(mode="json")]}, onboarding={"status": "ready"},
                                conversation_recente=[], memoire_pertinente=[])
                            item["request"] = prompt
                            response = coach_chat.generate_response({"content": prompt, "coach_mode": "deep"}, settings, context)
                            item["technical_success"] = True
                            item["response"] = response.model_dump(mode="json")
                            assert target.read_bytes() == before, "Model calls must not mutate the plan."
                            if expected is None:
                                assert response.action == "none" and not response.plan_modifie
                                assert response.session_patch is None
                            else:
                                assert response.action == "modify_session" and response.session_index == 0
                                assert response.session_patch is not None
                                changed = dashboard.modify_planned_session(target, 0, response.session_patch.model_dump(exclude_none=True))
                                persisted = PlannedSession.model_validate(changed)
                                assert compare_blocks(persisted.blocks, expected)
                                assert persisted.date == baseline.date and persisted.distance_estimee_km == baseline.distance_estimee_km
                                assert persisted.echauffement_minutes == 10 and persisted.retour_au_calme_minutes == 5
                                workout = build_running_workout(persisted)
                                assert effective_seconds(workout.workoutSegments[0].workoutSteps) == seconds == workout.estimatedDurationInSecs
                                item["persisted"] = changed
                                item["workout"] = workout.model_dump(mode="json")
                                # The same blocks must survive the canonical V2 boundary and acceptance.
                                v2 = PlanSessionV2(date=date.fromisoformat(persisted.date), sport="running", session_type="intervalles",
                                    objective=persisted.objectif, duration_minutes=persisted.duree_estimee_minutes,
                                    intensity_instruction=persisted.consigne_intensite, explanation=persisted.raison,
                                    structure=dict(warmup_minutes=10, cooldown_minutes=5, main_set="Deux blocs distincts.", blocks=persisted.blocks))
                                proposal = PlanProposalV2(period=PlanPeriodV2(start=date(2099, 10, 12), end=date(2099, 10, 18)),
                                    summary="Semaine synthétique.", strategy="Blocs explicitement prescrits.", sessions=[v2])
                                current = PlanProposalStateV2(status="available", input_fingerprint="a" * 64, proposal=proposal)
                                assert garmin_delivery.build_plan_session_workout(v2).estimatedDurationInSecs == seconds
                                item["delivery"] = exercise_acceptance(user, current)
                        item["functional_success"] = True
                    except Exception as error:
                        item["error_type"] = type(error).__name__
                        # Avoid exception text, which can contain provider credentials or payloads.
                    usage = root / "users" / user / "ai_usage.json"
                    item["usage"] = json.loads(usage.read_text(encoding="utf-8")) if usage.exists() else []
                    save()
                    print(name, "technical", item["technical_success"], "functional", item["functional_success"], flush=True)
    save()
    print(json.dumps({key: value for key, value in report.items() if key != "profiles"}))
    if any(not item["functional_success"] for item in report["profiles"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
