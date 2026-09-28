import json
from datetime import date

from garmin_coach.garmin_workouts import build_running_workout, remove_future_sessions
from garmin_coach.training_cycle import PlannedSession


def session() -> PlannedSession:
    return PlannedSession(
        date="2026-07-30",
        titre="Fractions cardiaques",
        type="intervalles",
        objectif="Travailler une intensité soutenue et contrôlée.",
        raison="Développer progressivement la capacité aérobie.",
        echauffement="Progressif de la zone 1 à la zone 2.",
        bloc_principal="Quatre répétitions de trois minutes.",
        retour_au_calme="Dix minutes très faciles.",
        duree_estimee_minutes=40,
        distance_estimee_km=5.6,
        consigne_intensite="Atteindre progressivement la zone 4 sans sprinter.",
        echauffement_minutes=10,
        repetitions_bloc=4,
        effort_minutes=3,
        recuperation_minutes=2,
        retour_au_calme_minutes=10,
        echauffement_zone_fc=1,
        echauffement_zone_fc_fin=2,
        bloc_zone_fc=4,
        recuperation_zone_fc=1,
        retour_au_calme_zone_fc=1,
    )


def test_workout_targets_every_phase_by_hr_zone() -> None:
    workout = build_running_workout(session())
    steps = workout.workoutSegments[0].workoutSteps

    assert [steps[0].zoneNumber, steps[1].zoneNumber] == [1, 2]
    assert steps[2].workoutSteps[0].zoneNumber == 4
    assert steps[2].workoutSteps[1].zoneNumber == 1
    assert steps[3].zoneNumber == 1


class FakeGarth:
    def __init__(self):
        self.deleted = []

    def delete(self, service, path, api):
        self.deleted.append(path)


class FakeCalendarClient:
    def __init__(self):
        self.garth = FakeGarth()

    def connectapi(self, path):
        return {
            "calendarItems": [
                {"id": 900, "itemType": "workout", "workoutId": 800}
            ]
        }


def test_future_workout_is_unscheduled_then_deleted(tmp_path) -> None:
    target = tmp_path / "plan.json"
    target.write_text(
        json.dumps(
            {
                "seances": [
                    {
                        **session().model_dump(mode="json"),
                        "garmin": {"workout_id": 800, "scheduled": True},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    client = FakeCalendarClient()

    assert remove_future_sessions(client, target, today=date(2026, 7, 23)) == 1
    assert client.garth.deleted == [
        "/workout-service/schedule/900",
        "/workout-service/workout/800",
    ]
    assert "garmin" not in json.loads(target.read_text(encoding="utf-8"))["seances"][0]


def test_only_selected_future_workouts_are_removed(tmp_path) -> None:
    target = tmp_path / "plan.json"
    first = {**session().model_dump(mode="json"), "garmin": {"workout_id": 800}}
    second = {
        **session().model_copy(update={"date": "2026-07-31", "titre": "À garder"}).model_dump(mode="json"),
        "garmin": {"workout_id": 801},
    }
    target.write_text(
        json.dumps({"seances": [first, second]}), encoding="utf-8"
    )
    client = FakeCalendarClient()

    removed = remove_future_sessions(
        client, target, today=date(2026, 7, 23), session_indexes={0}
    )

    saved = json.loads(target.read_text(encoding="utf-8"))["seances"]
    assert removed == 1
    assert "garmin" not in saved[0]
    assert saved[1]["garmin"]["workout_id"] == 801
