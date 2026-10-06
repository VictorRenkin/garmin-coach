from __future__ import annotations

import http.client
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from garmin_coach import (
    ai_provider,
    config,
    dashboard,
    history_import,
    history_storage,
    runtime_context,
    user_accounts,
)
from garmin_coach.availability import build_availability_v1
from garmin_coach.config import Settings
from garmin_coach.goal import GoalV1, build_goal_v1
from garmin_coach.goal_assessment import (
    GoalAssessmentStateV1,
    GoalAssessmentV1,
    SupportingFact as AssessmentSupportingFact,
    build_goal_assessment_input_v1,
    generate_goal_assessment,
    goal_assessment_fingerprint,
)
from garmin_coach.planning_decision import (
    PLANNING_RECOMMENDATION_PROMPT_V1,
    PROMPT_VERSION,
    PlanningDecisionError,
    PlanningRecommendationInputV1,
    PlanningRecommendationStateV1,
    PlanningRecommendationV1,
    SupportingFact,
    build_plan_constraints_v1,
    build_planning_recommendation_input_v1,
    confirm_planning_decision,
    decision_context_fingerprint,
    generate_planning_recommendation,
    get_planning_decision_state,
    get_planning_recommendation_state,
    planning_recommendation_fingerprint,
    validate_planning_recommendation,
)
from garmin_coach.subjective_context import build_subjective_context_v1


REFERENCE = date(2026, 9, 30)
RACE_DATE = date(2027, 4, 18)


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    monkeypatch.setattr(user_accounts, "SESSION_KEY_FILE", tmp_path / "session.key")
    return tmp_path


def settings(*, key: str | None = "test-key") -> Settings:
    return Settings(None, None, 300, key, "gpt-5.6-sol", "openai", None)


def race_goal(*, target_time: str = "01:45:00") -> GoalV1:
    return build_goal_v1(
        "race",
        event_type="half_marathon",
        race_date=RACE_DATE,
        target_time=target_time,
    )


def prepare_runtime(
    user_id: str,
    goal: GoalV1,
    *,
    available_days: list[str] | None = None,
    preferred: int | None = 3,
    activity_count: int = 12,
    pain: bool | None = False,
    pain_description: str | None = None,
    unrecorded: list[dict[str, Any]] | None = None,
) -> runtime_context.RuntimeMotionestContextV1:
    days = available_days or ["tue", "thu", "sat"]
    runtime_context.save_motionest_inputs_v1(
        user_id,
        goal=goal,
        availability=build_availability_v1(
            days,
            preferred_sessions_per_week=preferred,
            long_session_preferred_days=[days[-1]],
        ),
        subjective_context=build_subjective_context_v1(
            affecting_training=pain,
            pain_description=(pain_description or "Private pain description") if pain else None,
            important_preferences=["Private free-form preference"],
            unrecorded_activities=unrecorded or [],
        ),
    )
    with config.user_context(user_id):
        history_import.initialize_history_import("all", reference_date=REFERENCE)
        for index in range(activity_count):
            history_storage.save_activity_metadata(
                {
                    "activityId": f"{user_id}-{index}",
                    "activityName": f"Private location {index}",
                    "activityType": {"typeKey": "running"},
                    "eventType": {"typeKey": "uncategorized"},
                    "startTimeLocal": (
                        REFERENCE - timedelta(days=index * 5)
                    ).isoformat()
                    + "T08:00:00",
                    "distance": 6_000 + index * 500,
                    "movingDuration": 2_100 + index * 100,
                    "averageHR": 145,
                    "hrTimeInZone_2": 1_200,
                    "aerobicTrainingEffect": 2.8,
                    "anaerobicTrainingEffect": 0.2,
                }
            )
    return runtime_context.build_runtime_motionest_context(
        user_id, reference_date=REFERENCE
    )


def assessment_state(
    runtime: runtime_context.RuntimeMotionestContextV1,
    *,
    status: str = "available",
    assessment: str = "ambitious",
    confidence: str = "medium",
) -> GoalAssessmentStateV1:
    source = build_goal_assessment_input_v1(runtime)
    current_fingerprint = goal_assessment_fingerprint(source)
    if status != "available":
        return GoalAssessmentStateV1(
            status=status,  # type: ignore[arg-type]
            input_fingerprint=current_fingerprint,
        )
    return GoalAssessmentStateV1(
        status="available",
        input_fingerprint=current_fingerprint,
        assessment=GoalAssessmentV1(
            assessment_scope=source.assessment_scope,
            assessment=assessment,  # type: ignore[arg-type]
            confidence=confidence,  # type: ignore[arg-type]
            summary="Texte IA précédent qui ne doit pas être propagé.",
            explanation="Explication IA précédente qui ne doit pas être propagée.",
            supporting_facts=[
                AssessmentSupportingFact(
                    path="goal_evidence.training_support.running_distance_last_84_days_km",
                    role="contextual",
                )
            ],
            uncertainties=["Texte libre précédent."],
            caveats=["Réserve précédente."],
        ),
    )


def recommendation_for(
    source: PlanningRecommendationInputV1,
    *,
    sessions: int | None = 3,
    status: str = "recommended",
    confidence: str = "medium",
    path: str = "availability.available_day_count",
) -> PlanningRecommendationV1:
    return PlanningRecommendationV1(
        status=status,  # type: ignore[arg-type]
        recommended_sessions_per_week=sessions,
        confidence=confidence,  # type: ignore[arg-type]
        summary="Fréquence hebdomadaire proposée.",
        explanation="La recommandation distingue les faits utilisateur des principes généraux.",
        supporting_facts=[SupportingFact(path=path, role="contextual")],
        uncertainties=["La réponse individuelle à l'entraînement reste inconnue."],
        caveats=["Cette fréquence ne constitue pas un calendrier."],
    )


@pytest.mark.parametrize(
    ("days", "preferred", "recommended"),
    [(["wed"], None, 1), (list(config_day for config_day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")), 5, 7)],
)
def test_one_to_seven_available_days_and_optional_preference(
    days: list[str], preferred: int | None, recommended: int
) -> None:
    runtime = prepare_runtime(
        f"availability_{len(days)}",
        build_goal_v1("progress"),
        available_days=days,
        preferred=preferred,
        activity_count=2,
    )
    source = build_planning_recommendation_input_v1(
        f"availability_{len(days)}", runtime
    )
    value = recommendation_for(source, sessions=recommended)

    assert source.facts["availability.available_day_count"] == len(days)
    assert source.facts["availability.preferred_sessions_per_week"] == preferred
    assert validate_planning_recommendation(value, source) == value


@pytest.mark.parametrize("intent", ["beginner", "comeback", "progress"])
def test_non_race_intents_allow_ai_recommendation_without_goal_assessment(
    intent: str,
) -> None:
    user_id = f"intent_{intent}"
    runtime = prepare_runtime(
        user_id, build_goal_v1(intent), activity_count=0, preferred=None
    )
    source = build_planning_recommendation_input_v1(user_id, runtime)

    assert source.facts["goal.intent"] == intent
    assert source.facts["goal_assessment.assessment_scope"] == "not_applicable"
    assert source.facts["goal_assessment.assessment"] == "not_applicable"
    assert source.facts["current_training.running_sessions_84d"] == 0


def test_observed_frequency_and_preference_are_facts_not_local_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    low_runtime = prepare_runtime(
        "observed_low", build_goal_v1("progress"), activity_count=1, preferred=1
    )
    rich_runtime = prepare_runtime(
        "observed_rich", build_goal_v1("progress"), activity_count=12, preferred=3
    )
    low_source = build_planning_recommendation_input_v1("observed_low", low_runtime)
    rich_source = build_planning_recommendation_input_v1("observed_rich", rich_runtime)
    calls: list[dict[str, Any]] = []

    def mocked(*args: Any, **kwargs: Any) -> PlanningRecommendationV1:
        calls.append(kwargs)
        source = low_source if len(calls) == 1 else rich_source
        return recommendation_for(source, sessions=2)

    monkeypatch.setattr(ai_provider, "structured_completion", mocked)
    low = generate_planning_recommendation(
        "observed_low", runtime=low_runtime, settings=settings()
    )
    rich = generate_planning_recommendation(
        "observed_rich", runtime=rich_runtime, settings=settings()
    )

    assert low.recommendation is not None and rich.recommendation is not None
    assert low.recommendation.recommended_sessions_per_week == 2
    assert rich.recommendation.recommended_sessions_per_week == 2
    assert calls[0]["quality"] == "balanced"
    assert calls[0]["reasoning_effort"] == "medium"
    assert calls[0]["instructions"] == PLANNING_RECOMMENDATION_PROMPT_V1
    assert calls[0]["payload"]["facts"]["availability.preferred_sessions_per_week"] == 1
    assert calls[1]["payload"]["facts"]["availability.preferred_sessions_per_week"] == 3
    assert calls[0]["payload"]["facts"]["current_training.running_sessions_84d"] != calls[1]["payload"]["facts"]["current_training.running_sessions_84d"]


def test_race_requires_current_goal_assessment_and_passes_only_structured_fields() -> None:
    runtime = prepare_runtime("race_dependency", race_goal())
    with pytest.raises(PlanningDecisionError) as missing:
        build_planning_recommendation_input_v1(
            "race_dependency", runtime, goal_assessment_state=assessment_state(runtime, status="missing")
        )
    assert missing.value.code == "goal_assessment_required"

    with pytest.raises(PlanningDecisionError) as stale:
        build_planning_recommendation_input_v1(
            "race_dependency", runtime, goal_assessment_state=assessment_state(runtime, status="stale")
        )
    assert stale.value.code == "goal_assessment_stale"

    manually_stale = assessment_state(runtime).model_copy(
        update={"input_fingerprint": "f" * 64}
    )
    with pytest.raises(PlanningDecisionError) as stale_injected:
        build_planning_recommendation_input_v1(
            "race_dependency",
            runtime,
            goal_assessment_state=manually_stale,
        )
    assert stale_injected.value.code == "goal_assessment_stale"

    current_fingerprint = goal_assessment_fingerprint(
        build_goal_assessment_input_v1(runtime)
    )
    current_without_assessment = GoalAssessmentStateV1(
        status="available",
        input_fingerprint=current_fingerprint,
        assessment=None,
    )
    with pytest.raises(PlanningDecisionError) as absent_assessment:
        build_planning_recommendation_input_v1(
            "race_dependency",
            runtime,
            goal_assessment_state=current_without_assessment,
        )
    assert absent_assessment.value.code == "goal_assessment_required"

    for verdict in ("ambitious", "very_ambitious"):
        source = build_planning_recommendation_input_v1(
            "race_dependency",
            runtime,
            goal_assessment_state=assessment_state(
                runtime, assessment=verdict, confidence="low"
            ),
        )
        serialized = json.dumps(source.model_dump(mode="json"), ensure_ascii=False)
        assert source.facts["goal_assessment.assessment"] == verdict
        assert source.facts["goal_assessment.confidence"] == "low"
        assert source.facts["goal_assessment.supporting_fact_paths"]
        assert "Texte IA précédent" not in serialized
        assert "Explication IA précédente" not in serialized
        assert "Texte libre précédent" not in serialized


def test_pain_description_is_sent_to_coach_but_other_private_fields_stay_excluded() -> None:
    runtime = prepare_runtime(
        "private_planning",
        build_goal_v1("comeback"),
        pain=True,
        unrecorded=[
            {
                "sport": "strength_training",
                "sessions_per_week": 2,
                "typical_duration_minutes": 45,
            }
        ],
    )
    source = build_planning_recommendation_input_v1("private_planning", runtime)
    serialized = json.dumps(source.model_dump(mode="json"), ensure_ascii=False).lower()

    assert source.facts["safety.pain_status"] == "explicit_yes"
    assert source.facts["safety.pain_description_present"] is True
    assert source.facts["safety.user_reported_pain_or_injury"] == "Private pain description"
    assert source.facts["unrecorded_activities.count"] == 1
    assert source.facts["unrecorded_activities.total_reported_sessions_per_week"] == 2
    assert source.facts["unrecorded_activities.total_typical_duration_minutes_per_week"] == 90
    assert source.facts["unrecorded_activities.provenance"] == "user_reported_unrecorded"
    for forbidden in (
        "user_id",
        "garmin",
        "activityid",
        "activityname",
        "latitude",
        "longitude",
        "email",
        "token",
        "secret",
        "api key",
        "strength_training",
    ):
        assert forbidden not in serialized
    assert "private free-form preference" not in serialized


def test_pain_description_changes_decision_context_fingerprint() -> None:
    first = prepare_runtime(
        "pain_fingerprint_first",
        build_goal_v1("comeback"),
        pain=True,
        pain_description="Gêne légère au genou après la course.",
    )
    second = prepare_runtime(
        "pain_fingerprint_second",
        build_goal_v1("comeback"),
        pain=True,
        pain_description="Interdiction médicale temporaire de courir.",
    )

    assert decision_context_fingerprint(first) != decision_context_fingerprint(second)


def test_missing_goal_or_availability_returns_explicit_dependency_error() -> None:
    subjective = build_subjective_context_v1(affecting_training=False)
    runtime_context.save_motionest_inputs_v1(
        "missing_goal",
        goal=None,
        availability=build_availability_v1(["tue"]),
        subjective_context=subjective,
    )
    missing_goal_runtime = runtime_context.build_runtime_motionest_context(
        "missing_goal", reference_date=REFERENCE
    )
    with pytest.raises(PlanningDecisionError) as goal_error:
        build_planning_recommendation_input_v1(
            "missing_goal", missing_goal_runtime
        )
    assert goal_error.value.code == "goal_unavailable"

    runtime_context.save_motionest_inputs_v1(
        "missing_availability",
        goal=build_goal_v1("progress"),
        availability=None,
        subjective_context=subjective,
    )
    missing_availability_runtime = runtime_context.build_runtime_motionest_context(
        "missing_availability", reference_date=REFERENCE
    )
    with pytest.raises(PlanningDecisionError) as availability_error:
        build_planning_recommendation_input_v1(
            "missing_availability", missing_availability_runtime
        )
    assert availability_error.value.code == "availability_unavailable"


def test_recommendation_validation_enforces_only_hard_constraints() -> None:
    runtime = prepare_runtime(
        "hard_constraints",
        build_goal_v1("progress"),
        available_days=["tue", "thu", "sat"],
        preferred=1,
    )
    source = build_planning_recommendation_input_v1("hard_constraints", runtime)
    different_from_preference = recommendation_for(source, sessions=3)
    assert validate_planning_recommendation(different_from_preference, source) == different_from_preference

    with pytest.raises(PlanningDecisionError) as outside:
        validate_planning_recommendation(
            recommendation_for(source, sessions=4), source
        )
    assert outside.value.code == "recommendation_outside_availability"

    with pytest.raises(ValidationError):
        recommendation_for(source, sessions=0)

    unknown = recommendation_for(source, path="user.imaginary_fact")
    with pytest.raises(PlanningDecisionError) as grounding:
        validate_planning_recommendation(unknown, source)
    assert grounding.value.code == "unknown_supporting_fact"


def test_insufficient_context_is_explicit_and_never_contains_frequency() -> None:
    runtime = prepare_runtime("insufficient", build_goal_v1("beginner"), activity_count=0)
    source = build_planning_recommendation_input_v1("insufficient", runtime)
    value = recommendation_for(
        source, sessions=None, status="insufficient_context", confidence="low"
    )
    assert validate_planning_recommendation(value, source).recommended_sessions_per_week is None
    with pytest.raises(ValidationError):
        recommendation_for(source, sessions=2, status="insufficient_context")

    explicit_contraindication = recommendation_for(
        source, sessions=None, status="insufficient_context", confidence="high"
    )
    assert validate_planning_recommendation(
        explicit_contraindication, source
    ).confidence == "high"


def test_insufficient_context_cannot_be_confirmed_or_create_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "no_decision_without_recommendation"
    runtime = prepare_runtime(user_id, build_goal_v1("beginner"), activity_count=0)
    source = build_planning_recommendation_input_v1(user_id, runtime)
    provider_calls = 0

    def mocked(*args: Any, **kwargs: Any) -> PlanningRecommendationV1:
        nonlocal provider_calls
        provider_calls += 1
        return recommendation_for(
            source,
            sessions=None,
            status="insufficient_context",
            confidence="low",
        )

    monkeypatch.setattr(ai_provider, "structured_completion", mocked)
    recommendation = generate_planning_recommendation(
        user_id, runtime=runtime, settings=settings()
    )
    assert recommendation.recommendation is not None
    assert recommendation.recommendation.status == "insufficient_context"

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("La confirmation ne doit appeler aucune IA.")

    monkeypatch.setattr(ai_provider, "structured_completion", forbidden)
    with pytest.raises(PlanningDecisionError) as error:
        confirm_planning_decision(
            user_id,
            confirmed_sessions_per_week=3,
            recommendation_fingerprint=recommendation.input_fingerprint,
            runtime=runtime,
        )
    assert error.value.code == "recommendation_insufficient_context"
    assert provider_calls == 1
    with config.user_context(user_id):
        assert not config.PLANNING_DECISION_FILE.exists()


def test_missing_ai_key_never_creates_deterministic_frequency_fallback() -> None:
    user_id = "planning_missing_key"
    runtime = prepare_runtime(user_id, build_goal_v1("progress"))
    with pytest.raises(PlanningDecisionError) as error:
        generate_planning_recommendation(
            user_id, runtime=runtime, settings=settings(key=None)
        )
    assert error.value.code == "ai_not_configured"
    assert get_planning_recommendation_state(
        user_id, runtime=runtime
    ).status == "missing"


@pytest.mark.parametrize(
    ("case", "error", "code"),
    [
        ("validation", ValidationError.from_exception_data("invalid", []), "invalid_provider_response"),
        ("json", json.JSONDecodeError("invalid", "{", 1), "invalid_provider_response"),
        ("timeout", TimeoutError("timeout"), "provider_timeout"),
        ("quota", RuntimeError("insufficient_quota"), "provider_quota_exhausted"),
        ("rate", RuntimeError("status 429 rate limit"), "provider_rate_limited"),
        ("down", RuntimeError("provider down"), "provider_unavailable"),
    ],
)
def test_provider_failures_are_explicit(
    case: str, error: Exception, code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = f"provider_{case}"
    runtime = prepare_runtime(user_id, build_goal_v1("progress"))
    monkeypatch.setattr(
        ai_provider,
        "structured_completion",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )
    with pytest.raises(PlanningDecisionError) as result:
        generate_planning_recommendation(user_id, runtime=runtime, settings=settings())
    assert result.value.code == code


def test_cache_prompt_digest_atomicity_and_fingerprint_invalidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "recommendation_cache"
    runtime = prepare_runtime(user_id, build_goal_v1("progress"))
    source = build_planning_recommendation_input_v1(user_id, runtime)
    calls = 0

    def mocked(*args: Any, **kwargs: Any) -> PlanningRecommendationV1:
        nonlocal calls
        calls += 1
        return recommendation_for(source)

    monkeypatch.setattr(ai_provider, "structured_completion", mocked)
    first = generate_planning_recommendation(user_id, runtime=runtime, settings=settings())
    second = generate_planning_recommendation(user_id, runtime=runtime, settings=settings())

    assert calls == 1
    assert first.cache_hit is False and second.cache_hit is True
    baseline = planning_recommendation_fingerprint(source)
    assert planning_recommendation_fingerprint(source, prompt_version=PROMPT_VERSION + 1) != baseline
    assert planning_recommendation_fingerprint(
        source, system_prompt=PLANNING_RECOMMENDATION_PROMPT_V1 + "\nRévision."
    ) != baseline
    with config.user_context(user_id):
        persisted = json.loads(config.PLANNING_RECOMMENDATION_FILE.read_text(encoding="utf-8"))
        assert "facts" not in persisted
        assert len(persisted["prompt_digest"]) == 64
        assert PLANNING_RECOMMENDATION_PROMPT_V1 not in json.dumps(persisted, ensure_ascii=False)
        assert not list(config.PLANNING_RECOMMENDATION_FILE.parent.glob("*.tmp"))


def test_same_user_concurrency_calls_provider_once(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = "same_user_concurrency"
    runtime = prepare_runtime(user_id, build_goal_v1("progress"))
    source = build_planning_recommendation_input_v1(user_id, runtime)
    calls = 0

    def mocked(*args: Any, **kwargs: Any) -> PlanningRecommendationV1:
        nonlocal calls
        calls += 1
        time.sleep(0.02)
        return recommendation_for(source)

    monkeypatch.setattr(ai_provider, "structured_completion", mocked)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: generate_planning_recommendation(
                    user_id, runtime=runtime, settings=settings()
                ),
                range(2),
            )
        )
    assert calls == 1
    assert sorted(item.cache_hit for item in results) == [False, True]


def test_two_users_generate_in_parallel_with_isolated_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alpha = prepare_runtime("parallel_plan_alpha", build_goal_v1("progress"))
    beta = prepare_runtime("parallel_plan_beta", build_goal_v1("comeback"))
    response = recommendation_for(
        build_planning_recommendation_input_v1("parallel_plan_alpha", alpha)
    )
    barrier = threading.Barrier(2)
    guard = threading.Lock()
    active = 0
    peak = 0

    def mocked(*args: Any, **kwargs: Any) -> PlanningRecommendationV1:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(timeout=2)
            return response
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(ai_provider, "structured_completion", mocked)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            generate_planning_recommendation,
            "parallel_plan_alpha",
            runtime=alpha,
            settings=settings(),
        )
        second = executor.submit(
            generate_planning_recommendation,
            "parallel_plan_beta",
            runtime=beta,
            settings=settings(),
        )
        assert first.result(timeout=5).status == "available"
        assert second.result(timeout=5).status == "available"

    assert peak == 2
    with config.user_context("parallel_plan_alpha"):
        alpha_store = Path(config.PLANNING_RECOMMENDATION_FILE)
    with config.user_context("parallel_plan_beta"):
        beta_store = Path(config.PLANNING_RECOMMENDATION_FILE)
    assert alpha_store != beta_store
    assert alpha_store.is_file() and beta_store.is_file()


def _persist_recommendation(
    user_id: str,
    runtime: runtime_context.RuntimeMotionestContextV1,
    monkeypatch: pytest.MonkeyPatch,
    *,
    sessions: int = 3,
) -> PlanningRecommendationStateV1:
    source = build_planning_recommendation_input_v1(user_id, runtime)
    monkeypatch.setattr(
        ai_provider,
        "structured_completion",
        lambda *args, **kwargs: recommendation_for(source, sessions=sessions),
    )
    return generate_planning_recommendation(user_id, runtime=runtime, settings=settings())


def test_accept_recommendation_and_user_overrides_build_constraints_from_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for confirmed, expected_source in (
        (3, "accepted_recommendation"),
        (2, "user_override"),
        (4, "user_override"),
    ):
        user_id = f"decision_{confirmed}"
        runtime = prepare_runtime(
            user_id,
            build_goal_v1("progress"),
            available_days=["mon", "tue", "thu", "sat"],
            preferred=2,
        )
        recommendation = _persist_recommendation(
            user_id, runtime, monkeypatch, sessions=3
        )
        state = confirm_planning_decision(
            user_id,
            confirmed_sessions_per_week=confirmed,
            recommendation_fingerprint=recommendation.input_fingerprint,
            runtime=runtime,
        )
        assert state.decision is not None and state.plan_constraints is not None
        assert state.decision.decision_source == expected_source
        assert state.plan_constraints.confirmed_sessions_per_week == confirmed
        assert "recommended_sessions_per_week" not in state.plan_constraints.model_dump()
        if confirmed != 3:
            assert recommendation.recommendation is not None
            assert state.plan_constraints.confirmed_sessions_per_week != recommendation.recommendation.recommended_sessions_per_week


def test_decision_rejects_outside_availability_and_stale_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "decision_validation"
    runtime = prepare_runtime(user_id, build_goal_v1("progress"))
    recommendation = _persist_recommendation(user_id, runtime, monkeypatch)
    with pytest.raises(PlanningDecisionError) as outside:
        confirm_planning_decision(
            user_id,
            confirmed_sessions_per_week=4,
            recommendation_fingerprint=recommendation.input_fingerprint,
            runtime=runtime,
        )
    assert outside.value.code == "decision_invalid"
    with pytest.raises(PlanningDecisionError) as stale:
        confirm_planning_decision(
            user_id,
            confirmed_sessions_per_week=3,
            recommendation_fingerprint="f" * 64,
            runtime=runtime,
        )
    assert stale.value.code == "recommendation_stale"


def test_decision_staleness_tracks_declarations_but_not_new_garmin_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "decision_context"
    goal = build_goal_v1("progress")
    runtime = prepare_runtime(user_id, goal)
    recommendation = _persist_recommendation(user_id, runtime, monkeypatch)
    confirm_planning_decision(
        user_id,
        confirmed_sessions_per_week=3,
        recommendation_fingerprint=recommendation.input_fingerprint,
        runtime=runtime,
    )
    original_decision_fingerprint = decision_context_fingerprint(runtime)

    with config.user_context(user_id):
        history_storage.save_activity_metadata(
            {
                "activityId": "new-garmin-history-only",
                "activityType": {"typeKey": "running"},
                "eventType": {"typeKey": "uncategorized"},
                "startTimeLocal": REFERENCE.isoformat() + "T18:00:00",
                "distance": 5_000,
                "movingDuration": 1_800,
            }
        )
    history_changed = runtime_context.build_runtime_motionest_context(
        user_id, reference_date=REFERENCE
    )
    assert decision_context_fingerprint(history_changed) == original_decision_fingerprint
    assert get_planning_decision_state(
        user_id, runtime=history_changed
    ).status == "available"
    assert get_planning_recommendation_state(
        user_id, runtime=history_changed
    ).status == "stale"

    runtime_context.save_motionest_inputs_v1(
        user_id,
        goal=build_goal_v1("comeback"),
        availability=history_changed.context.availability,
        subjective_context=history_changed.context.subjective_context,
    )
    goal_changed = runtime_context.build_runtime_motionest_context(
        user_id, reference_date=REFERENCE
    )
    assert get_planning_decision_state(user_id, runtime=goal_changed).status == "stale"


def test_availability_and_declared_safety_changes_make_decision_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for change in ("availability", "safety"):
        user_id = f"decision_change_{change}"
        goal = build_goal_v1("progress")
        runtime = prepare_runtime(user_id, goal)
        recommendation = _persist_recommendation(user_id, runtime, monkeypatch)
        confirm_planning_decision(
            user_id,
            confirmed_sessions_per_week=3,
            recommendation_fingerprint=recommendation.input_fingerprint,
            runtime=runtime,
        )
        runtime_context.save_motionest_inputs_v1(
            user_id,
            goal=goal,
            availability=(
                build_availability_v1(["tue", "sat"], preferred_sessions_per_week=2)
                if change == "availability"
                else runtime.context.availability
            ),
            subjective_context=(
                build_subjective_context_v1(affecting_training=True, pain_description="Private")
                if change == "safety"
                else runtime.context.subjective_context
            ),
        )
        changed = runtime_context.build_runtime_motionest_context(
            user_id, reference_date=REFERENCE
        )
        state = get_planning_decision_state(user_id, runtime=changed)
        assert state.status == "stale"
        assert state.plan_constraints is None
        original_state = get_planning_decision_state(user_id, runtime=runtime)
        assert original_state.decision is not None
        with pytest.raises(PlanningDecisionError) as stale_constraints:
            build_plan_constraints_v1(original_state.decision, changed)
        assert stale_constraints.value.code == "decision_stale"


def test_decision_store_is_atomic_and_isolated_per_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths: list[Path] = []
    for user_id in ("decision_alpha", "decision_beta"):
        runtime = prepare_runtime(user_id, build_goal_v1("progress"))
        recommendation = _persist_recommendation(user_id, runtime, monkeypatch)
        confirm_planning_decision(
            user_id,
            confirmed_sessions_per_week=3,
            recommendation_fingerprint=recommendation.input_fingerprint,
            runtime=runtime,
        )
        with config.user_context(user_id):
            paths.append(Path(config.PLANNING_DECISION_FILE))
            assert not list(config.PLANNING_DECISION_FILE.parent.glob("*.tmp"))
    assert paths[0] != paths[1]
    assert all(path.is_file() for path in paths)


def _serve() -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _request(
    server: ThreadingHTTPServer,
    method: str,
    path: str,
    cookie: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Cookie": cookie}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    response_payload = json.loads(response.read())
    connection.close()
    return response.status, response_payload


def test_get_recommendation_never_calls_ai_and_post_decision_never_calls_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "planning_endpoints"
    runtime = prepare_runtime(user_id, build_goal_v1("progress"))
    source = build_planning_recommendation_input_v1(user_id, runtime)
    calls = 0

    def recommendation_mock(*args: Any, **kwargs: Any) -> PlanningRecommendationV1:
        nonlocal calls
        calls += 1
        return recommendation_for(source)

    monkeypatch.setattr(ai_provider, "structured_completion", recommendation_mock)
    monkeypatch.setattr(
        dashboard, "generate_planning_recommendation", generate_planning_recommendation
    )
    monkeypatch.setattr(
        dashboard, "get_planning_recommendation_state", get_planning_recommendation_state
    )
    monkeypatch.setattr(dashboard, "load_settings", lambda: settings())
    monkeypatch.setattr(
        __import__("garmin_coach.planning_decision", fromlist=["load_settings"]),
        "load_settings",
        lambda: settings(),
    )
    cookie = f"motionest_session={user_accounts.create_session(user_id)}"
    server, thread = _serve()
    try:
        get_status, get_payload = _request(
            server, "GET", "/api/planning-recommendation", cookie
        )
        post_status, post_payload = _request(
            server, "POST", "/api/planning-recommendation", cookie, {}
        )
        assert calls == 1

        def forbidden(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("PlanningDecision must not call AI")

        monkeypatch.setattr(ai_provider, "structured_completion", forbidden)
        decision_status, decision_payload = _request(
            server,
            "POST",
            "/api/planning-decision",
            cookie,
            {
                "confirmedSessionsPerWeek": 3,
                "recommendationFingerprint": post_payload["input_fingerprint"],
            },
        )
        read_status, read_payload = _request(
            server, "GET", "/api/planning-decision", cookie
        )
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()

    assert get_status == 200 and get_payload["status"] == "missing"
    assert post_status == 200 and post_payload["status"] == "available"
    assert decision_status == 200 and decision_payload["status"] == "available"
    assert read_status == 200 and read_payload["status"] == "available"
    assert calls == 1


def test_decision_endpoint_rejects_insufficient_context_with_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "insufficient_endpoint"
    runtime = prepare_runtime(user_id, build_goal_v1("beginner"), activity_count=0)
    source = build_planning_recommendation_input_v1(user_id, runtime)
    monkeypatch.setattr(
        ai_provider,
        "structured_completion",
        lambda *args, **kwargs: recommendation_for(
            source,
            sessions=None,
            status="insufficient_context",
            confidence="low",
        ),
    )
    recommendation = generate_planning_recommendation(
        user_id, runtime=runtime, settings=settings()
    )

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("POST planning-decision ne doit appeler aucune IA.")

    monkeypatch.setattr(ai_provider, "structured_completion", forbidden)
    cookie = f"motionest_session={user_accounts.create_session(user_id)}"
    server, thread = _serve()
    try:
        status, payload = _request(
            server,
            "POST",
            "/api/planning-decision",
            cookie,
            {
                "confirmedSessionsPerWeek": 3,
                "recommendationFingerprint": recommendation.input_fingerprint,
            },
        )
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()

    assert status == 409
    assert payload["code"] == "recommendation_insufficient_context"
    with config.user_context(user_id):
        assert not config.PLANNING_DECISION_FILE.exists()


def test_payload_is_compact_and_local_processing_is_fast() -> None:
    runtime = prepare_runtime("planning_performance", build_goal_v1("progress"))
    started = time.perf_counter()
    source = build_planning_recommendation_input_v1("planning_performance", runtime)
    fingerprint = planning_recommendation_fingerprint(source)
    elapsed = time.perf_counter() - started
    encoded = json.dumps(source.model_dump(mode="json"), ensure_ascii=False).encode()

    assert len(fingerprint) == 64
    assert len(encoded) < 15_000
    assert elapsed < 0.1
