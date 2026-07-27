import json

from garmin_coach.garmin_workouts import build_running_workout, publish_plan
from garmin_coach.training_cycle import PlannedSession


def make_session(**overrides) -> PlannedSession:
    values = {
        "date": "2026-07-28",
        "titre": "Intervalles courts",
        "type": "intervalles",
        "objectif": "Développer une allure soutenue contrôlée.",
        "raison": "Introduire un stimulus rapide sans volume excessif.",
        "echauffement": "Footing facile et mobilisation.",
        "bloc_principal": "4 répétitions de 3 minutes.",
        "retour_au_calme": "Footing très facile.",
        "duree_estimee_minutes": 35,
        "distance_estimee_km": 5.5,
        "consigne_intensite": "Soutenu mais régulier, sans sprint.",
        "echauffement_minutes": 10,
        "repetitions_bloc": 4,
        "effort_minutes": 3,
        "recuperation_minutes": 2,
        "retour_au_calme_minutes": 5,
    }
    values.update(overrides)
    return PlannedSession(**values)


def test_interval_session_becomes_structured_garmin_workout() -> None:
    workout = build_running_workout(make_session())

    assert workout.workoutName == "GC 2807 INT"
    assert workout.estimatedDurationInSecs == 2100
    assert len(workout.workoutSegments[0].workoutSteps) == 3
    repeat = workout.workoutSegments[0].workoutSteps[1]
    assert repeat.numberOfIterations == 4


class FakeGarmin:
    def __init__(self) -> None:
        self.uploads = 0
        self.scheduled: list[tuple[int, str]] = []

    def upload_running_workout(self, workout):
        self.uploads += 1
        return {"workoutId": 100 + self.uploads}

    def schedule_workout(self, workout_id, scheduled_date):
        self.scheduled.append((workout_id, scheduled_date))
        return {"ok": True}


def test_publish_plan_is_idempotent(tmp_path) -> None:
    target = tmp_path / "plan.json"
    target.write_text(
        json.dumps({"seances": [make_session().model_dump(mode="json")]}),
        encoding="utf-8",
    )
    client = FakeGarmin()

    assert publish_plan(client, target) == 1
    assert publish_plan(client, target) == 0
    assert client.uploads == 1
    assert client.scheduled == [(101, "2026-07-28")]
