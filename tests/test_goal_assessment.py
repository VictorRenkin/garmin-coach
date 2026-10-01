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
    goal_assessment as assessment_module,
    runtime_context,
    user_accounts,
)
from garmin_coach.availability import build_availability_v1
from garmin_coach.config import Settings
from garmin_coach.goal import GoalV1, build_goal_v1
from garmin_coach.goal_assessment import (
    PROMPT_VERSION,
    SYSTEM_PROMPT_V1,
    GoalAssessmentError,
    GoalAssessmentInputV1,
    GoalAssessmentV1,
    SupportingFact,
    build_goal_assessment_input_v1,
    generate_goal_assessment,
    get_goal_assessment_state,
    goal_assessment_fingerprint,
    validate_goal_assessment,
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


def race_goal(
    *,
    target_time: str | None = "01:45:00",
    race_date: date | None = RACE_DATE,
    event_type: str = "half_marathon",
    distance_km: float | None = None,
) -> GoalV1:
    return build_goal_v1(
        "race",
        event_type=event_type,  # type: ignore[arg-type]
        distance_km=distance_km,
        race_date=race_date,
        target_time=target_time,
    )


def prepare_runtime(
    user_id: str,
    goal: GoalV1,
    *,
    activity_count: int = 12,
    stale: bool = False,
    pain: bool | None = False,
) -> runtime_context.RuntimeMotionestContextV1:
    runtime_context.save_motionest_inputs_v1(
        user_id,
        goal=goal,
        availability=build_availability_v1(
            ["tue", "thu", "sat"], preferred_sessions_per_week=3
        ),
        subjective_context=build_subjective_context_v1(
            affecting_training=pain,
            pain_description="Private pain description" if pain else None,
            important_preferences=["Private preference"],
        ),
    )
    with config.user_context(user_id):
        history_import.initialize_history_import("all", reference_date=REFERENCE)
        for index in range(activity_count):
            offset = 150 + index * 4 if stale else index * 5
            item: dict[str, Any] = {
                "activityId": f"{user_id}-{index}",
                "activityName": f"Private location {index}",
                "activityType": {"typeKey": "running"},
                "eventType": {"typeKey": "uncategorized"},
                "startTimeLocal": (
                    REFERENCE - timedelta(days=offset)
                ).isoformat()
                + "T08:00:00",
                "distance": 6_000 + index * 500,
                "movingDuration": 2_100 + index * 100,
                "averageHR": 145,
                "hrTimeInZone_2": 1_200,
                "aerobicTrainingEffect": 2.8,
                "anaerobicTrainingEffect": 0.2,
            }
            if index == 0:
                item["fastestSplit_5000"] = 1_250.0
            history_storage.save_activity_metadata(item)
    return runtime_context.build_runtime_motionest_context(
        user_id, reference_date=REFERENCE
    )


def settings(*, key: str | None = "test-key") -> Settings:
    return Settings(
        None,
        None,
        300,
        key,
        "gpt-5.6-sol",
        "openai",
        None,
    )


def assessment_for(
    source: GoalAssessmentInputV1,
    *,
    assessment: str = "ambitious",
    confidence: str = "medium",
    path: str = "goal_evidence.training_support.running_distance_last_84_days_km",
) -> GoalAssessmentV1:
    return GoalAssessmentV1(
        assessment_scope=source.assessment_scope,
        assessment=assessment,  # type: ignore[arg-type]
        confidence=confidence,  # type: ignore[arg-type]
        summary="Évaluation factuelle de l'objectif.",
        explanation="Les faits disponibles permettent une interprétation prudente sans garantir le résultat.",
        supporting_facts=[SupportingFact(path=path, role="contextual")],
        uncertainties=["Les données observées ne garantissent pas la performance future."],
        caveats=["Cette évaluation ne constitue pas une prédiction."],
    )


def test_compact_input_is_grounded_private_and_excludes_free_text() -> None:
    runtime = prepare_runtime("private_input", race_goal(), pain=True)
    before = runtime.model_dump(mode="json")
    source = build_goal_assessment_input_v1(runtime)
    serialized = json.dumps(source.model_dump(mode="json"), ensure_ascii=False).lower()

    assert source.assessment_scope == "timed_race"
    assert source.facts["motionest_context.integration.safety_context"] == {
        "pain_status": "explicit_yes",
        "pain_or_injury_affecting_training": True,
        "pain_description_present": True,
        "unrecorded_activities_count": 0,
    }
    for forbidden in (
        "private",
        "activityid",
        "activityname",
        "latitude",
        "longitude",
        "email",
        "token",
        "secret",
        "api key",
    ):
        assert forbidden not in serialized
    assert runtime.model_dump(mode="json") == before


@pytest.mark.parametrize("intent", ["progress", "beginner", "comeback"])
def test_non_race_is_not_applicable_without_provider_call(
    intent: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = f"non_race_{intent}"
    runtime = prepare_runtime(user_id, build_goal_v1(intent))  # type: ignore[arg-type]

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(ai_provider, "structured_completion", forbidden)
    state = generate_goal_assessment(user_id, runtime=runtime, settings=settings())

    assert state.assessment is not None
    assert state.assessment.assessment_scope == "not_applicable"
    assert state.assessment.assessment == "not_applicable"
    assert state.generation_source == "local_scope_rule"


def test_timed_race_uses_balanced_provider_and_valid_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "timed_race"
    runtime = prepare_runtime(user_id, race_goal())
    source = build_goal_assessment_input_v1(runtime)
    captured: dict[str, Any] = {}

    def mocked(*args: Any, **kwargs: Any) -> GoalAssessmentV1:
        captured.update(kwargs)
        return assessment_for(source, path="goal_evidence.performance_comparison.status")

    monkeypatch.setattr(ai_provider, "structured_completion", mocked)
    state = generate_goal_assessment(user_id, runtime=runtime, settings=settings())

    assert state.assessment is not None
    assert state.assessment.assessment == "ambitious"
    assert captured["quality"] == "balanced"
    assert captured["reasoning_effort"] == "medium"
    assert captured["instructions"] == SYSTEM_PROMPT_V1


def test_weak_and_stale_facts_can_return_insufficient_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "weak_race"
    runtime = prepare_runtime(user_id, race_goal(), activity_count=1, stale=True)
    source = build_goal_assessment_input_v1(runtime)

    monkeypatch.setattr(
        ai_provider,
        "structured_completion",
        lambda *args, **kwargs: assessment_for(
            source,
            assessment="insufficient_evidence",
            confidence="low",
            path="motionest_context.data_confidence.current_state",
        ),
    )
    state = generate_goal_assessment(user_id, runtime=runtime, settings=settings())

    assert state.assessment is not None
    assert state.assessment.assessment == "insufficient_evidence"
    assert "current_state_stale" in source.limitations


def test_race_without_time_uses_event_completion_without_invented_target() -> None:
    runtime = prepare_runtime("completion", race_goal(target_time=None))
    source = build_goal_assessment_input_v1(runtime)

    assert source.assessment_scope == "event_completion"
    assert source.facts["goal_evidence.race.target_time_seconds"] is None
    assert source.facts["goal_evidence.race.target_pace_seconds_per_km"] is None


def test_race_without_date_keeps_timed_scope_and_missing_horizon() -> None:
    runtime = prepare_runtime("no_date", race_goal(race_date=None))
    source = build_goal_assessment_input_v1(runtime)

    assert source.assessment_scope == "timed_race"
    assert source.facts["goal_evidence.race.date"] is None
    assert source.facts["goal_evidence.race.days_to_event"] is None
    assert "sans date" in SYSTEM_PROMPT_V1

    high_confidence = assessment_for(source, confidence="high")
    with pytest.raises(GoalAssessmentError) as high_error:
        validate_goal_assessment(high_confidence, source)
    assert high_error.value.code == "confidence_incompatible_missing_date"

    missing_uncertainty = assessment_for(source).model_copy(
        update={"uncertainties": [], "caveats": []}
    )
    with pytest.raises(GoalAssessmentError) as uncertainty_error:
        validate_goal_assessment(missing_uncertainty, source)
    assert uncertainty_error.value.code == "missing_date_uncertainty_required"

    assert validate_goal_assessment(assessment_for(source), source).confidence == "medium"


@pytest.mark.parametrize(
    ("event_type", "distance"), [("trail", 35.0), ("custom", 12.3)]
)
def test_trail_and_custom_remain_explicit_without_riegel_invention(
    event_type: str, distance: float
) -> None:
    runtime = prepare_runtime(
        f"event_{event_type}",
        race_goal(event_type=event_type, distance_km=distance),
    )
    source = build_goal_assessment_input_v1(runtime)

    assert source.facts["goal_evidence.race.event_type"] == event_type
    assert source.facts["goal_evidence.performance_comparison.method"] is None


def test_recovery_absence_and_no_past_race_are_preserved_as_absence_of_evidence() -> None:
    source = build_goal_assessment_input_v1(
        prepare_runtime("missing_evidence", race_goal())
    )

    assert source.facts["motionest_context.data_confidence.recovery"] == "none"
    assert source.facts["training_pattern.past_race_evidence"] == []
    assert source.facts["training_pattern.session_patterns.race"]["confidence"] == "none"


def test_training_patterns_are_forwarded_as_facts_without_reclassification() -> None:
    runtime = prepare_runtime("patterns", race_goal())
    source = build_goal_assessment_input_v1(runtime)

    assert "training_pattern.session_patterns.structured_intervals" in source.facts
    assert source.facts["training_pattern.session_patterns.relative_long_run"][
        "count_84_days"
    ] >= 1
    assert "training_pattern.pattern_coverage" in source.facts


def test_explicit_structured_interval_pattern_is_forwarded_unchanged() -> None:
    runtime = prepare_runtime("structured_pattern", race_goal())
    pattern = runtime.training_pattern
    observed = pattern.session_patterns.structured_intervals.model_copy(
        update={
            "status": "observed",
            "count_28_days": 1,
            "count_84_days": 2,
            "provenance": ["garmin_typed_splits"],
            "confidence": "high",
        }
    )
    runtime = runtime.model_copy(
        update={
            "training_pattern": pattern.model_copy(
                update={
                    "session_patterns": pattern.session_patterns.model_copy(
                        update={"structured_intervals": observed}
                    )
                }
            )
        }
    )
    source = build_goal_assessment_input_v1(runtime)

    assert source.facts["training_pattern.session_patterns.structured_intervals"] == {
        "status": "observed",
        "count_28_days": 1,
        "count_84_days": 2,
        "provenance": ["garmin_typed_splits"],
        "confidence": "high",
    }


@pytest.mark.parametrize("verdict", ["realistic", "very_ambitious"])
def test_provider_may_interpret_close_or_challenging_facts_without_local_thresholds(
    verdict: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = f"verdict_{verdict}"
    runtime = prepare_runtime(user_id, race_goal())
    source = build_goal_assessment_input_v1(runtime)
    monkeypatch.setattr(
        ai_provider,
        "structured_completion",
        lambda *args, **kwargs: assessment_for(source, assessment=verdict),
    )

    state = generate_goal_assessment(user_id, runtime=runtime, settings=settings())
    assert state.assessment is not None
    assert state.assessment.assessment == verdict


def test_unknown_supporting_fact_and_semantic_incompatibilities_are_rejected() -> None:
    source = build_goal_assessment_input_v1(
        prepare_runtime("invalid_grounding", race_goal())
    )
    invented = assessment_for(source, path="garmin.raw.imaginary_fact")
    with pytest.raises(GoalAssessmentError, match="fait absent"):
        validate_goal_assessment(invented, source)

    high_insufficient = assessment_for(
        source, assessment="insufficient_evidence", confidence="high"
    )
    with pytest.raises(GoalAssessmentError, match="confiance élevée"):
        validate_goal_assessment(high_insufficient, source)

    unsupported = assessment_for(source).model_copy(update={"supporting_facts": []})
    with pytest.raises(GoalAssessmentError, match="au moins un fait"):
        validate_goal_assessment(unsupported, source)


def test_invalid_provider_response_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = prepare_runtime("provider_failures", race_goal())

    def invalid(*args: Any, **kwargs: Any) -> Any:
        GoalAssessmentV1.model_validate({})

    monkeypatch.setattr(ai_provider, "structured_completion", invalid)
    with pytest.raises(GoalAssessmentError) as invalid_error:
        generate_goal_assessment("provider_failures", runtime=runtime, settings=settings())
    assert invalid_error.value.code == "invalid_provider_response"


@pytest.mark.parametrize(
    "error",
    [
        json.JSONDecodeError("invalid", "{", 1),
        ValueError("L'IA n'a pas renvoyé de JSON exploitable."),
        RuntimeError("L'IA n'a pas produit de réponse structurée."),
    ],
)
def test_parser_and_structured_output_failures_are_invalid_provider_responses(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = f"invalid_structured_{type(error).__name__}"
    runtime = prepare_runtime(user_id, race_goal())
    monkeypatch.setattr(
        ai_provider,
        "structured_completion",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(GoalAssessmentError) as provider_error:
        generate_goal_assessment(user_id, runtime=runtime, settings=settings())

    assert provider_error.value.code == "invalid_provider_response"


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (TimeoutError("timeout"), "provider_timeout"),
        (RuntimeError("status 429 rate limit"), "provider_rate_limited"),
        (RuntimeError("insufficient_quota"), "provider_quota_exhausted"),
        (ValueError("unrelated value error"), "provider_unavailable"),
        (RuntimeError("provider down"), "provider_unavailable"),
    ],
)
def test_provider_failure_modes_are_classified_without_fallback(
    error: Exception, expected_code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = prepare_runtime(f"failure_{expected_code}", race_goal())
    monkeypatch.setattr(
        ai_provider,
        "structured_completion",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )
    with pytest.raises(GoalAssessmentError) as provider_error:
        generate_goal_assessment(
            f"failure_{expected_code}", runtime=runtime, settings=settings()
        )
    assert provider_error.value.code == expected_code
    assert get_goal_assessment_state(
        f"failure_{expected_code}", runtime=runtime
    ).status == "missing"


def test_quota_signal_has_priority_over_rate_limit_class_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rate_limit_error = type("RateLimitError", (RuntimeError,), {})
    error = rate_limit_error("insufficient_quota")
    runtime = prepare_runtime("quota_priority", race_goal())
    monkeypatch.setattr(
        ai_provider,
        "structured_completion",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(GoalAssessmentError) as provider_error:
        generate_goal_assessment(
            "quota_priority", runtime=runtime, settings=settings()
        )

    assert provider_error.value.code == "provider_quota_exhausted"


def test_missing_key_never_fabricates_a_fallback_assessment() -> None:
    runtime = prepare_runtime("missing_key", race_goal())
    with pytest.raises(GoalAssessmentError) as error:
        generate_goal_assessment("missing_key", runtime=runtime, settings=settings(key=None))
    assert error.value.code == "ai_not_configured"
    assert get_goal_assessment_state("missing_key", runtime=runtime).status == "missing"


def test_cache_hit_calls_provider_once_and_persists_no_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "cache_hit"
    runtime = prepare_runtime(user_id, race_goal())
    source = build_goal_assessment_input_v1(runtime)
    calls = 0

    def mocked(*args: Any, **kwargs: Any) -> GoalAssessmentV1:
        nonlocal calls
        calls += 1
        return assessment_for(source)

    monkeypatch.setattr(ai_provider, "structured_completion", mocked)
    first = generate_goal_assessment(user_id, runtime=runtime, settings=settings())
    second = generate_goal_assessment(user_id, runtime=runtime, settings=settings())

    assert calls == 1
    assert first.cache_hit is False
    assert second.cache_hit is True
    with config.user_context(user_id):
        persisted = json.loads(
            config.GOAL_ASSESSMENT_FILE.read_text(encoding="utf-8")
        )
        assert "facts" not in persisted
        assert len(persisted["prompt_digest"]) == 64
        assert SYSTEM_PROMPT_V1 not in json.dumps(persisted, ensure_ascii=False)
        assert not list(config.GOAL_ASSESSMENT_FILE.parent.glob("*.tmp"))


def test_concurrent_generation_for_same_fingerprint_calls_provider_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "concurrent_cache"
    runtime = prepare_runtime(user_id, race_goal())
    source = build_goal_assessment_input_v1(runtime)
    calls = 0

    def mocked(*args: Any, **kwargs: Any) -> GoalAssessmentV1:
        nonlocal calls
        calls += 1
        time.sleep(0.02)
        return assessment_for(source)

    monkeypatch.setattr(ai_provider, "structured_completion", mocked)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: generate_goal_assessment(
                    user_id, runtime=runtime, settings=settings()
                ),
                range(2),
            )
        )

    assert calls == 1
    assert sorted(item.cache_hit for item in results) == [False, True]


def test_different_users_generate_concurrently_and_keep_isolated_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alpha_runtime = prepare_runtime("parallel_alpha", race_goal())
    beta_runtime = prepare_runtime(
        "parallel_beta", race_goal(target_time="01:35:00")
    )
    response = assessment_for(build_goal_assessment_input_v1(alpha_runtime))
    barrier = threading.Barrier(2)
    counter_lock = threading.Lock()
    active = 0
    peak_active = 0

    def mocked(*args: Any, **kwargs: Any) -> GoalAssessmentV1:
        nonlocal active, peak_active
        with counter_lock:
            active += 1
            peak_active = max(peak_active, active)
        try:
            barrier.wait(timeout=2)
            time.sleep(0.02)
            return response
        finally:
            with counter_lock:
                active -= 1

    monkeypatch.setattr(ai_provider, "structured_completion", mocked)
    with ThreadPoolExecutor(max_workers=2) as executor:
        alpha_future = executor.submit(
            generate_goal_assessment,
            "parallel_alpha",
            runtime=alpha_runtime,
            settings=settings(),
        )
        beta_future = executor.submit(
            generate_goal_assessment,
            "parallel_beta",
            runtime=beta_runtime,
            settings=settings(),
        )
        alpha_state = alpha_future.result(timeout=5)
        beta_state = beta_future.result(timeout=5)

    assert peak_active == 2
    assert alpha_state.status == beta_state.status == "available"
    with config.user_context("parallel_alpha"):
        alpha_store = Path(config.GOAL_ASSESSMENT_FILE)
    with config.user_context("parallel_beta"):
        beta_store = Path(config.GOAL_ASSESSMENT_FILE)
    assert alpha_store != beta_store
    assert alpha_store.is_file()
    assert beta_store.is_file()


def test_fingerprint_changes_with_goal_history_pattern_and_prompt() -> None:
    first_runtime = prepare_runtime("fingerprint_one", race_goal())
    first = build_goal_assessment_input_v1(first_runtime)
    changed_goal = build_goal_assessment_input_v1(
        prepare_runtime("fingerprint_goal", race_goal(target_time="01:40:00"))
    )
    changed_history = build_goal_assessment_input_v1(
        prepare_runtime("fingerprint_history", race_goal(), activity_count=3)
    )
    changed_pattern = first.model_copy(deep=True)
    changed_pattern.facts["training_pattern.pattern_coverage"][
        "sessions_with_observed_pattern"
    ] += 1

    baseline = goal_assessment_fingerprint(first)
    assert goal_assessment_fingerprint(changed_goal) != baseline
    assert goal_assessment_fingerprint(changed_history) != baseline
    assert goal_assessment_fingerprint(changed_pattern) != baseline
    assert goal_assessment_fingerprint(first, prompt_version=PROMPT_VERSION + 1) != baseline
    assert goal_assessment_fingerprint(
        first, system_prompt=SYSTEM_PROMPT_V1 + "\nInstruction révisée."
    ) != baseline


def test_store_becomes_stale_when_goal_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "stale_store"
    first_runtime = prepare_runtime(user_id, race_goal())
    first_source = build_goal_assessment_input_v1(first_runtime)
    monkeypatch.setattr(
        ai_provider,
        "structured_completion",
        lambda *args, **kwargs: assessment_for(first_source),
    )
    generate_goal_assessment(user_id, runtime=first_runtime, settings=settings())

    changed_runtime = prepare_runtime(
        user_id, race_goal(target_time="01:40:00")
    )
    assert get_goal_assessment_state(user_id, runtime=changed_runtime).status == "stale"


def test_corrupt_store_is_missing_and_does_not_fallback_to_stale_data() -> None:
    user_id = "corrupt_store"
    runtime = prepare_runtime(user_id, race_goal())
    with config.user_context(user_id):
        config.GOAL_ASSESSMENT_FILE.write_text("{broken", encoding="utf-8")
    state = get_goal_assessment_state(user_id, runtime=runtime)
    assert state.status == "missing"
    assert state.assessment is None
    assert state.diagnostics == ["goal_assessment_store_invalid"]


def test_two_users_have_isolated_assessment_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alpha_runtime = prepare_runtime("assessment_alpha", race_goal())
    beta_runtime = prepare_runtime("assessment_beta", race_goal(target_time="01:35:00"))
    sources = {
        "assessment_alpha": build_goal_assessment_input_v1(alpha_runtime),
        "assessment_beta": build_goal_assessment_input_v1(beta_runtime),
    }
    active = "assessment_alpha"

    def mocked(*args: Any, **kwargs: Any) -> GoalAssessmentV1:
        return assessment_for(sources[active])

    monkeypatch.setattr(ai_provider, "structured_completion", mocked)
    generate_goal_assessment("assessment_alpha", runtime=alpha_runtime, settings=settings())
    active = "assessment_beta"
    generate_goal_assessment("assessment_beta", runtime=beta_runtime, settings=settings())

    assert get_goal_assessment_state(
        "assessment_alpha", runtime=alpha_runtime
    ).status == "available"
    assert get_goal_assessment_state(
        "assessment_beta", runtime=beta_runtime
    ).status == "available"
    with config.user_context("assessment_alpha"):
        alpha_path = Path(config.GOAL_ASSESSMENT_FILE)
    with config.user_context("assessment_beta"):
        beta_path = Path(config.GOAL_ASSESSMENT_FILE)
    assert alpha_path != beta_path


def _serve() -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _request(
    server: ThreadingHTTPServer, method: str, cookie: str
) -> tuple[int, dict[str, Any]]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    connection.request(method, "/api/goal-assessment", headers={"Cookie": cookie})
    response = connection.getresponse()
    payload = json.loads(response.read())
    connection.close()
    return response.status, payload


def test_get_never_calls_ai_and_post_calls_once_for_same_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "assessment_endpoint"
    runtime = prepare_runtime(user_id, race_goal())
    source = build_goal_assessment_input_v1(runtime)
    calls = 0

    def mocked(*args: Any, **kwargs: Any) -> GoalAssessmentV1:
        nonlocal calls
        calls += 1
        return assessment_for(source)

    monkeypatch.setattr(ai_provider, "structured_completion", mocked)
    monkeypatch.setattr(assessment_module, "load_settings", lambda: settings())
    cookie = f"motionest_session={user_accounts.create_session(user_id)}"
    server, thread = _serve()
    try:
        get_status, get_payload = _request(server, "GET", cookie)
        first_status, first_payload = _request(server, "POST", cookie)
        second_status, second_payload = _request(server, "POST", cookie)
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()

    assert get_status == 200
    assert get_payload["status"] == "missing"
    assert calls == 1
    assert first_status == second_status == 200
    assert first_payload["cache_hit"] is False
    assert second_payload["cache_hit"] is True


def test_payload_and_local_processing_are_compact() -> None:
    runtime = prepare_runtime("assessment_performance", race_goal())
    started = time.perf_counter()
    source = build_goal_assessment_input_v1(runtime)
    fingerprint = goal_assessment_fingerprint(source)
    elapsed = time.perf_counter() - started
    encoded = json.dumps(source.model_dump(mode="json"), ensure_ascii=False).encode()

    assert len(fingerprint) == 64
    assert len(encoded) < 20_000
    assert elapsed < 0.1
