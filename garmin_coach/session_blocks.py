"""Optional sequential blocks shared by the existing V2 and legacy pipelines.

Minutes remain the public unit; executable precision is one second. Recovery is
included after every effort, including the last repetition, as in the simple DTO.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .session_limits import MAX_BLOCK_REPETITIONS, MAX_EFFORT_MINUTES, MAX_SESSION_MINUTES


class SessionBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    effort_minutes: float = Field(gt=0, le=MAX_EFFORT_MINUTES)
    recovery_minutes: float = Field(default=0, ge=0, le=120)
    repetitions: int = Field(default=1, ge=1, le=MAX_BLOCK_REPETITIONS)
    intensity_instruction: str = Field(min_length=1, max_length=400)
    effort_hr_zone: int | None = Field(default=None, ge=1, le=5)
    recovery_hr_zone: int | None = Field(default=None, ge=1, le=5)

    @field_validator("effort_minutes", "recovery_minutes", "repetitions", "effort_hr_zone", "recovery_hr_zone", mode="before")
    @classmethod
    def reject_boolean_measurements(cls, value):
        if isinstance(value, bool):
            raise ValueError("Une valeur numérique de bloc ne peut pas être un booléen.")
        return value

    @model_validator(mode="after")
    def recovery_target_requires_recovery(self) -> "SessionBlock":
        if self.recovery_minutes == 0 and self.recovery_hr_zone is not None:
            raise ValueError("Une cible de récupération nécessite une récupération positive.")
        return self

    @field_validator("effort_minutes", "recovery_minutes")
    @classmethod
    def whole_seconds(cls, value: float) -> float:
        if abs(value * 60 - round(value * 60)) > 1e-7:
            raise ValueError("Les blocs doivent être représentables en secondes entières.")
        return value

    @field_validator("intensity_instruction")
    @classmethod
    def normalized_instruction(cls, value: str) -> str:
        if not value.strip() or " ".join(value.split()) != value:
            raise ValueError("La consigne du bloc doit être un texte normalisé.")
        return value


def blocks_duration_seconds(blocks: list[SessionBlock], warmup_minutes: int, cooldown_minutes: int) -> int:
    """Validate the existing protective ceilings across the whole session."""
    if sum(block.repetitions for block in blocks) > MAX_BLOCK_REPETITIONS:
        raise ValueError("Le cumul des répétitions dépasse le plafond de la séance.")
    seconds = (warmup_minutes + cooldown_minutes) * 60 + sum(
        block.repetitions * (round(block.effort_minutes * 60) + round(block.recovery_minutes * 60))
        for block in blocks
    )
    if seconds > MAX_SESSION_MINUTES * 60:
        raise ValueError("La durée des blocs dépasse le plafond de la séance.")
    return seconds


def validate_blocks_duration(blocks: list[SessionBlock], warmup_minutes: int, cooldown_minutes: int, advertised_minutes: int) -> int:
    seconds = blocks_duration_seconds(blocks, warmup_minutes, cooldown_minutes)
    # Advertised durations use whole minutes. At most half a minute of rounding.
    if abs(seconds - advertised_minutes * 60) > 30:
        raise ValueError("La durée annoncée ne correspond pas aux blocs de la séance.")
    return seconds
