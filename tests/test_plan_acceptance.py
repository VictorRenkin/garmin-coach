from __future__ import annotations

import json
import threading
import urllib.request
from datetime import date
from pathlib import Path

import pytest

from garmin_coach import config, dashboard, plan_acceptance as module, user_accounts
from garmin_coach.plan_acceptance import (
    PlanAcceptanceError,
    PlanAcceptanceStateV2,
    get_plan_acceptance_state,
    record_plan_acceptance_decision,
)
from garmin_coach.plan_proposal import (
    PlanPeriodV2,
    PlanProposalStateV2,
    PlanProposalV2,
    PlanSessionStructureV2,
    PlanSessionV2,
    proposal_fingerprint,
)


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / ".secrets")
    monkeypatch.setattr(user_accounts, "SESSION_KEY_FILE", tmp_path / "session.key")
    return tmp_path


def proposal(*, objective: str = "Construire une base régulière.") -> PlanProposalV2:
    return PlanProposalV2(
        period=PlanPeriodV2(start=date(2026, 10, 12), end=date(2026, 10, 18)),
        summary="Une semaine progressive.",
        strategy="Deux séances espacées et qualitatives.",
        sessions=[
            PlanSessionV2(
                date=date(2026, 10, 13),
                sport="running",
                session_type="aerobic_session",
                objective=objective,
                duration_minutes=40,
                intensity_instruction="Effort confortable et conversation possible.",
                structure=PlanSessionStructureV2(
                    warmup_minutes=10,
                    main_set="Vingt minutes régulières.",
                    cooldown_minutes=10,
                ),
                explanation="Séance compatible avec les faits disponibles.",
            )
        ],
    )


def available_state(*, value: PlanProposalV2 | None = None, fingerprint: str = "a" * 64):
    return PlanProposalStateV2(
        status="available", input_fingerprint=fingerprint, proposal=value or proposal()
    )


def decide(user_id: str, decision: str, state: PlanProposalStateV2, feedback=None):
    return record_plan_acceptance_decision(
        user_id,
        decision=decision,  # type: ignore[arg-type]
        input_fingerprint=state.input_fingerprint,
        expected_proposal_fingerprint=proposal_fingerprint(state.proposal),
        feedback=feedback,
        proposal_state=state,
    )


def test_exact_proposal_identity_and_explicit_acceptance_are_persisted() -> None:
    state = available_state()
    result = decide("accepted_user", "accepted", state)

    assert result.status == "accepted"
    assert result.decision.source == "user_confirmed"
    assert result.decision.input_fingerprint == state.input_fingerprint
    assert result.decision.proposal_fingerprint == proposal_fingerprint(state.proposal)
    stored = json.loads((config.USERS_DATA_DIR / "accepted_user" / "plan_acceptance_v2.json").read_text())
    assert len(stored["decisions"]) == 1


def test_proposal_fingerprint_is_deterministic_and_content_specific() -> None:
    assert proposal_fingerprint(proposal()) == proposal_fingerprint(proposal())
    assert proposal_fingerprint(proposal()) != proposal_fingerprint(
        proposal(objective="Reprendre progressivement.")
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("input", "b" * 64, "input_fingerprint_mismatch"),
        ("proposal", "b" * 64, "proposal_fingerprint_mismatch"),
    ],
)
def test_falsified_or_old_fingerprint_is_rejected(field, value, code) -> None:
    state = available_state()
    kwargs = {
        "input_fingerprint": state.input_fingerprint,
        "expected_proposal_fingerprint": proposal_fingerprint(state.proposal),
    }
    kwargs["input_fingerprint" if field == "input" else "expected_proposal_fingerprint"] = value
    with pytest.raises(PlanAcceptanceError) as error:
        record_plan_acceptance_decision(
            "fingerprint_user", decision="accepted", proposal_state=state, **kwargs
        )
    assert error.value.code == code


@pytest.mark.parametrize("status", ["missing", "invalid", "stale", "generation_failed"])
def test_unavailable_proposal_cannot_be_accepted(status: str) -> None:
    state = PlanProposalStateV2(status=status, input_fingerprint="a" * 64)
    with pytest.raises(PlanAcceptanceError) as error:
        record_plan_acceptance_decision(
            "unavailable_user",
            decision="accepted",
            input_fingerprint="a" * 64,
            expected_proposal_fingerprint="b" * 64,
            proposal_state=state,
        )
    assert error.value.code == f"proposal_{status}"


def test_double_acceptance_is_idempotent() -> None:
    state = available_state()
    first = decide("double_user", "accepted", state)
    second = decide("double_user", "accepted", state)
    stored = json.loads((config.USERS_DATA_DIR / "double_user" / "plan_acceptance_v2.json").read_text())

    assert not first.idempotent
    assert second.idempotent
    assert first.decision.decided_at == second.decision.decided_at
    assert len(stored["decisions"]) == 1


def test_refusal_and_revision_are_audited_without_mutating_proposal() -> None:
    state = available_state()
    original = state.model_dump(mode="json")
    refused = decide("choices_user", "refused", state)
    revised = decide(
        "choices_user",
        "revision_requested",
        state,
        "La séance du mardi est trop longue.",
    )

    assert refused.status == "refused"
    assert revised.status == "revision_requested"
    assert revised.decision.feedback == "La séance du mardi est trop longue."
    assert state.model_dump(mode="json") == original


def test_revision_requires_feedback() -> None:
    state = available_state()
    with pytest.raises(PlanAcceptanceError) as error:
        decide("revision_user", "revision_requested", state)
    assert error.value.code == "decision_invalid"


def test_changed_proposal_makes_previous_acceptance_stale() -> None:
    first = available_state()
    decide("stale_user", "accepted", first)
    changed = available_state(value=proposal(objective="Reprendre progressivement."))
    current = get_plan_acceptance_state("stale_user", proposal_state=changed)

    assert current.status == "stale"
    assert current.decision.decision == "accepted"
    assert current.proposal_fingerprint == proposal_fingerprint(changed.proposal)


def test_get_and_all_decisions_have_no_ai_garmin_or_publication(monkeypatch) -> None:
    state = available_state()
    forbidden = lambda *args, **kwargs: pytest.fail("external side effect called")
    monkeypatch.setattr("garmin_coach.ai_provider.structured_completion", forbidden)
    monkeypatch.setattr("garmin_coach.garmin.connect", forbidden)
    monkeypatch.setattr("garmin_coach.garmin_workouts.publish_plan", forbidden)

    assert get_plan_acceptance_state("no_external", proposal_state=state).status == "missing"
    for decision, feedback in (
        ("accepted", None),
        ("refused", None),
        ("revision_requested", "Alléger la séance."),
    ):
        assert decide("no_external", decision, state, feedback).status == decision


def test_legacy_publication_flag_and_canonical_inputs_are_unchanged() -> None:
    user_id = "unchanged_user"
    state = available_state()
    with config.user_context(user_id):
        onboarding = Path(config.ONBOARDING_FILE)
        onboarding.parent.mkdir(parents=True, exist_ok=True)
        onboarding.write_text('{"publication_allowed": false}', encoding="utf-8")
        goal_before = Path(config.MOTIONEST_INPUTS_FILE).read_bytes() if Path(config.MOTIONEST_INPUTS_FILE).exists() else None
        planning_before = Path(config.PLANNING_DECISION_FILE).read_bytes() if Path(config.PLANNING_DECISION_FILE).exists() else None
    decide(user_id, "accepted", state)
    with config.user_context(user_id):
        assert json.loads(Path(config.ONBOARDING_FILE).read_text())["publication_allowed"] is False
        assert (Path(config.MOTIONEST_INPUTS_FILE).read_bytes() if Path(config.MOTIONEST_INPUTS_FILE).exists() else None) == goal_before
        assert (Path(config.PLANNING_DECISION_FILE).read_bytes() if Path(config.PLANNING_DECISION_FILE).exists() else None) == planning_before


def test_two_users_are_isolated() -> None:
    state = available_state()
    decide("user_a", "accepted", state)
    decide("user_b", "refused", state)

    assert get_plan_acceptance_state("user_a", proposal_state=state).status == "accepted"
    assert get_plan_acceptance_state("user_b", proposal_state=state).status == "refused"


def test_corrupt_store_is_visible_and_never_overwritten() -> None:
    user_id = "corrupt_user"
    state = available_state()
    with config.user_context(user_id):
        target = Path(config.PLAN_ACCEPTANCE_FILE)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{broken", encoding="utf-8")

    assert get_plan_acceptance_state(user_id, proposal_state=state).status == "corrupt"
    with pytest.raises(PlanAcceptanceError) as error:
        decide(user_id, "accepted", state)
    assert error.value.code == "acceptance_store_corrupt"
    with config.user_context(user_id):
        assert Path(config.PLAN_ACCEPTANCE_FILE).read_text(encoding="utf-8") == "{broken"


def test_dashboard_get_and_post_decision_routes_are_explicit(monkeypatch) -> None:
    state = available_state()
    proposal_id = proposal_fingerprint(state.proposal)
    missing = PlanAcceptanceStateV2(
        status="missing",
        input_fingerprint=state.input_fingerprint,
        proposal_fingerprint=proposal_id,
    )
    accepted = decide("route_seed", "accepted", state)
    calls: list[dict] = []
    monkeypatch.setattr(dashboard, "get_plan_acceptance_state", lambda user_id: missing)

    def record(user_id, **kwargs):
        calls.append({"user_id": user_id, **kwargs})
        return accepted

    monkeypatch.setattr(dashboard, "record_plan_acceptance_decision", record)
    cookie = f"motionest_session={user_accounts.create_session('route_user')}"
    server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        get_request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/plan-proposal/decision",
            headers={"Cookie": cookie},
        )
        with urllib.request.urlopen(get_request, timeout=5) as response:
            get_payload = json.loads(response.read())
        assert get_payload["status"] == "missing"

        body = {
            "decision": "accepted",
            "input_fingerprint": state.input_fingerprint,
            "proposal_fingerprint": proposal_id,
        }
        post_request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/plan-proposal/decision",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Cookie": cookie},
            method="POST",
        )
        with urllib.request.urlopen(post_request, timeout=5) as response:
            post_payload = json.loads(response.read())
        assert post_payload["status"] == "accepted"
        assert calls == [
            {
                "user_id": "route_user",
                "decision": "accepted",
                "input_fingerprint": state.input_fingerprint,
                "expected_proposal_fingerprint": proposal_id,
                "feedback": None,
            }
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
