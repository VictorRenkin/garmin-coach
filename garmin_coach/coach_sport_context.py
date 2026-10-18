"""Compact, deterministic multisport evidence for the conversation coach."""
from __future__ import annotations

from datetime import date
from typing import Any

from .athlete_state import athlete_state_for_ai, build_athlete_state
from .config import user_context
from .data_confidence import build_data_confidence
from .goal import goal_context_for_legacy_confidence
from .local_activity_history import local_history_scope
from .runtime_context import load_motionest_inputs_v1
from .training_history_summary import build_training_history_summary


def build_coach_sport_context(
    user_id: str, *, reference_date: date | None = None
) -> dict[str, Any]:
    """Read only this user's local evidence; never sync, call AI or change a plan.

    Retain the import's frozen reference for reproducible datasets, as the
    canonical runtime does. Unknown metrics remain null, not zero estimates.
    The projection excludes raw activity names, identifiers and coordinates.
    """
    with user_context(user_id):
        scope_reference = local_history_scope().get("reference_date")
        reference = reference_date or (
            date.fromisoformat(scope_reference) if scope_reference else date.today()
        )
        inputs = load_motionest_inputs_v1(user_id)
        summary = build_training_history_summary(reference_date=reference)
        confidence = build_data_confidence(
            summary,
            objective=(goal_context_for_legacy_confidence(inputs.goal)
                       if inputs.goal is not None else None),
            reference_date=reference,
        )
        athlete = build_athlete_state(summary, confidence, reference_date=reference)
        history = summary.model_dump(mode="json")
        return {
            "status": "available",
            "athlete_state": athlete_state_for_ai(athlete),
            "training_history_summary": {
                key: history[key]
                for key in (
                    "schema_version", "as_of", "source", "scope", "scope_cutoff_date",
                    "coverage", "windows", "sports", "sport_classification", "trends",
                )
            },
        }
