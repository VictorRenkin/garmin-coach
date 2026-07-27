import json

from garmin_coach import activity_data
from garmin_coach.activity_data import fetch_activity_bundle


class FakeDetailsClient:
    def get_activity_details(self, activity_id, maxchart, maxpoly):
        return {
            "metricDescriptors": [
                {"metricsIndex": 0, "key": "directHeartRate"},
                {"metricsIndex": 1, "key": "directElevation"},
                {"metricsIndex": 2, "key": "directLatitude"},
                {"metricsIndex": 3, "key": "directDoubleCadence"},
            ],
            "activityDetailMetrics": [{"metrics": [142, 85.2, 50.6, 158]}],
        }

    def get_activity_splits(self, activity_id):
        return {
            "lapDTOs": [
                {
                    "distance": 1000,
                    "strideLength": 109.2,
                    "startLatitude": 50.6,
                    "averageHR": 142,
                }
            ]
        }

    def get_activity_hr_in_timezones(self, activity_id):
        return [{"zoneNumber": 2, "zoneLowBoundary": 121, "secsInZone": 600}]

    def get_activity_weather(self, activity_id):
        return {"temp": 22, "latitude": 50.6, "relativeHumidity": 60}


def test_detailed_bundle_keeps_sport_data_and_removes_gps() -> None:
    bundle = fetch_activity_bundle(FakeDetailsClient(), {"activityId": 42})

    assert bundle["samples"][0]["directHeartRate"] == 142
    assert bundle["samples"][0]["directDoubleCadence"] == 158
    assert "directLatitude" not in bundle["samples"][0]
    assert bundle["laps"][0]["strideLength"] == 109.2
    assert "startLatitude" not in bundle["laps"][0]
    assert "latitude" not in bundle["weather"]


def test_public_detail_cleans_location_from_legacy_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(activity_data, "ACTIVITY_DETAILS_DIR", tmp_path)
    (tmp_path / "42.json").write_text(
        json.dumps(
            {
                "samples": [{"directHeartRate": 142, "directLatitude": 50.6}],
                "laps": [{"distance": 1000, "startLongitude": 5.1}],
                "weather": {"temp": 20, "latitude": 50.6},
            }
        ),
        encoding="utf-8",
    )

    detail = activity_data.public_detail("42")

    assert detail is not None
    serialized = json.dumps(detail).lower()
    assert "latitude" not in serialized
    assert "longitude" not in serialized
