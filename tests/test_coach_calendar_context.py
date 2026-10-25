"""Synthetic canonical calendar projection and deterministic reading regressions."""
from datetime import date, datetime, timezone
import json
from types import SimpleNamespace

import pytest

from garmin_coach import coach_calendar_context as module, config
from garmin_coach.plan_proposal import PlanProposalV2, PlanProposalStateV2, PlanSessionV2, proposal_fingerprint


def session(identity="evening", hour=18, **changes):
    raw = dict(session_id=identity, starts_at=datetime(2026, 10, 27, hour, tzinfo=timezone.utc),
               date=date(2026, 10, 27), sport="running", session_type="easy_run",
               objective="Développer une endurance confortable", duration_minutes=35,
               intensity_instruction="Facile, pouvoir parler sans difficulté",
               explanation="Séance facile prévue", structure={"main_set": "Courir facilement"})
    raw.update(changes)
    return PlanSessionV2.model_validate(raw)


@pytest.fixture
def projected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DATA_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / "secrets")
    def build(sessions=None, acceptance="accepted", delivery=None):
        proposal = PlanProposalV2(period={"start": "2026-10-26", "end": "2026-11-01"},
            summary="Semaine prévue", strategy="Progression facile", sessions=sessions or [session()])
        state = PlanProposalStateV2(status="available", input_fingerprint="a" * 64, proposal=proposal)
        monkeypatch.setattr(module, "get_plan_proposal_state", lambda user: state)
        monkeypatch.setattr(module, "get_plan_acceptance_state", lambda *a, **kw: SimpleNamespace(status=acceptance))
        monkeypatch.setattr(module, "local_calendar_blocked_dates", lambda *a: [date(2026, 10, 28)])
        monkeypatch.setattr(module, "get_garmin_delivery_state", lambda user: delivery or {
            "proposal_fingerprint": proposal_fingerprint(proposal), "sessions": [
                {"session_id": s.session_id, "garmin_connect_status": "not_created", "upload_status": "not_requested",
                 "calendar_status": "not_scheduled", "last_error": "password=PRIVATE", "token": "PRIVATE"}
                for s in proposal.sessions]})
        return module.build_coach_calendar_context("alice")
    return build


def answer(calendar, text="Quelle est ma séance de mardi 27/10 à 18h ?"):
    return module.grounded_v2_calendar_response(text, {"calendrier_plan_v2": calendar,
        "plan_actif": {"seances": [{"date": "2026-10-27", "titre": "Legacy contradictoire"}]}})


def test_reference_35_minutes_is_read_from_v2_without_invented_steps(projected):
    calendar = projected()
    text = answer(calendar)
    assert "35 min" in text and "18 h 00" in text and "2026-10-27" in text
    assert "endurance confortable" in text and "pouvoir parler" in text
    assert "enregistrée dans Motionest" in text and "Garmin non confirmée" in text
    assert "pas d'activités déjà réalisées" in text
    assert "Legacy" not in text and "échauffement" not in text
    assert calendar["sessions"][0]["structure"] == {"main_set": "Courir facilement"}
    assert calendar["blocked_dates"] == ["2026-10-28"]
    assert "PRIVATE" not in json.dumps(calendar)


def test_week_read_lists_all_and_does_not_pick_arbitrarily(projected):
    calendar = projected([session("morning", 8), session()])
    assert "Plusieurs séances" in answer(calendar, "Quelle séance mardi ?")
    assert "08 h 00" in answer(calendar, "J'ai quoi comme séances cette semaine ?")
    assert "18 h 00" in answer(calendar, "J'ai quoi comme séances cette semaine ?")
    assert "08 h 00" in answer(calendar, "Ma séance de mardi matin ?")
    assert "08 h 00" not in answer(calendar, "Ma séance de mardi soir ?")


def test_unknown_time_is_not_guessed(projected):
    calendar = projected([session(starts_at=None)])
    assert "n'est pas renseigné" in answer(calendar, "Ma séance du soir ?")


@pytest.mark.parametrize("status", ["refused", "revision_requested", "stale", "corrupt", "missing"])
def test_acceptance_facts_kept_without_claiming_consent(projected, status):
    calendar = projected(acceptance=status)
    assert f"Accord Motionest : {status}" in answer(calendar)


@pytest.mark.parametrize("status", ["missing", "stale", "invalid", "generation_failed"])
def test_unavailable_source_is_not_claimed_empty(projected, monkeypatch, status):
    projected()
    monkeypatch.setattr(module, "get_plan_proposal_state", lambda user: PlanProposalStateV2(status=status, input_fingerprint="a" * 64))
    calendar = module.build_coach_calendar_context("alice")
    assert calendar["sessions"] == [] and calendar["source_status"] == status
    text = answer(calendar)
    assert text is None if status == "missing" else "indisponible ou obsolète" in text


def test_corrupt_journals_do_not_hide_proposal_or_invent_delivery(projected, monkeypatch):
    projected()
    def broken(*a, **kw):
        raise ValueError("PRIVATE account@example.invalid token")
    monkeypatch.setattr(module, "get_plan_acceptance_state", broken)
    monkeypatch.setattr(module, "get_garmin_delivery_state", broken)
    calendar = module.build_coach_calendar_context("alice")
    assert calendar["status"] == "available"
    assert calendar["sessions"][0]["delivery"] == {"status": "unavailable"}
    assert "non envoyée" not in answer(calendar)
    assert "PRIVATE" not in json.dumps(calendar)


@pytest.mark.parametrize("word", ["Raccourcis", "Déplace", "Supprime", "Annule", "Rends", "Change", "Réduis", "Réduire", "Reporte", "Reporter", "Décale", "Ajoute"])
def test_mutations_are_left_to_confirmation_path(projected, word):
    assert answer(projected(), f"{word} ma séance de mardi") is None


def test_projection_is_bounded_and_declares_omissions(projected):
    blocks = [dict(effort_minutes=1, intensity_instruction="Facile") for _ in range(100)]
    calendar = projected([session(str(i), duration_minutes=100,
        objective="A" * 240, explanation="B" * 500,
        structure={"main_set": "C" * 600, "blocks": blocks}) for i in range(7)])
    assert calendar["truncated"]
    assert calendar["sessions"][0]["structure_truncated"] is True
    assert len(json.dumps(calendar, ensure_ascii=False)) <= module.MAX_CONTEXT_CHARS
    # Exercise smaller budgets while retaining an explicit non-exhaustive marker.
    small = module.build_coach_calendar_context("alice", max_chars=2048)
    assert len(json.dumps(small, ensure_ascii=False)) <= 2048
    assert small["truncated"] and small["omitted_sessions"] > 0
    single = projected([session(duration_minutes=100, structure={"main_set": "Facile", "blocks": blocks})])
    assert single["sessions"][0]["structure"]["omitted_blocks"] == 92


@pytest.mark.parametrize("kind,expected", [
    ("scheduled", "Programmation Garmin confirmée"),
    ("uncertain", "Création Garmin incertaine"),
    ("created", "Création Garmin confirmée, programmation non confirmée"),
])
def test_delivery_facts_are_not_conflated(projected, monkeypatch, kind, expected):
    calendar = projected()
    fact = {"session_id": "evening", "garmin_connect_status": "created", "calendar_status": "not_scheduled"}
    if kind == "scheduled":
        fact["calendar_status"] = "scheduled"
    elif kind == "uncertain":
        fact.update(garmin_connect_status="unknown", upload_status="uncertain")
    monkeypatch.setattr(module, "get_garmin_delivery_state", lambda user: {
        "proposal_fingerprint": calendar["proposal_fingerprint"], "sessions": [fact]})
    assert expected in answer(module.build_coach_calendar_context("alice"))


def test_stale_delivery_fingerprint_is_not_attributed_to_current_session(projected, monkeypatch):
    projected()
    monkeypatch.setattr(module, "get_garmin_delivery_state", lambda user: {
        "proposal_fingerprint": "wrong", "sessions": [{"session_id": "evening", "calendar_status": "scheduled"}]})
    calendar = module.build_coach_calendar_context("alice")
    assert calendar["sessions"][0]["delivery"] == {"status": "unavailable"}


def test_user_scope_is_bound_and_restored(projected, monkeypatch):
    projected()
    seen = []
    def acceptance(user, **kw):
        seen.append((user, config.current_user_id()))
        return SimpleNamespace(status="accepted")
    monkeypatch.setattr(module, "get_plan_acceptance_state", acceptance)
    with config.user_context("bob"):
        module.build_coach_calendar_context("alice")
        assert config.current_user_id() == "bob"
    assert seen == [("alice", "alice")]


def test_private_free_text_filtered(projected):
    calendar = projected([session(objective="Contact athlete@example.invalid", intensity_instruction="token PRIVATE")])
    item = calendar["sessions"][0]
    assert item["objective"] is None and item["intensity_instruction"] is None


def test_canonical_failure_is_safe_and_legacy_without_projection_preserved(monkeypatch):
    monkeypatch.setattr(module, "get_plan_proposal_state", lambda user: (_ for _ in ()).throw(ValueError("PRIVATE")))
    calendar = module.build_coach_calendar_context("alice")
    assert calendar["source_status"] == "unavailable" and "PRIVATE" not in json.dumps(calendar)
    assert module.grounded_v2_calendar_response("Quel est mon plan actif ?", {"plan_actif": {}}) is None


def test_seven_verbose_sessions_fit_coach_message_without_losing_essential_slots(projected):
    calendar = projected([session(str(i), objective="A" * 240,
        intensity_instruction="B" * 400) for i in range(7)])
    text = answer(calendar, "J'ai quoi comme séances cette semaine ?")
    assert len(text) <= 3000
    assert "omis" in text and "précise une séance" in text
    for i in range(7):
        assert f"identité {i}." in text
    assert text.count("35 min") == 7
