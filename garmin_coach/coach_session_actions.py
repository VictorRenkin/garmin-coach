"""Local, explicitly confirmed actions on identified V2 sessions. No remote effects."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import config, garmin_delivery, plan_acceptance, plan_proposal
from .local_activity_history import iter_local_activity_history


class SessionActionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SessionActionPatch(StrictModel):
    objective: str | None = Field(default=None, min_length=1, max_length=240)
    intensity_instruction: str | None = Field(default=None, min_length=1, max_length=400)
    explanation: str | None = Field(default=None, min_length=1, max_length=500)
    structure: plan_proposal.PlanSessionStructureV2 | None = None


class SessionActionIntent(StrictModel):
    action: Literal["modify", "move", "change_time"]
    session_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    patch: SessionActionPatch | None = None
    target_duration_minutes: int | None = Field(default=None, strict=True, gt=0, le=480)
    target_date: date | None = None
    target_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}(:\d{2})?$")

    @model_validator(mode="after")
    def unambiguous_action(self):
        if self.action == "modify":
            if self.target_date is not None or self.target_time is not None:
                raise ValueError("Une modification de contenu ne déplace pas la séance.")
            if self.target_duration_minutes is None and not (self.patch and self.patch.model_dump(exclude_none=True)):
                raise ValueError("Précise la modification demandée.")
        elif self.patch is not None or self.target_duration_minutes is not None:
            raise ValueError("Un déplacement ne modifie pas le contenu.")
        elif self.action == "move" and self.target_date is None:
            raise ValueError("Précise la date demandée.")
        elif self.action == "change_time" and (self.target_time is None or self.target_date is not None):
            raise ValueError("Précise uniquement l'heure demandée.")
        if self.target_time is not None:
            time.fromisoformat(self.target_time)
        return self


class Draft(StrictModel):
    draft_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    status: Literal["pending", "applying", "confirmed", "cancelled"] = "pending"
    intent: SessionActionIntent
    before: plan_proposal.PlanSessionV2
    after: plan_proposal.PlanSessionV2
    input_fingerprint: str
    before_proposal_fingerprint: str
    after_proposal_fingerprint: str
    acceptance_fingerprint: str


class Store(StrictModel):
    schema_version: Literal[1] = 1
    drafts: list[Draft] = Field(default_factory=list, max_length=30)


def _path() -> Path:
    return config.DATA_DIR / "coach_session_actions_v1.json"


def _read_store() -> Store:
    target = _path()
    if not target.is_file():
        return Store()
    try:
        if target.stat().st_size > 2_000_000:
            raise ValueError("oversize")
        return Store.model_validate_json(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SessionActionError("draft_store_invalid", "Le journal des brouillons est indisponible.") from exc


def _write_store(store: Store) -> None:
    body = store.model_dump_json(indent=2)
    if len(body.encode("utf-8")) > 2_000_000:
        raise SessionActionError("draft_budget_exceeded", "Le journal des brouillons est plein.")
    config.ensure_directories()
    target = _path()
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(body, encoding="utf-8")
        Store.model_validate_json(temporary.read_text(encoding="utf-8"))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _locked(user_id: str):
    # Same order as delivery + direct revision. No other lock is acquired first.
    with config.user_context(user_id), garmin_delivery._WRITE_LOCK, plan_proposal._WRITE_LOCK, plan_acceptance._WRITE_LOCK:
        yield


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _source(user_id):
    return plan_proposal.build_plan_generation_input_v2(user_id, plan_proposal.build_runtime_motionest_context(user_id))


def _current(user_id, source):
    state = plan_proposal._state(source)
    if state.status != "available" or state.proposal is None:
        raise SessionActionError("proposal_unavailable", "Aucune proposition V2 actuelle à modifier.")
    return state


def _session(state, identity):
    matches = [item for item in state.proposal.sessions if item.session_id == identity]
    if len(matches) != 1:
        raise SessionActionError("session_not_found", "Précise l'identité exacte de la séance V2.")
    return matches[0]


def _mutable(user_id, state, session):
    if session.date < date.today():
        raise SessionActionError("session_past", "Une séance passée ne peut pas être modifiée ici.")
    decision = plan_acceptance.get_plan_acceptance_state(user_id, proposal_state=state, session_id=session.session_id)
    if decision.status in {"refused", "revision_requested", "corrupt"}:
        raise SessionActionError("session_decision_blocked", "La décision actuelle ne permet pas cette modification.")
    records = garmin_delivery._read_store()["deliveries"]
    for record in records.values():
        if not isinstance(record, dict):
            raise SessionActionError("delivery_unavailable", "Le suivi Garmin n'est pas vérifiable.")
        if record.get("session_id") in {None, session.session_id} and any(record.get(key) for key in
                ("workout_id", "upload_requested_at", "schedule_requested_at")):
            raise SessionActionError("session_delivery_blocked", "Cette séance peut déjà exister sur Garmin ; modification locale bloquée.")
    for activity in iter_local_activity_history(start_date=session.date, end_date=session.date):
        # Without a reliable identity, any observed activity on that day makes
        # completion ambiguous. Refuse conservatively rather than mutate it.
        if activity:
            raise SessionActionError("session_completed_or_ambiguous", "Une activité existe à cette date ; modification bloquée.")
    return _digest(decision.model_dump(mode="json"))


def _patch_for(session, intent):
    patch = intent.patch.model_dump(mode="json", exclude_none=True) if intent.patch else {}
    if session.structure.blocks is not None and "intensity_instruction" in patch and "structure" not in patch:
        raise SessionActionError("intensity_ambiguous", "Précise les intensités des blocs ; une consigne seule ne change pas leurs cibles.")
    if intent.target_duration_minutes is not None:
        target = intent.target_duration_minutes
        structure = session.structure
        if "structure" not in patch:
            if structure.blocks is not None or (structure.repetitions or 1) != 1 or (structure.recovery_minutes or 0) != 0:
                raise SessionActionError("duration_ambiguous", "Précise les blocs à modifier pour atteindre cette durée.")
            effort = target - (structure.warmup_minutes or 0) - (structure.cooldown_minutes or 0)
            if effort <= 0:
                raise SessionActionError("duration_ambiguous", "La durée ne permet pas de conserver échauffement et retour au calme.")
            patch["structure"] = structure.model_dump(mode="json")
            patch["structure"]["effort_minutes"] = effort
            patch["structure"]["main_set"] = f"Bloc principal de {effort} minutes ; conserver la consigne d'intensité."
        patch["duration_minutes"] = target
    if intent.target_date is not None:
        patch["date"] = intent.target_date.isoformat()
        if session.starts_at is not None:
            patch["starts_at"] = datetime.combine(intent.target_date, session.starts_at.timetz()).isoformat()
    if intent.target_time is not None:
        if session.starts_at is None:
            raise SessionActionError("time_ambiguous", "Un horaire avec fuseau explicite est nécessaire avant ce changement.")
        moment = datetime.combine(intent.target_date or session.date,
            time.fromisoformat(intent.target_time).replace(tzinfo=session.starts_at.tzinfo))
        patch["starts_at"] = moment.isoformat()
    return patch


def _preview(user_id, state, session, intent, source):
    patch = _patch_for(session, intent)
    try:
        candidate = plan_proposal.preview_plan_session_revision(user_id, session_id=intent.session_id,
            expected_proposal_fingerprint=plan_proposal.proposal_fingerprint(state.proposal), patch=patch, source=source)
        after = _session(candidate, intent.session_id)
        # Reuse the converter locally, never a Garmin client. Verify the actual
        # executable duration; canonical validation tolerances are not rounding
        # permission for a user-requested exact duration.
        workout = garmin_delivery.build_plan_session_workout(after)
        if workout.estimatedDurationInSecs != after.duration_minutes * 60:
            raise ValueError("executable duration")
    except (ValueError, plan_proposal.PlanProposalError, garmin_delivery.GarminDeliveryError) as exc:
        raise SessionActionError("candidate_invalid", "La modification complète n'est pas représentable ou ne respecte pas le calendrier.") from exc
    if plan_proposal.session_fingerprint(after) == plan_proposal.session_fingerprint(session):
        raise SessionActionError("no_change", "La demande ne change pas cette séance.")
    return candidate, after


def _public(draft):
    fields = {"session_id", "date", "starts_at", "sport", "session_type", "objective", "duration_minutes",
              "distance_km", "intensity_instruction", "structure", "explanation"}
    return {"draft_id": draft.draft_id, "status": draft.status,
            "session_id": draft.intent.session_id,
            "before": draft.before.model_dump(mode="json", include=fields),
            "after": draft.after.model_dump(mode="json", include=fields)}


def _find(store, draft_id):
    draft = next((item for item in store.drafts if item.draft_id == draft_id), None)
    if draft is None:
        raise SessionActionError("draft_not_found", "Brouillon introuvable dans ce compte.")
    return draft


def get_session_action(user_id: str, draft_id: str) -> dict:
    with _locked(user_id):
        return _public(_find(_read_store(), draft_id))


def prepare_session_action(user_id: str, intent: SessionActionIntent | dict) -> dict:
    try:
        intent = SessionActionIntent.model_validate(intent.model_dump() if isinstance(intent, SessionActionIntent) else intent)
    except ValueError as exc:
        raise SessionActionError("intent_invalid", "Précise une séance et une modification V2 valides.") from exc
    with _locked(user_id):
        source = _source(user_id)
        state = _current(user_id, source)
        session = _session(state, intent.session_id)
        accepted = _mutable(user_id, state, session)
        candidate, after = _preview(user_id, state, session, intent, source)
        draft = Draft(draft_id=uuid.uuid4().hex, intent=intent, before=session, after=after,
            input_fingerprint=state.input_fingerprint,
            before_proposal_fingerprint=plan_proposal.proposal_fingerprint(state.proposal),
            after_proposal_fingerprint=plan_proposal.proposal_fingerprint(candidate.proposal),
            acceptance_fingerprint=accepted)
        store = _read_store()
        if len(store.drafts) >= 30:
            raise SessionActionError("draft_budget_exceeded", "Le journal des brouillons est plein.")
        store.drafts.append(draft)
        _write_store(store)
        return _public(draft)


def confirm_session_action(user_id: str, draft_id: str) -> dict:
    with _locked(user_id):
        store = _read_store()
        draft = _find(store, draft_id)
        if draft.status in {"confirmed", "cancelled"}:
            return _public(draft)
        source = _source(user_id)
        state = _current(user_id, source)
        fingerprint = plan_proposal.proposal_fingerprint(state.proposal)
        if draft.status == "applying" and fingerprint == draft.after_proposal_fingerprint and state.input_fingerprint == draft.input_fingerprint:
            draft.status = "confirmed"  # Recover committed plan, never apply twice.
            _write_store(store)
            return _public(draft)
        if fingerprint != draft.before_proposal_fingerprint or state.input_fingerprint != draft.input_fingerprint:
            raise SessionActionError("draft_stale", "L'aperçu est devenu obsolète ; prépare une nouvelle demande.")
        session = _session(state, draft.intent.session_id)
        if plan_proposal.session_fingerprint(session) != plan_proposal.session_fingerprint(draft.before):
            raise SessionActionError("draft_stale", "La séance affichée a changé.")
        if _mutable(user_id, state, session) != draft.acceptance_fingerprint:
            raise SessionActionError("draft_stale", "La décision de cette séance a changé.")
        candidate, after = _preview(user_id, state, session, draft.intent, source)
        if plan_proposal.proposal_fingerprint(candidate.proposal) != draft.after_proposal_fingerprint:
            raise SessionActionError("draft_stale", "Le candidat validé a changé.")
        draft.status = "applying"
        _write_store(store)
        # No separate acceptance mutation: its existing session fingerprint
        # makes the old agreement stale once this atomic plan write succeeds.
        plan_proposal.revise_plan_session(user_id, session_id=draft.intent.session_id,
            expected_proposal_fingerprint=fingerprint, patch=after.model_dump(mode="json", exclude={"session_id"}), source=source)
        draft.status = "confirmed"
        _write_store(store)
        return _public(draft)


def cancel_session_action(user_id: str, draft_id: str) -> dict:
    with _locked(user_id):
        store = _read_store()
        draft = _find(store, draft_id)
        if draft.status in {"confirmed", "cancelled"}:
            return _public(draft)
        if draft.status == "applying":
            raise SessionActionError("action_in_progress", "Une application a commencé ; vérifie son résultat avant toute autre action.")
        draft.status = "cancelled"
        _write_store(store)
        return _public(draft)
