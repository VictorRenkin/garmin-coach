from garmin_coach.summary import build_summary, is_running


ACTIVITY = {
    "activityId": 123,
    "activityName": "Course du soir",
    "activityType": {"typeKey": "running"},
    "startTimeLocal": "2026-07-21 18:30:00",
    "distance": 10000,
    "movingDuration": 3000,
    "averageHR": 155,
    "maxHR": 180,
    "elevationGain": 80,
    "calories": 650,
    "aerobicTrainingEffect": 3.4,
}


def test_running_activity_is_detected() -> None:
    assert is_running(ACTIVITY)


def test_summary_contains_main_metrics() -> None:
    report = build_summary(ACTIVITY)
    assert "10.00 km" in report
    assert "50 min 00 s" in report
    assert "5:00/km" in report
    assert "155 bpm" in report
    assert "Séance productive" in report
