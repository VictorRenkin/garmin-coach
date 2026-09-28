from datetime import date

from garmin_coach import training_cycle


def test_monday_of_week() -> None:
    assert training_cycle.monday_of(date(2026, 7, 22)) == date(2026, 7, 20)


def test_session_offsets_respect_declared_available_days() -> None:
    assert training_cycle._session_offsets(3, ["tue", "thu", "sun"]) == [1, 3, 6]
    assert training_cycle._session_offsets(2, ["mon", "wed", "fri", "sun"]) == [0, 6]


def test_planned_session_is_matched_by_date(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(training_cycle, "PLANS_DIR", tmp_path)
    (tmp_path / "2026-07-20.json").write_text(
        '{"seances":[{"date":"2026-07-22","objectif":"Footing facile"}]}',
        encoding="utf-8",
    )

    session = training_cycle.find_planned_session(
        {"startTimeLocal": "2026-07-22 18:00:00"}
    )

    assert session is not None
    assert session["objectif"] == "Footing facile"


def test_weekly_cycle_only_triggers_sunday_or_monday(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(training_cycle, "PLANS_DIR", tmp_path)
    settings = training_cycle.Settings(None, None, 300, "sk-test")

    assert training_cycle.maybe_review_and_plan(settings, date(2026, 7, 22)) is None


def test_week_context_computes_adherence_without_ai(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(training_cycle, "PLANS_DIR", tmp_path / "plans")
    (tmp_path / "plans").mkdir()
    monkeypatch.setattr(
        training_cycle,
        "load_activities",
        lambda: [
            {"activityId": 1, "startTimeLocal": "2026-07-21 08:00:00"},
            {"activityId": 2, "startTimeLocal": "2026-07-23 08:00:00"},
        ],
    )
    monkeypatch.setattr(
        training_cycle,
        "list_messages",
        lambda: [
            {"role": "assistant", "content": "Comment te sens-tu ?"},
            {"role": "user", "content": "Effort difficile et jambes lourdes."},
        ],
    )
    (tmp_path / "plans" / "2026-07-20.json").write_text(
        '{"seances":['
        '{"date":"2026-07-21"},'
        '{"date":"2026-07-23"},'
        '{"date":"2026-07-26"}'
        ']}',
        encoding="utf-8",
    )

    context = training_cycle._context_for_week(date(2026, 7, 20))

    assert context["semaine_evaluee"]["adherence_calculee"] == "2/3"
    assert context["retours_conversation"][-1]["content"] == (
        "Effort difficile et jambes lourdes."
    )


def test_moved_session_is_found_outside_file_week(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(training_cycle, "PLANS_DIR", tmp_path)
    (tmp_path / "2026-07-27.json").write_text(
        '{"seances":[{"date":"2026-07-23","objectif":"Endurance facile"}]}',
        encoding="utf-8",
    )

    session = training_cycle.find_planned_session(
        {"startTimeLocal": "2026-07-23 18:03:30"}
    )

    assert session is not None
    assert session["objectif"] == "Endurance facile"


def test_workout_id_takes_priority_over_same_day_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(training_cycle, "PLANS_DIR", tmp_path)
    (tmp_path / "2026-07-20.json").write_text(
        '{"seances":['
        '{"date":"2026-07-23","titre":"Autre course","garmin":{"workout_id":111}},'
        '{"date":"2026-07-25","titre":"Programme Garmin","garmin":{"workout_id":222}}'
        ']}',
        encoding="utf-8",
    )

    session = training_cycle.find_planned_session(
        {"startTimeLocal": "2026-07-23 18:03:30", "workoutId": 222}
    )

    assert session is not None
    assert session["titre"] == "Programme Garmin"


def test_completed_plan_triggers_next_plan_immediately(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(training_cycle, "PLANS_DIR", tmp_path)
    monkeypatch.setattr(
        training_cycle,
        "load_activities",
        lambda: [
            {"startTimeLocal": "2026-07-21 08:00:00"},
            {"startTimeLocal": "2026-07-23 08:00:00"},
            {"startTimeLocal": "2026-07-24 08:00:00"},
        ],
    )
    (tmp_path / "2026-07-20.json").write_text(
        '{"seances":['
        '{"date":"2026-07-21"},'
        '{"date":"2026-07-23"},'
        '{"date":"2026-07-24"}'
        ']}',
        encoding="utf-8",
    )
    expected = tmp_path / "2026-07-27.json"
    monkeypatch.setattr(
        training_cycle, "review_and_plan", lambda week, settings: expected
    )
    settings = training_cycle.Settings(None, None, 300, "sk-test")

    assert training_cycle.maybe_review_and_plan(settings, date(2026, 7, 24)) == expected


def test_incomplete_plan_does_not_trigger_next_plan(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(training_cycle, "PLANS_DIR", tmp_path)
    monkeypatch.setattr(
        training_cycle,
        "load_activities",
        lambda: [{"startTimeLocal": "2026-07-21 08:00:00"}],
    )
    (tmp_path / "2026-07-20.json").write_text(
        '{"seances":['
        '{"date":"2026-07-21"},'
        '{"date":"2026-07-23"},'
        '{"date":"2026-07-25","statut":"skippee"}'
        ']}',
        encoding="utf-8",
    )
    settings = training_cycle.Settings(None, None, 300, "sk-test")

    assert training_cycle.maybe_review_and_plan(settings, date(2026, 7, 24)) is None


def test_past_incomplete_session_is_marked_skipped() -> None:
    sessions = [
        {"date": "2026-07-21"},
        {"date": "2026-07-23"},
        {"date": "2026-07-25"},
    ]
    activities = [{"startTimeLocal": "2026-07-21 08:00:00"}]

    assert training_cycle.session_statuses(
        sessions, activities, date(2026, 7, 26)
    ) == ["terminee", "a_venir", "a_venir"]


def test_manually_completed_session_counts_as_finished() -> None:
    sessions = [
        {"date": "2026-07-21", "statut": "terminee", "distance_realisee_km": 7},
        {"date": "2026-07-23", "statut": "terminee", "distance_realisee_km": 5},
        {"date": "2026-07-25", "statut": "terminee", "distance_realisee_km": 5},
    ]

    assert training_cycle.session_statuses(sessions, [], date(2026, 7, 26)) == [
        "terminee",
        "terminee",
        "terminee",
    ]


def test_finished_cycle_with_skipped_session_triggers_next_plan(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(training_cycle, "PLANS_DIR", tmp_path)
    monkeypatch.setattr(
        training_cycle,
        "load_activities",
        lambda: [
            {"startTimeLocal": "2026-07-21 08:00:00"},
            {"startTimeLocal": "2026-07-23 08:00:00"},
        ],
    )
    (tmp_path / "2026-07-20.json").write_text(
        '{"seances":['
        '{"date":"2026-07-21"},'
        '{"date":"2026-07-23"},'
        '{"date":"2026-07-25","statut":"skippee"}'
        ']}',
        encoding="utf-8",
    )
    expected = tmp_path / "2026-07-27.json"
    monkeypatch.setattr(
        training_cycle, "review_and_plan", lambda week, settings: expected
    )
    settings = training_cycle.Settings(None, None, 300, "sk-test")

    assert training_cycle.maybe_review_and_plan(settings, date(2026, 7, 26)) == expected
