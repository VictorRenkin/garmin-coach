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
)


SCHEMA_VERSION = 2
Decision = Literal["accepted", "refused", "revision_requested"]
AcceptanceStatus = Literal[
    "missing", "accepted", "refused", "revision_requested", "stale", "corrupt"
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


def _current_state(proposal_state: PlanProposalStateV2) -> PlanAcceptanceStateV2:
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
    if not store.decisions:
        return PlanAcceptanceStateV2(
            status="missing",
            input_fingerprint=proposal_state.input_fingerprint,
            proposal_fingerprint=current_proposal_fingerprint,
        )
    latest = store.decisions[-1]
    if (
        proposal_state.status != "available"
        or current_proposal_fingerprint is None
        or latest.input_fingerprint != proposal_state.input_fingerprint
        or latest.proposal_fingerprint != current_proposal_fingerprint
    ):
        return PlanAcceptanceStateV2(
            status="stale",
            input_fingerprint=proposal_state.input_fingerprint,
            proposal_fingerprint=current_proposal_fingerprint,
            decision=latest,
            diagnostics=["plan_or_generation_input_changed"],
        )
    return PlanAcceptanceStateV2(
        status=latest.decision,
        input_fingerprint=proposal_state.input_fingerprint,
        proposal_fingerprint=current_proposal_fingerprint,
        decision=latest,
    )


def get_plan_acceptance_state(
    user_id: str, *, proposal_state: PlanProposalStateV2 | None = None
) -> PlanAcceptanceStateV2:
    """Read local proposal and decision state. Never generates a plan."""
    with user_context(user_id):
        current = proposal_state or get_plan_proposal_state(user_id)
        return _current_state(current)


def record_plan_acceptance_decision(
    user_id: str,
    *,
    decision: Decision,
    input_fingerprint: str,
    expected_proposal_fingerprint: str,
    feedback: str | None = None,
    proposal_state: PlanProposalStateV2 | None = None,
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
        try:
            value = PlanAcceptanceDecisionV2(
                decision=decision,
                input_fingerprint=current.input_fingerprint,
                proposal_fingerprint=actual_proposal_fingerprint,
                feedback=feedback,
                decided_at=datetime.now().astimezone().isoformat(timespec="seconds"),
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
            identical = next(
                (
                    item
                    for item in reversed(store.decisions)
                    if item.input_fingerprint == value.input_fingerprint
                    and item.proposal_fingerprint == value.proposal_fingerprint
                    and item.decision == value.decision
                    and item.feedback == value.feedback
                ),
                None,
            )
            if identical is not None:
                return PlanAcceptanceStateV2(
                    status=identical.decision,
                    input_fingerprint=current.input_fingerprint,
                    proposal_fingerprint=actual_proposal_fingerprint,
                    decision=identical,
                    idempotent=True,
                )
            _write_store(
                PlanAcceptanceStoreV2(decisions=[*store.decisions, value][-100:])
            )
        return PlanAcceptanceStateV2(
            status=value.decision,
            input_fingerprint=current.input_fingerprint,
            proposal_fingerprint=actual_proposal_fingerprint,
            decision=value,
        )
