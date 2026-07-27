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
