"""Synthetic local V2 drafts: no model and no real Garmin client."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from garmin_coach import config, coach_session_actions as actions, plan_proposal, plan_acceptance, garmin_delivery
from tests.test_calendar_3c import source_for, double_proposal, accept
from tests.test_plan_proposal import settings


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / "secrets")
    user = "synthetic-actions"
    source = source_for(user)
    raw = double_proposal(source).model_dump(mode="json")
    raw["sessions"][0]["duration_minutes"] = 35
    raw["sessions"][0]["structure"]["effort_minutes"] = 25
    proposal = plan_proposal.PlanProposalV2.model_validate(raw)
    monkeypatch.setattr(plan_proposal, "build_plan_generation_input_v2", lambda *args, **kwargs: source)
    monkeypatch.setattr(plan_proposal.ai_provider, "structured_completion", lambda *args, **kwargs: proposal)
    plan_proposal.generate_plan_proposal(user, settings=settings())
    monkeypatch.setattr(actions, "_source", lambda user: source)
    def state():
        with config.user_context(user): return plan_proposal._state(source)
    return user, source, state


def snapshot(user):
    with config.user_context(user):
        return {str(path): path.read_bytes() if path.is_file() else None for path in
            [config.PLAN_PROPOSAL_FILE, config.PLAN_ACCEPTANCE_FILE, config.GARMIN_DELIVERY_FILE]}


def shorten(user):
    return actions.prepare_session_action(user, dict(action="modify", session_id="morning", target_duration_minutes=25))


def test_prepare_and_cancel_do_not_write_plan_decisions_or_delivery(setup):
    user, _, state = setup
    before = snapshot(user)
    draft = shorten(user)
    assert draft["before"]["duration_minutes"] == 35 and draft["after"]["duration_minutes"] == 25
    assert snapshot(user) == before
    assert actions.cancel_session_action(user, draft["draft_id"])["status"] == "cancelled"
    assert actions.cancel_session_action(user, draft["draft_id"])["status"] == "cancelled"
    assert actions.confirm_session_action(user, draft["draft_id"])["status"] == "cancelled"
    assert snapshot(user) == before


def test_confirm_shortens_actual_executable_and_stales_only_its_acceptance(setup):
    user, source, state = setup
    accept(user, state(), "morning")
    accept(user, state(), "evening")
    sibling = state().proposal.sessions[1].model_dump(mode="json")
    draft = shorten(user)
    result = actions.confirm_session_action(user, draft["draft_id"])
    assert result["status"] == "confirmed"
    current = state()
    assert garmin_delivery.build_plan_session_workout(current.proposal.sessions[0]).estimatedDurationInSecs == 1500
    assert "15 minutes" in current.proposal.sessions[0].structure.main_set
    assert current.proposal.sessions[1].model_dump(mode="json") == sibling
    assert plan_acceptance.get_plan_acceptance_state(user, proposal_state=current, session_id="morning").status == "stale"
    assert plan_acceptance.get_plan_acceptance_state(user, proposal_state=current, session_id="evening").status == "accepted"
    after = snapshot(user)
    assert actions.confirm_session_action(user, draft["draft_id"]) == result
    assert snapshot(user) == after


def test_draft_is_stale_after_other_plan_revision(setup):
    user, source, state = setup
    draft = shorten(user)
    plan_proposal.revise_plan_session(user, session_id="evening", source=source,
        expected_proposal_fingerprint=plan_proposal.proposal_fingerprint(state().proposal), patch={"objective": "Objectif changé."})
    before = snapshot(user)
    with pytest.raises(actions.SessionActionError) as error: actions.confirm_session_action(user, draft["draft_id"])
    assert error.value.code == "draft_stale" and snapshot(user) == before


@pytest.mark.parametrize("record", [{"workout_id": 701}, {"upload_requested_at": "2099-10-13T08:00:00+02:00"}])
def test_published_or_uncertain_session_is_blocked_before_and_after_draft(setup, record):
    user, _, state = setup
    draft = shorten(user)
    with config.user_context(user):
        garmin_delivery._write_store({"schema_version": 1, "deliveries": {"synthetic": {"session_id": "morning", **record}}})
    before = snapshot(user)
    with pytest.raises(actions.SessionActionError): actions.confirm_session_action(user, draft["draft_id"])
    with pytest.raises(actions.SessionActionError): shorten(user)
    assert snapshot(user) == before


def test_refused_session_and_completed_date_are_blocked(setup, monkeypatch):
    user, _, state = setup
    current = state()
    plan_acceptance.record_plan_acceptance_decision(user, decision="refused", session_id="morning",
        input_fingerprint=current.input_fingerprint, expected_proposal_fingerprint=plan_proposal.proposal_fingerprint(current.proposal), proposal_state=current)
    with pytest.raises(actions.SessionActionError): shorten(user)
    accept(user, state(), "morning")
    monkeypatch.setattr(actions, "iter_local_activity_history", lambda **kwargs: iter([{"activityId": "synthetic"}]))
    with pytest.raises(actions.SessionActionError) as error: shorten(user)
    assert error.value.code == "session_completed_or_ambiguous"


def test_double_confirmation_is_idempotent(setup):
    user, _, state = setup
    draft = shorten(user)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: actions.confirm_session_action(user, draft["draft_id"]), range(2)))
    assert results[0] == results[1] and results[0]["status"] == "confirmed"


def test_other_account_cannot_read_confirm_or_cancel_draft(setup):
    user, _, state = setup
    draft = shorten(user)
    before = snapshot(user)
    for method in [actions.get_session_action, actions.confirm_session_action, actions.cancel_session_action]:
        with pytest.raises(actions.SessionActionError) as error: method("other-synthetic", draft["draft_id"])
        assert error.value.code == "draft_not_found"
    assert snapshot(user) == before


def test_change_time_reuses_calendar_validation(setup):
    user, _, state = setup
    before = snapshot(user)
    with pytest.raises(actions.SessionActionError):
        actions.prepare_session_action(user, dict(action="change_time", session_id="evening", target_time="08:15"))
    assert snapshot(user) == before
    draft = actions.prepare_session_action(user, dict(action="change_time", session_id="evening", target_time="17:00"))
    assert draft["after"]["starts_at"].endswith("17:00:00+02:00")


def test_crash_after_plan_commit_is_recoverable_and_cannot_be_cancelled(setup, monkeypatch):
    user, _, state = setup
    draft = shorten(user)
    original = actions._write_store
    def fail_final(store):
        if store.drafts[0].status == "confirmed": raise OSError("Synthetic write failure")
        original(store)
    with monkeypatch.context() as scoped:
        scoped.setattr(actions, "_write_store", fail_final)
        with pytest.raises(OSError): actions.confirm_session_action(user, draft["draft_id"])
    assert state().proposal.sessions[0].duration_minutes == 25
    with pytest.raises(actions.SessionActionError): actions.cancel_session_action(user, draft["draft_id"])
    assert actions.confirm_session_action(user, draft["draft_id"])["status"] == "confirmed"


def test_unknown_fields_and_index_target_are_rejected(setup):
    user, _, _ = setup
    with pytest.raises(actions.SessionActionError):
        actions.prepare_session_action(user, dict(action="modify", session_index=0, target_duration_minutes=25))
    with pytest.raises(actions.SessionActionError):
        actions.prepare_session_action(user, dict(action="modify", session_id="morning", patch={"sport": "cycling"}))


def test_complex_duration_requires_explicit_full_structure(setup):
    user, source, state = setup
    from tests.test_session_blocks_3b import session as complex_session
    blocks = complex_session(.75)
    plan_proposal.revise_plan_session(user, session_id="morning", source=source,
        expected_proposal_fingerprint=plan_proposal.proposal_fingerprint(state().proposal),
        patch={"structure": blocks.structure.model_dump(mode="json"), "duration_minutes": blocks.duration_minutes})
    before = snapshot(user)
    with pytest.raises(actions.SessionActionError) as error:
        actions.prepare_session_action(user, dict(action="modify", session_id="morning", target_duration_minutes=25))
    assert error.value.code == "duration_ambiguous" and snapshot(user) == before
    with pytest.raises(actions.SessionActionError) as error:
        actions.prepare_session_action(user, dict(action="modify", session_id="morning", patch={"intensity_instruction": "Plus facile."}))
    assert error.value.code == "intensity_ambiguous"
    raw = blocks.structure.model_dump(mode="json")
    raw["blocks"][0]["intensity_instruction"] = "Consigne synthétique adaptée."
    draft = actions.prepare_session_action(user, dict(action="modify", session_id="morning", patch={"structure": raw}))
    assert snapshot(user) == before
    actions.confirm_session_action(user, draft["draft_id"])
    assert state().proposal.sessions[0].structure.blocks[0].intensity_instruction == "Consigne synthétique adaptée."


def test_move_keeps_time_offset_and_sibling_identical(setup):
    user, source, state = setup
    # Thursday must already be an allowed canonical date; no availability edit.
    assert any(day.weekday() == 3 for day in source.allowed_dates)
    sibling = state().proposal.sessions[1].model_dump(mode="json")
    draft = actions.prepare_session_action(user, dict(action="move", session_id="morning", target_date="2099-10-15"))
    assert draft["after"]["starts_at"] == "2099-10-15T08:00:00+02:00"
    actions.confirm_session_action(user, draft["draft_id"])
    assert state().proposal.sessions[1].model_dump(mode="json") == sibling


def test_decision_changed_after_preview_prevents_confirmation(setup):
    user, _, state = setup
    draft = shorten(user)
    accept(user, state(), "morning")
    before = snapshot(user)
    with pytest.raises(actions.SessionActionError) as error: actions.confirm_session_action(user, draft["draft_id"])
    assert error.value.code == "draft_stale" and snapshot(user) == before
