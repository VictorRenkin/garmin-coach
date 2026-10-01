"""Runtime orchestration for the deterministic Motionest V1 contracts.

This module is deliberately a thin local boundary: it migrates declared legacy
inputs, validates their versioned persistence, then calls the existing V1
builders in dependency order. It performs no Garmin, AI, or publication call.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .athlete_state import build_athlete_state
from .availability import AvailabilityV1, availability_from_legacy_objective
from .config import (
    MOTIONEST_INPUTS_FILE,
    PROFILE_FILE,
    ensure_directories,
    user_context,
)
from .data_confidence import build_data_confidence
from .goal import GoalV1, goal_context_for_legacy_confidence, goal_from_legacy_objective
from .goal_evidence import GoalEvidenceV1, build_goal_evidence_v1
from .local_activity_history import local_history_scope
from .motionest_context import MotionestContextV1, build_motionest_context_v1
from .subjective_context import (
    SubjectiveContextV1,
    build_subjective_context_v1,
    subjective_context_from_legacy_objective,
)
from .training_history_summary import build_training_history_summary
from .training_pattern import (
    TrainingPatternEvidenceV1,
    build_training_pattern_evidence_v1,
    collect_local_training_pattern_inputs,
)


SCHEMA_VERSION = 1
# One re-entrant process lock protects both the migration decision and its
# atomic replacement. A concurrent explicit V1 save therefore always wins by
# order of execution and can never be overwritten from a stale legacy read.
_WRITE_LOCK = threading.RLock()
_USER_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeInputDiagnostic(StrictModel):
    code: str
    contract: Literal["store", "profile", "goal", "availability", "subjective_context"]
    field: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MotionestInputsV1(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    source: Literal["legacy_profile", "v1"]
    legacy_objective_digest: str | None = None
    goal: GoalV1 | None
    availability: AvailabilityV1 | None
    subjective_context: SubjectiveContextV1
    migration_diagnostics: list[RuntimeInputDiagnostic] = Field(default_factory=list)


class RuntimeMotionestContextV1(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    reference_date: str
    inputs_source: Literal["legacy_profile", "v1", "safe_fallback"]
    input_diagnostics: list[RuntimeInputDiagnostic]
    context: MotionestContextV1
    goal_evidence: GoalEvidenceV1 | None
    training_pattern: TrainingPatternEvidenceV1


def _unknown_subjective_context() -> SubjectiveContextV1:
    return build_subjective_context_v1(affecting_training=None)


def _validate_user_id(user_id: str) -> None:
    if (
        not user_id
        or len(user_id) > 80
        or any(character not in _USER_ID_CHARS for character in user_id)
    ):
        raise ValueError("Identifiant utilisateur Motionest invalide.")


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _profile_objective() -> tuple[dict[str, Any] | None, list[RuntimeInputDiagnostic]]:
    target = Path(os.fspath(PROFILE_FILE))
    if not target.is_file():
        return None, [
            RuntimeInputDiagnostic(code="legacy_profile_missing", contract="profile")
        ]
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, [
            RuntimeInputDiagnostic(code="legacy_profile_invalid", contract="profile")
        ]
    objective = payload.get("objective") if isinstance(payload, dict) else None
    if not isinstance(objective, dict):
        return None, [
            RuntimeInputDiagnostic(
                code="legacy_objective_missing", contract="profile", field="objective"
            )
        ]
    return objective, []


def _adapt_legacy_objective(objective: dict[str, Any]) -> MotionestInputsV1:
    diagnostics: list[RuntimeInputDiagnostic] = []
    if str(objective.get("title") or "").strip():
        diagnostics.append(
            RuntimeInputDiagnostic(
                code="legacy_title_unmapped", contract="goal", field="title"
            )
        )
    goal_diagnostics: list[Any] = []
    try:
        goal = goal_from_legacy_objective(
            objective, diagnostics=goal_diagnostics
        )
    except (TypeError, ValueError, ValidationError):
        goal = None
        diagnostics.append(
            RuntimeInputDiagnostic(
                code="legacy_goal_invalid", contract="goal", field="goalType"
            )
        )
    diagnostics.extend(
        RuntimeInputDiagnostic(
            code=item.code, contract="goal", field=item.field
        )
        for item in goal_diagnostics
    )

    try:
        availability_result = availability_from_legacy_objective(objective)
        availability = availability_result.availability
        diagnostics.extend(
            RuntimeInputDiagnostic(
                code=item.code,
                contract="availability",
                field=item.field,
                details=item.details,
            )
            for item in availability_result.diagnostics
        )
    except (TypeError, ValueError, ValidationError):
        availability = None
        diagnostics.append(
            RuntimeInputDiagnostic(
                code="legacy_availability_invalid", contract="availability"
            )
        )

    try:
        subjective_result = subjective_context_from_legacy_objective(objective)
        subjective = subjective_result.context
        diagnostics.extend(
            RuntimeInputDiagnostic(
                code=item.code,
                contract="subjective_context",
                field=item.field,
            )
            for item in subjective_result.diagnostics
        )
    except (TypeError, ValueError, ValidationError):
        subjective = _unknown_subjective_context()
        diagnostics.append(
            RuntimeInputDiagnostic(
                code="legacy_subjective_context_invalid",
                contract="subjective_context",
            )
        )

    return MotionestInputsV1(
        source="legacy_profile",
        legacy_objective_digest=_canonical_digest(objective),
        goal=goal,
        availability=availability,
        subjective_context=subjective,
        migration_diagnostics=sorted(
            diagnostics,
            key=lambda item: (item.contract, item.field or "", item.code),
        ),
    )


def _atomic_write_inputs(inputs: MotionestInputsV1) -> None:
    ensure_directories()
    target = Path(os.fspath(MOTIONEST_INPUTS_FILE))
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(inputs.model_dump(mode="json"), ensure_ascii=False, indent=2)
    with _WRITE_LOCK:
        try:
            temporary.write_text(encoded, encoding="utf-8")
            MotionestInputsV1.model_validate_json(
                temporary.read_text(encoding="utf-8")
            )
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def save_motionest_inputs_v1(
    user_id: str,
    *,
    goal: GoalV1 | None,
    availability: AvailabilityV1 | None,
    subjective_context: SubjectiveContextV1,
) -> MotionestInputsV1:
    """Persist explicit V1 declarations in exactly one user's workspace."""
    _validate_user_id(user_id)
    inputs = MotionestInputsV1(
        source="v1",
        goal=goal,
        availability=availability,
        subjective_context=subjective_context,
    )
    with user_context(user_id):
        _atomic_write_inputs(inputs)
    return inputs


def _load_or_migrate_inputs() -> tuple[MotionestInputsV1, str]:
    """Serialize the legacy bridge's read/compare/write transaction."""
    with _WRITE_LOCK:
        return _load_or_migrate_inputs_unlocked()


def _load_or_migrate_inputs_unlocked() -> tuple[MotionestInputsV1, str]:
    """Load explicit V1 first, otherwise migrate legacy inputs idempotently."""
    target = Path(os.fspath(MOTIONEST_INPUTS_FILE))
    objective, profile_diagnostics = _profile_objective()
    if target.is_file():
        try:
            stored = MotionestInputsV1.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, ValidationError, ValueError):
            fallback = MotionestInputsV1(
                source="v1",
                goal=None,
                availability=None,
                subjective_context=_unknown_subjective_context(),
                migration_diagnostics=[
                    RuntimeInputDiagnostic(
                        code="motionest_inputs_store_invalid", contract="store"
                    ),
                    *profile_diagnostics,
                ],
            )
            return fallback, "safe_fallback"
        if stored.source == "legacy_profile":
            if objective is None and stored.legacy_objective_digest is not None:
                fallback = MotionestInputsV1(
                    source="v1",
                    goal=None,
                    availability=None,
                    subjective_context=_unknown_subjective_context(),
                    migration_diagnostics=profile_diagnostics,
                )
                return fallback, "safe_fallback"
            if objective is not None:
                current_digest = _canonical_digest(objective)
                if stored.legacy_objective_digest != current_digest:
                    migrated = _adapt_legacy_objective(objective)
                    _atomic_write_inputs(migrated)
                    return migrated, "legacy_profile"
        return stored, stored.source

    if objective is None:
        inputs = MotionestInputsV1(
            source="legacy_profile",
            goal=None,
            availability=None,
            subjective_context=_unknown_subjective_context(),
            migration_diagnostics=profile_diagnostics,
        )
    else:
        inputs = _adapt_legacy_objective(objective)
    _atomic_write_inputs(inputs)
    return inputs, "legacy_profile"


def build_runtime_motionest_context(
    user_id: str, *, reference_date: date | None = None
) -> RuntimeMotionestContextV1:
    """Build the current user's integrated V1 context from local inputs only.

    No computed context is persisted. Every call rereads declarations and local
    history, preventing a stale snapshot from being presented as current.
    """
    _validate_user_id(user_id)
    with user_context(user_id):
        scope_reference = local_history_scope().get("reference_date")
        resolved_reference = reference_date or (
            date.fromisoformat(scope_reference)
            if scope_reference
            else date.today()
        )
        inputs, inputs_source = _load_or_migrate_inputs()
        summary = build_training_history_summary(reference_date=resolved_reference)
        confidence = build_data_confidence(
            summary,
            objective=(
                goal_context_for_legacy_confidence(inputs.goal)
                if inputs.goal is not None
                else None
            ),
            reference_date=resolved_reference,
        )
        athlete = build_athlete_state(
            summary, confidence, reference_date=resolved_reference
        )
        context = build_motionest_context_v1(
            goal=inputs.goal,
            availability=inputs.availability,
            subjective_context=inputs.subjective_context,
            training_history_summary=summary,
            data_confidence=confidence,
            athlete_state=athlete,
            reference_date=resolved_reference,
        )
        goal_evidence = (
            build_goal_evidence_v1(
                inputs.goal,
                athlete,
                summary,
                confidence,
                reference_date=resolved_reference,
            )
            if inputs.goal is not None
            else None
        )
        training_pattern = build_training_pattern_evidence_v1(
            summary,
            confidence,
            collect_local_training_pattern_inputs(),
            reference_date=resolved_reference,
        )
    return RuntimeMotionestContextV1(
        reference_date=resolved_reference.isoformat(),
        inputs_source=inputs_source,
        input_diagnostics=inputs.migration_diagnostics,
        context=context,
        goal_evidence=goal_evidence,
        training_pattern=training_pattern,
    )
