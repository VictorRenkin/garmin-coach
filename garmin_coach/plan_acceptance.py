"""Explicit, local and audited decisions for PlanProposal V2.

Acceptance is deliberately not publication.  This module has no Garmin, coach
or AI dependency and never mutates the proposal or its source contracts.
"""
from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config import PLAN_ACCEPTANCE_FILE, ensure_directories, user_context
from .plan_proposal import (
    PlanProposalStateV2,
    get_plan_proposal_state,
    proposal_fingerprint,
    session_fingerprint,
)


SCHEMA_VERSION = 2
Decision = Literal["accepted", "refused", "revision_requested"]
AcceptanceStatus = Literal[
    "missing", "accepted", "partially_accepted", "refused", "revision_requested", "stale", "corrupt"
]
_WRITE_LOCK = threading.RLock()


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def _clean_feedback(value: str | None) -> str | None:
    if value is None:
        return None
    clean = " ".join(value.strip().split())
    if not clean:
        return None
    if len(clean) > 1_000 or any(ord(character) < 32 for character in clean):
        raise ValueError("Le feedback de décision est invalide.")
    return clean


class PlanAcceptanceDecisionV2(StrictContract):
    schema_version: Literal[2] = SCHEMA_VERSION
    decision: Decision
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposal_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    feedback: str | None = Field(default=None, max_length=1_000)
    decided_at: str
    source: Literal["user_confirmed"] = "user_confirmed"
    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    session_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("feedback", mode="before")
    @classmethod
    def normalize_feedback(cls, value: object) -> str | None:
        return _clean_feedback(None if value is None else str(value))

    @field_validator("decided_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()

    @model_validator(mode="after")
    def validate_revision_feedback(self) -> "PlanAcceptanceDecisionV2":
        if self.decision == "revision_requested" and self.feedback is None:
            raise ValueError("Une demande de modification exige un feedback.")
        return self


class PlanAcceptanceStoreV2(StrictContract):
    schema_version: Literal[2] = SCHEMA_VERSION
    decisions: list[PlanAcceptanceDecisionV2] = Field(default_factory=list, max_length=100)


class PlanAcceptanceStateV2(StrictContract):
    status: AcceptanceStatus
    input_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    proposal_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    decision: PlanAcceptanceDecisionV2 | None = None
    diagnostics: list[str] = Field(default_factory=list)
    idempotent: bool = False
    session_id: str | None = None
    sessions: list[dict] = Field(default_factory=list)


class PlanAcceptanceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _read_store() -> tuple[PlanAcceptanceStoreV2 | None, bool]:
    target = Path(os.fspath(PLAN_ACCEPTANCE_FILE))
    if not target.is_file():
        return PlanAcceptanceStoreV2(), False
    try:
        return PlanAcceptanceStoreV2.model_validate_json(
            target.read_text(encoding="utf-8")
        ), False
    except (OSError, UnicodeDecodeError, ValueError, ValidationError):
        return None, True


def _write_store(store: PlanAcceptanceStoreV2) -> None:
    ensure_directories()
    target = Path(os.fspath(PLAN_ACCEPTANCE_FILE))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(store.model_dump_json(indent=2), encoding="utf-8")
        PlanAcceptanceStoreV2.model_validate_json(
            temporary.read_text(encoding="utf-8")
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _current_state(proposal_state: PlanProposalStateV2, session_id: str | None = None) -> PlanAcceptanceStateV2:
    store, corrupt = _read_store()
    if corrupt or store is None:
        return PlanAcceptanceStateV2(
            status="corrupt", diagnostics=["plan_acceptance_store_invalid"]
        )
    current_proposal_fingerprint = (
        proposal_fingerprint(proposal_state.proposal)
        if proposal_state.status == "available" and proposal_state.proposal is not None
        else None
    )
    scoped = [item for item in store.decisions if item.session_id == session_id]
    session = next((item for item in proposal_state.proposal.sessions if item.session_id == session_id), None) if proposal_state.proposal and session_id else None
    if session_id is not None and session is None:
        raise PlanAcceptanceError("session_not_found", "Séance identifiée introuvable.")
    if not scoped:
        return PlanAcceptanceStateV2(
            status="missing",
            input_fingerprint=proposal_state.input_fingerprint,
            proposal_fingerprint=current_proposal_fingerprint,
            session_id=session_id,
        )
    latest = scoped[-1]
    latest_position = max(index for index, item in enumerate(store.decisions) if item == latest)
    withdrawn = next((item for item in reversed(store.decisions[latest_position + 1:])
                      if item.session_id is None and item.decision in {"refused", "revision_requested"}
                      and item.input_fingerprint == proposal_state.input_fingerprint), None) if session_id else None
    if withdrawn is not None:
        return PlanAcceptanceStateV2(status=withdrawn.decision, input_fingerprint=proposal_state.input_fingerprint,
            proposal_fingerprint=current_proposal_fingerprint, session_id=session_id, decision=withdrawn,
            diagnostics=["session_acceptance_withdrawn_by_plan_decision"])
    if (
        proposal_state.status != "available"
        or current_proposal_fingerprint is None
        or latest.input_fingerprint != proposal_state.input_fingerprint
        or (latest.session_fingerprint != session_fingerprint(session) if session is not None
            else latest.proposal_fingerprint != current_proposal_fingerprint)
    ):
        return PlanAcceptanceStateV2(
            status="stale",
            input_fingerprint=proposal_state.input_fingerprint,
            proposal_fingerprint=current_proposal_fingerprint,
            decision=latest,
            diagnostics=["plan_or_generation_input_changed"],
            session_id=session_id,
        )
    return PlanAcceptanceStateV2(
        status=latest.decision,
        input_fingerprint=proposal_state.input_fingerprint,
        proposal_fingerprint=current_proposal_fingerprint,
        decision=latest,
        session_id=session_id,
    )


def get_plan_acceptance_state(
    user_id: str, *, proposal_state: PlanProposalStateV2 | None = None, session_id: str | None = None
) -> PlanAcceptanceStateV2:
    """Read local proposal and decision state. Never generates a plan."""
    with user_context(user_id):
        current = proposal_state or get_plan_proposal_state(user_id)
        state = _current_state(current, session_id)
        if session_id is None and current.proposal is not None and current.status == "available":
            states = [_current_state(current, item.session_id) for item in current.proposal.sessions if item.session_id]
            state.sessions = [dict(session_id=item.session_id, status=item.status,
                                  session_fingerprint=session_fingerprint(session))
                              for item, session in zip(states, current.proposal.sessions)]
            if states and state.status != "corrupt":
                accepted = sum(item.status == "accepted" for item in states)
                if accepted and state.status != "accepted":
                    state.status = "accepted" if accepted == len(states) else "partially_accepted"
        return state


def record_plan_acceptance_decision(
    user_id: str,
    *,
    decision: Decision,
    input_fingerprint: str,
    expected_proposal_fingerprint: str,
    feedback: str | None = None,
    proposal_state: PlanProposalStateV2 | None = None,
    session_id: str | None = None,
) -> PlanAcceptanceStateV2:
    with user_context(user_id):
        current = proposal_state or get_plan_proposal_state(user_id)
        if current.status != "available" or current.proposal is None:
            raise PlanAcceptanceError(
                f"proposal_{current.status}",
                "La proposition actuelle n'est pas disponible pour une décision.",
            )
        actual_proposal_fingerprint = proposal_fingerprint(current.proposal)
        if input_fingerprint != current.input_fingerprint:
            raise PlanAcceptanceError(
                "input_fingerprint_mismatch", "Les informations du plan ont changé."
            )
        if expected_proposal_fingerprint != actual_proposal_fingerprint:
            raise PlanAcceptanceError(
                "proposal_fingerprint_mismatch", "La proposition affichée n'est plus actuelle."
            )
        session = next((item for item in current.proposal.sessions if item.session_id == session_id), None) if session_id else None
        if session_id is not None and session is None:
            raise PlanAcceptanceError("session_not_found", "Séance identifiée introuvable.")
        try:
            value = PlanAcceptanceDecisionV2(
                decision=decision,
                input_fingerprint=current.input_fingerprint,
                proposal_fingerprint=actual_proposal_fingerprint,
                feedback=feedback,
                decided_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                session_id=session_id,
                session_fingerprint=session_fingerprint(session) if session is not None else None,
            )
        except ValidationError as error:
            raise PlanAcceptanceError("decision_invalid", str(error)) from error
        with _WRITE_LOCK:
            store, corrupt = _read_store()
            if corrupt or store is None:
                raise PlanAcceptanceError(
                    "acceptance_store_corrupt",
                    "Le journal de décision est corrompu et ne sera pas écrasé.",
                )
            latest_scoped = next((item for item in reversed(store.decisions) if item.session_id == session_id), None)
            identical = None
            if latest_scoped is not None:
                same_facts = (latest_scoped.input_fingerprint == value.input_fingerprint
                    and (latest_scoped.session_fingerprint == value.session_fingerprint if session_id else latest_scoped.proposal_fingerprint == value.proposal_fingerprint)
                    and latest_scoped.decision == value.decision and latest_scoped.feedback == value.feedback)
                if same_facts and _current_state(current, session_id).status == decision:
                    identical = latest_scoped
            if identical is not None:
                return PlanAcceptanceStateV2(
                    status=identical.decision,
                    input_fingerprint=current.input_fingerprint,
                    proposal_fingerprint=actual_proposal_fingerprint,
                    decision=identical,
                    idempotent=True,
                    session_id=session_id,
                )
            _write_store(
                PlanAcceptanceStoreV2(decisions=[*store.decisions, value][-100:])
            )
        return PlanAcceptanceStateV2(
            status=value.decision,
            input_fingerprint=current.input_fingerprint,
            proposal_fingerprint=actual_proposal_fingerprint,
            decision=value,
            session_id=session_id,
        )
