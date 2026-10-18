"""Pure SDK serialization for sequential blocks, without network or storage."""
from __future__ import annotations

from typing import Any

from garminconnect.workout import create_interval_step, create_recovery_step, create_repeat_group

from .session_blocks import SessionBlock


HR_ZONE_TARGET = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4}


def _target(step: Any, zone: int | None) -> Any:
    # Qualitative instructions remain local; they never imply invented HR targets.
    if zone is not None:
        step.targetType = dict(HR_ZONE_TARGET)
        step.zoneNumber = zone
    return step


def build_block_steps(blocks: list[SessionBlock], start_order: int) -> list[Any]:
    steps: list[Any] = []
    order = start_order
    for block in blocks:
        effort = round(block.effort_minutes * 60)
        recovery = round(block.recovery_minutes * 60)
        if block.repetitions > 1:
            children = [_target(create_interval_step(effort, step_order=1), block.effort_hr_zone)]
            if recovery:
                children.append(_target(create_recovery_step(recovery, step_order=2), block.recovery_hr_zone))
            steps.append(create_repeat_group(block.repetitions, children, step_order=order))
            order += 1
        else:
            steps.append(_target(create_interval_step(effort, step_order=order), block.effort_hr_zone))
            order += 1
            if recovery:
                steps.append(_target(create_recovery_step(recovery, step_order=order), block.recovery_hr_zone))
                order += 1
    return steps
