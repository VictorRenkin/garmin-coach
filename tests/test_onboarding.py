from garmin_coach import onboarding


class FakeGarmin:
    def __init__(self) -> None:
        self.activity_calls = 0
        self.workout_calls = 0

    def get_activities(self, start, limit):
        self.activity_calls += 1
        raise AssertionError("L'inspection ne doit pas appeler Garmin")

    def get_workouts(self, start, limit):
        self.workout_calls += 1
        raise AssertionError("L'inspection ne doit pas appeler Garmin")


RUNS = [
    {"activityType": {"typeKey": "running"}, "activityName": "Easy endurance", "distance": 5000},
    {"activityType": {"typeKey": "running"}, "activityName": "Tempo threshold", "distance": 10000},
]


def local_history(monkeypatch, activities) -> None:
    monkeypatch.setattr(
        onboarding, "iter_local_activity_history", lambda: iter(activities)
    )


def test_first_connection_inspects_local_history_without_network(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(onboarding, "ONBOARDING_FILE", tmp_path / "onboarding.json")
    monkeypatch.setattr(onboarding, "ensure_directories", lambda: None)
    local_history(monkeypatch, RUNS)
    client = FakeGarmin()

    state = onboarding.inspect_garmin_account(client)
    again = onboarding.inspect_garmin_account(client)

    assert state["status"] == "needs_profile"
    assert state["publication_allowed"] is False
    assert state["garmin_summary"]["courses_trouvees"] == 2
    assert state["garmin_summary"]["distance_moyenne_km"] == 7.5
    assert state["garmin_summary"]["types_detectes"] == ["endurance", "tempo"]
    assert again == state
    assert client.activity_calls == 0
    assert client.workout_calls == 0


def test_profile_then_plan_confirmation_controls_publication(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(onboarding, "ONBOARDING_FILE", tmp_path / "onboarding.json")
    monkeypatch.setattr(onboarding, "ensure_directories", lambda: None)
    onboarding.inspect_garmin_account(FakeGarmin())

    ready = onboarding.mark_profile_ready()
    proposed = onboarding.mark_plan_proposed("C:/draft-plan.json")
    rejected = onboarding.mark_plan_rejected("Trop intense")
    onboarding.mark_plan_proposed("C:/draft-plan.json")
    complete = onboarding.mark_complete()

    assert ready["status"] == "ready"
    assert ready["publication_allowed"] is False
    assert proposed["status"] == "plan_proposed"
    assert proposed["publication_allowed"] is False
    assert rejected["status"] == "ready"
    assert rejected["last_plan_feedback"] == "Trop intense"
    assert complete["status"] == "complete"
    assert complete["publication_allowed"] is True


def test_rejecting_generated_plan_preserves_dashboard_revision_state(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(onboarding, "ONBOARDING_FILE", tmp_path / "onboarding.json")
    monkeypatch.setattr(onboarding, "ensure_directories", lambda: None)
    onboarding.mark_profile_ready()
    onboarding.update_garmin_setup("plan_proposed")
    onboarding.mark_plan_proposed("C:/draft-plan.json")

    rejected = onboarding.mark_plan_rejected("Plus léger")

    assert rejected["garmin_setup"]["status"] == "plan_rejected"
    assert rejected["publication_allowed"] is False


def test_forced_inspection_preserves_completed_questionnaire(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(onboarding, "ONBOARDING_FILE", tmp_path / "onboarding.json")
    monkeypatch.setattr(onboarding, "ensure_directories", lambda: None)
    onboarding.mark_profile_ready()

    state = onboarding.inspect_garmin_account(
        FakeGarmin(), force=True, preserve_status=True
    )

    assert state["status"] == "ready"
    assert "garmin_summary" in state
    assert all("contraintes" not in answer for answer in state["required_answers"])


def test_onboarding_prompt_mentions_detected_structure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(onboarding, "ONBOARDING_FILE", tmp_path / "onboarding.json")
    monkeypatch.setattr(onboarding, "ensure_directories", lambda: None)
    local_history(monkeypatch, RUNS)
    state = onboarding.inspect_garmin_account(FakeGarmin())

    prompt = onboarding.onboarding_prompt(state)

    assert "2 course(s)" in prompt
    assert "endurance" in prompt
    assert "Quel objectif" in prompt


def test_onboarding_detects_multisport_regularity_and_intervals(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(onboarding, "ONBOARDING_FILE", tmp_path / "onboarding.json")
    monkeypatch.setattr(onboarding, "ensure_directories", lambda: None)

    activities = [
        {"activityType": {"typeKey": "running"}, "activityName": "Easy run", "startTimeLocal": "2026-07-01T08:00:00"},
        {"activityType": {"typeKey": "lap_swimming"}, "activityName": "Pool swim", "startTimeLocal": "2026-07-08T08:00:00"},
        {"activityType": {"typeKey": "running"}, "activityName": "6 x 400 interval", "startTimeLocal": "2026-07-15T08:00:00"},
        {"activityType": {"typeKey": "cycling"}, "activityName": "Bike", "startTimeLocal": "2026-07-22T08:00:00"},
    ]
    local_history(monkeypatch, activities)

    state = onboarding.inspect_garmin_account(FakeGarmin())
    summary = state["garmin_summary"]

    assert summary["sports_detectes"] == {"course": 2, "natation": 1, "vélo": 1}
    assert summary["regularite_detectee"] is True
    assert "intervalles" in summary["types_detectes"]
    assert "natation" in onboarding.onboarding_prompt(state)


def test_onboarding_translates_other_and_recognizes_garmin_abbreviations(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(onboarding, "ONBOARDING_FILE", tmp_path / "onboarding.json")
    monkeypatch.setattr(onboarding, "ensure_directories", lambda: None)

    activities = [
        {"activityType": {"typeKey": "running"}, "activityName": "GC 1908 INT"},
        {"activityType": {"typeKey": "running"}, "activityName": "GC 2308 SL"},
        {"activityType": {"typeKey": "running"}, "activityName": "GC 1708 REC"},
        {"activityType": {"typeKey": "running"}, "activityName": "GC 0608 EF"},
        {"activityType": {"typeKey": "other"}, "activityName": "Other"},
    ]
    local_history(monkeypatch, activities)

    summary = onboarding.inspect_garmin_account(FakeGarmin())["garmin_summary"]

    assert "other" not in summary["sports_detectes"]
    assert summary["sports_detectes"]["autres activités"] == 1
    assert summary["types_entrainement_detectes"] == {
        "intervalles": 1,
        "sortie longue": 1,
        "récupération": 1,
        "endurance": 1,
    }
