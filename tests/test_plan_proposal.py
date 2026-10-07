from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from garmin_coach import config, plan_proposal as module, runtime_context, user_accounts
from garmin_coach.availability import build_availability_v1
from garmin_coach.config import Settings
from garmin_coach.goal import build_goal_v1
from garmin_coach.plan_proposal import (
    PlanPeriodV2,
    PlanProposalError,
    PlanProposalV2,
    PlanSessionStructureV2,
    PlanSessionV2,
    build_plan_generation_input_v2,
    generate_plan_proposal,
    get_plan_proposal_state,
    plan_generation_fingerprint,
    validate_plan_proposal,
)
from garmin_coach.planning_decision import (
    PlanningDecisionStateV1,
    PlanningDecisionV1,
    PlanningRecommendationStateV1,
    PlanningRecommendationV1,
    SupportingFact,
    build_plan_constraints_v1,
    decision_context_fingerprint,
)
from garmin_coach.subjective_context import build_subjective_context_v1


REFERENCE = date(2026, 10, 7)


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    monkeypatch.setattr(user_accounts, "SESSION_KEY_FILE", tmp_path / "session.key")


def settings() -> Settings:
    return Settings(None, None, 300, "test-key", "gpt-5.6-sol", "openai", None)


def runtime_for(
    user_id: str,
    *,
    intent: str = "progress",
    preferred: int | None = 4,
    pain: bool | None = False,
) -> runtime_context.RuntimeMotionestContextV1:
    runtime_context.save_motionest_inputs_v1(
        user_id,
        goal=build_goal_v1(intent),  # type: ignore[arg-type]
        availability=build_availability_v1(
            ["mon", "tue", "thu", "sat"],
            preferred_sessions_per_week=preferred,
            long_session_preferred_days=["sat"],
        ),
        subjective_context=build_subjective_context_v1(
            affecting_training=pain,
            pain_description="Gêne légère déclarée." if pain else None,
            important_preferences=["Préférer une séance courte le mardi."],
        ),
    )
    return runtime_context.build_runtime_motionest_context(user_id, reference_date=REFERENCE)


def states(runtime, *, confirmed: int = 2, fingerprint: str = "a" * 64):
    recommendation = PlanningRecommendationStateV1(
        status="available",
        input_fingerprint=fingerprint,
        recommendation=PlanningRecommendationV1(
            status="recommended",
            recommended_sessions_per_week=3,
            confidence="medium",
            summary="Fréquence proposée.",
            explanation="Fréquence fondée sur le contexte compact.",
            supporting_facts=[SupportingFact(path="availability.available_day_count", role="contextual")],
        ),
    )
    decision = PlanningDecisionV1(
        confirmed_sessions_per_week=confirmed,
        decision_source="user_override",
        recommendation_fingerprint=fingerprint,
        decision_context_fingerprint=decision_context_fingerprint(runtime),
        confirmed_at=datetime.now().astimezone().isoformat(),
    )
    decision_state = PlanningDecisionStateV1(
        status="available",
        decision_context_fingerprint=decision.decision_context_fingerprint,
        decision=decision,
        plan_constraints=build_plan_constraints_v1(decision, runtime),
    )
    return recommendation, decision_state


def proposal_for(source, *, sport: str = "running", dates=None, count=None) -> PlanProposalV2:
    selected = dates or source.allowed_dates[: count or source.plan_constraints.confirmed_sessions_per_week]
    return PlanProposalV2(
        period=source.period,
        summary="Une semaine structurée sans pseudo-précision.",
        strategy="Répartir les séances sur les jours confirmés et ajuster selon les signaux déclarés.",
        sessions=[
            PlanSessionV2(
                date=day,
                sport=sport,  # type: ignore[arg-type]
                session_type="aerobic_session",
                objective="Développer la continuité avec une charge maîtrisée.",
                duration_minutes=40,
                intensity_instruction="Effort confortable, respiration maîtrisée et conversation possible.",
                structure=PlanSessionStructureV2(
                    warmup_minutes=10,
                    main_set="Vingt minutes régulières en aisance respiratoire.",
                    cooldown_minutes=10,
                ),
                explanation="Séance qualitative compatible avec les données disponibles.",
            )
            for day in selected
        ],
    )


def install_ai(monkeypatch, proposal_factory, calls: list[int]) -> None:
    def fake(*args, payload, **kwargs):
        calls.append(1)
        source = module.PlanGenerationInputV2.model_validate(payload)
        return proposal_factory(source)
    monkeypatch.setattr(module.ai_provider, "structured_completion", fake)


def test_generation_requires_planning_decision() -> None:
    runtime = runtime_for("missing_decision")
    recommendation, _ = states(runtime)
    missing = PlanningDecisionStateV1(
        status="missing", decision_context_fingerprint=decision_context_fingerprint(runtime)
    )
    with pytest.raises(PlanProposalError, match="PlanningDecision"):
        build_plan_generation_input_v2(
            "missing_decision", runtime, recommendation_state=recommendation, decision_state=missing
        )


def test_plan_period_is_exactly_seven_calendar_days() -> None:
    start = date(2026, 10, 12)

    assert PlanPeriodV2(start=start, end=date(2026, 10, 18)).end == date(
        2026, 10, 18
    )
    with pytest.raises(ValidationError, match="exactement sept jours"):
        PlanPeriodV2(start=start, end=date(2026, 10, 19))


def test_confirmed_frequency_is_authoritative_not_preference() -> None:
    runtime = runtime_for("confirmed", preferred=4)
    recommendation, decision = states(runtime, confirmed=2)
    source = build_plan_generation_input_v2(
        "confirmed", runtime, recommendation_state=recommendation, decision_state=decision
    )
    assert source.plan_constraints.confirmed_sessions_per_week == 2
    assert len(proposal_for(source).sessions) == 2
    assert runtime.context.availability.preferred_sessions_per_week == 4


def test_unavailable_day_and_invalid_ai_output_are_rejected() -> None:
    runtime = runtime_for("invalid_day")
    recommendation, decision = states(runtime)
    source = build_plan_generation_input_v2(
        "invalid_day", runtime, recommendation_state=recommendation, decision_state=decision
    )
    invalid = proposal_for(source, dates=[source.period.start + module.timedelta(days=2), source.allowed_dates[0]])
    result = validate_plan_proposal(invalid, source)
    assert not result.valid
    assert "unavailable_day" in {error.code for error in result.errors}


def test_medical_no_running_rejects_running_but_mild_discomfort_does_not(monkeypatch) -> None:
    runtime = runtime_for("medical", pain=True)
    recommendation, decision = states(runtime)
    monkeypatch.setattr(module, "decision_memory_context", lambda _: [{
        "category": "constraint", "key": "medical_no_running",
        "text": "Interdiction temporaire de courir déclarée.", "scope": "current"
    }])
    blocked = build_plan_generation_input_v2(
        "medical", runtime, recommendation_state=recommendation, decision_state=decision
    )
    assert "medical_no_running" in {e.code for e in validate_plan_proposal(proposal_for(blocked), blocked).errors}
    monkeypatch.setattr(module, "decision_memory_context", lambda _: [{
        "category": "current_signal", "key": "mild_knee_discomfort",
        "text": "Gêne légère au genou déclarée.", "scope": "current"
    }])
    mild = build_plan_generation_input_v2(
        "medical", runtime, recommendation_state=recommendation, decision_state=decision
    )
    assert validate_plan_proposal(proposal_for(mild), mild).valid


def test_relevant_memory_changes_fingerprint_irrelevant_memory_does_not(monkeypatch) -> None:
    runtime = runtime_for("memory")
    recommendation, decision = states(runtime)
    all_memory: list[dict[str, str]] = []

    def projected(_):
        return [
            item
            for item in all_memory
            if item["category"] in {"constraint", "current_signal", "unrecorded_activity", "preference"}
        ]

    monkeypatch.setattr(module, "decision_memory_context", projected)
    empty = build_plan_generation_input_v2("memory", runtime, recommendation_state=recommendation, decision_state=decision)
    all_memory.append({
        "category": "preference", "key": "short_tuesday", "text": "Séance courte le mardi.", "scope": "persistent"
    })
    relevant = build_plan_generation_input_v2("memory", runtime, recommendation_state=recommendation, decision_state=decision)
    assert plan_generation_fingerprint(empty) != plan_generation_fingerprint(relevant)
    all_memory.append({
        "category": "event", "key": "irrelevant_event", "text": "Événement sans rapport.", "scope": "event"
    })
    after_irrelevant = build_plan_generation_input_v2(
        "memory", runtime, recommendation_state=recommendation, decision_state=decision
    )
    assert plan_generation_fingerprint(relevant) == plan_generation_fingerprint(after_irrelevant)


def test_ai_input_is_compact_and_excludes_identifiers_secrets_and_raw_history(monkeypatch) -> None:
    user_id = "private_athlete_identifier"
    runtime = runtime_for(user_id)
    recommendation, decision = states(runtime)
    monkeypatch.setattr(module, "decision_memory_context", lambda _: [
        {
            "id": "mem_private_identifier",
            "category": "preference",
            "key": "contact_note",
            "text": "victor@example.com",
            "scope": "persistent",
            "updated_at": "2026-10-07T12:00:00+02:00",
        },
        {
            "id": "mem_other_identifier",
            "category": "preference",
            "key": "short_tuesday",
            "text": "Séance courte le mardi.",
            "scope": "persistent",
            "updated_at": "2026-10-07T12:00:00+02:00",
        },
    ])
    source = build_plan_generation_input_v2(
        user_id, runtime, recommendation_state=recommendation, decision_state=decision
    )
    payload = json.dumps(source.model_dump(mode="json"), ensure_ascii=False)
    assert user_id not in payload
    assert "victor@example.com" not in payload
    assert "mem_private_identifier" not in payload
    assert "updated_at" not in payload
    assert "activityId" not in payload
    assert "startTimeLocal" not in payload


def test_get_never_calls_ai_and_valid_cache_avoids_second_call(monkeypatch) -> None:
    runtime = runtime_for("cache")
    recommendation, decision = states(runtime)
    calls: list[int] = []
    install_ai(monkeypatch, proposal_for, calls)
    first = generate_plan_proposal(
        "cache", runtime=runtime, recommendation_state=recommendation,
        decision_state=decision, settings=settings()
    )
    assert first.status == "available" and calls == [1]
    read = get_plan_proposal_state(
        "cache", runtime=runtime, recommendation_state=recommendation, decision_state=decision
    )
    second = generate_plan_proposal(
        "cache", runtime=runtime, recommendation_state=recommendation,
        decision_state=decision, settings=settings()
    )
    assert read.status == "available"
    assert second.cache_hit and calls == [1]


def test_goal_and_decision_changes_make_proposal_stale(monkeypatch) -> None:
    runtime = runtime_for("stale")
    recommendation, decision = states(runtime, confirmed=2)
    calls: list[int] = []
    install_ai(monkeypatch, proposal_for, calls)
    assert generate_plan_proposal("stale", runtime=runtime, recommendation_state=recommendation, decision_state=decision, settings=settings()).status == "available"
    changed_goal = runtime_for("stale", intent="comeback")
    rec2, dec2 = states(changed_goal, confirmed=2, fingerprint="b" * 64)
    assert get_plan_proposal_state("stale", runtime=changed_goal, recommendation_state=rec2, decision_state=dec2).status == "stale"
    rec3, dec3 = states(runtime, confirmed=3, fingerprint="c" * 64)
    assert get_plan_proposal_state("stale", runtime=runtime, recommendation_state=rec3, decision_state=dec3).status == "stale"


def test_invalid_provider_proposal_is_not_exposed(monkeypatch) -> None:
    runtime = runtime_for("provider_invalid")
    recommendation, decision = states(runtime)
    calls: list[int] = []
    install_ai(monkeypatch, lambda source: proposal_for(source, dates=[source.period.start + module.timedelta(days=2), source.allowed_dates[0]]), calls)
    state = generate_plan_proposal(
        "provider_invalid", runtime=runtime, recommendation_state=recommendation,
        decision_state=decision, settings=settings()
    )
    assert state.status == "invalid"
    assert state.proposal is None
    assert "unavailable_day" in {error.code for error in state.validation_errors}


def test_user_isolation_and_no_garmin_or_publication(monkeypatch) -> None:
    calls: list[int] = []
    install_ai(monkeypatch, proposal_for, calls)
    monkeypatch.setattr("garmin_coach.garmin.connect", lambda *a, **k: pytest.fail("Garmin called"))
    monkeypatch.setattr("garmin_coach.garmin_workouts.publish_plan", lambda *a, **k: pytest.fail("publication called"))
    for user_id in ("athlete_a", "athlete_b"):
        runtime = runtime_for(user_id)
        recommendation, decision = states(runtime)
        assert generate_plan_proposal(user_id, runtime=runtime, recommendation_state=recommendation, decision_state=decision, settings=settings()).status == "available"
    assert calls == [1, 1]
    assert (config.USERS_DATA_DIR / "athlete_a" / "plan_proposal_v2.json").is_file()
    assert (config.USERS_DATA_DIR / "athlete_b" / "plan_proposal_v2.json").is_file()
